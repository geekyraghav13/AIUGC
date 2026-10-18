"""Variation engine: concepts × hooks × languages × creator styles → scored, deduped, stratified selection."""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, Sequence

from engine.localize import words
from engine import progress as prg
from engine.locks import locked
from engine import compliance, concepts as concepts_mod, scripts, tracking
from engine.brief import brief_of
from sqlmodel import update

from engine.db import Concept, Creative, creatives, get_app, save, session
from engine.settings import creator_styles, env_int, languages as lang_cfg, log

DEFAULT_STYLES = {
    "desi-casual": ["bestie_narrator", "meme_text"],
    "fun": ["bestie_narrator", "meme_text"],
    "trustworthy": ["explainer_didi_bhaiya", "screen_demo"],
    "premium": ["storytime", "screen_demo"],
}


def tokens(text: str) -> set[str]:
    return set(words(text))


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def dedupe(items: Sequence[dict[str, Any]], key: Callable[[dict], str] = lambda h: h["hook"],
           group: Callable[[dict], Any] = lambda h: (h.get("language"),), threshold: float = 0.7) -> list[dict]:
    """Drop near-identical texts (token Jaccard ≥ threshold) within the same group."""
    kept: list[dict] = []
    for it in items:
        if any(group(k) == group(it) and similarity(key(k), key(it)) >= threshold for k in kept):
            continue
        kept.append(it)
    return kept


def select_stratified(items: Sequence[Any], n: int, dims: Iterable[str], score: Callable[[Any], float]) -> list[Any]:
    """Greedy pick maximizing new tag-value coverage first, then score (not just top scores)."""
    dims = list(dims)
    pool = sorted(items, key=score, reverse=True)
    chosen: list[Any] = []
    seen: dict[str, dict[Any, int]] = {d: {} for d in dims}
    while pool and len(chosen) < n:
        def gain(it: Any) -> float:
            g = 0.0
            for d in dims:
                cnt = seen[d].get(_get(it, d), 0)
                g += 1.0 / (1 + cnt)  # diminishing returns per value
            return g * 100 + score(it)
        best = max(pool, key=gain)
        pool.remove(best)
        chosen.append(best)
        for d in dims:
            v = _get(best, d)
            seen[d][v] = seen[d].get(v, 0) + 1
    return chosen


def _get(it: Any, d: str) -> Any:
    return it.get(d) if isinstance(it, dict) else getattr(it, d)


def default_styles(brand_tone: str) -> list[str]:
    return DEFAULT_STYLES.get(brand_tone, ["bestie_narrator", "explainer_didi_bhaiya"])


def build_hook_creatives(app_id: str, concept_rows: list[Concept], hooks: list[dict], styles: list[str],
                         generation: int, origin: str = "explore",
                         parent_id: Optional[int] = None) -> list[Creative]:
    from engine.research.market import context

    ctx = context(app_id)
    by_id = {c.id: c for c in concept_rows}
    rows = []
    for h in hooks:
        c = by_id.get(h["concept_id"])
        if c is None:
            continue
        for st in styles:
            tags = {"format": c.format, "hook_type": h["hook_type"], "language": h["language"], "creator_style": st}
            heur = scripts.heuristic_score(h["hook"])
            rows.append(Creative(
                app_id=app_id, concept_id=c.id, generation=generation, parent_creative_id=parent_id,
                language=h["language"], hook=h["hook"], hook_type=h["hook_type"], format=c.format,
                creator_style=st, persona=c.persona, message=c.message,
                predicted_score=round(0.6 * heur + 0.4 * ctx.prior_score(tags), 1) if ctx.available else heur,
                status="hook_only", origin=origin,
                market_evidence=sorted(set(c.market_evidence or []) | set(ctx.evidence_ids(tags))) if ctx.available else [],
            ))
    from engine import modes

    plan = modes.assign(app_id, rows, seed=generation)
    log.info(f"visual modes for {app_id} gen {generation} ({plan['source']}): {plan['assigned']}")
    return rows


@locked("generation 1")
def run_generation_one(app_id: str, languages: Optional[list[str]] = None, styles: Optional[list[str]] = None,
                       n_concepts: int = 10, hooks_per_concept: int = 5, scripted: Optional[int] = None,
                       render_count: Optional[int] = None, use_llm_compliance: bool = True) -> dict[str, Any]:
    app = get_app(app_id)
    if app is None:
        raise ValueError(f"unknown app {app_id}")
    b = brief_of(app)
    langs = [l for l in (languages or b.languages) if l in lang_cfg()]
    styles = [s for s in (styles or default_styles(b.brand_tone)) if s in creator_styles()]
    scripted = scripted or env_int("SCRIPTED_COUNT", 60)
    render_count = render_count or env_int("GEN1_RENDER_COUNT", 12)

    # 1. concepts
    prg.step(app_id, "concepts", "running", f"Writing {n_concepts} video ideas from the brief + market playbook…")
    concept_rows = concepts_mod.run(app_id, n=n_concepts, generation=1)
    n_ev = sum(1 for c in concept_rows if c.market_evidence)
    prg.step(app_id, "concepts", "done", f"{len(concept_rows)} ideas · {n_ev} built on proven market patterns")
    # 2. hooks for every concept × language (batched LLM calls)
    prg.step(app_id, "hooks", "running", f"Opening lines in {', '.join(langs)}…")
    hooks: list[dict] = []
    for lang in langs:
        prg.detail(app_id, f"Writing hooks in {lang_cfg()[lang]['name']}…")
        hooks += scripts.generate_hooks(app, concept_rows, lang, hooks_per_concept)
    raw_hooks = len(hooks)
    # 3. dedupe near-identical hooks
    hooks = dedupe(hooks, group=lambda h: (h["language"], h["concept_id"]))
    # 4. full matrix of hook-level creatives (tagged), heuristic pre-score
    matrix = build_hook_creatives(app_id, concept_rows, hooks, styles, generation=1)
    save(*matrix)
    potential = len(concept_rows) * hooks_per_concept * len(langs) * len(styles)
    prg.step(app_id, "hooks", "done", f"{len(hooks)} hooks ({raw_hooks - len(hooks)} near-duplicates removed) → "
                                      f"{len(matrix)} of {potential} possible creatives")
    # 5. scripts for a stratified subset
    to_script = select_stratified(matrix, scripted, ["language", "creator_style", "hook_type", "format", "message"],
                                  score=lambda c: c.predicted_score)
    prg.step(app_id, "scripts", "running", f"Full scene-by-scene scripts for the best {len(to_script)}…")
    sstats = scripts.write_scripts(app_id, to_script)
    ok = [c for c in to_script if c.status == "draft"]
    prg.step(app_id, "scripts", "done", f"{sstats['ok']} scripts" + (f" · {sstats['failed']} failed validation"
                                                                     if sstats["failed"] else ""))
    # 6. compliance
    prg.step(app_id, "safety", "running", "Rules + AI review: testimonials, fake stats, guarantees, copied hooks…")
    cstats = compliance.check_creatives(ok, b, use_llm=use_llm_compliance)
    prg.step(app_id, "safety", "done", f"{cstats['pass']} passed · {cstats['flagged']} flagged for your review")
    # 7. tracking links
    tracking.assign(app_id, ok)
    # 8. stratified render selection among publishable creatives
    prg.step(app_id, "pick", "running", f"Choosing {render_count} to film (cover languages, styles, hook types)…")
    chosen = select_for_render(app_id, 1, render_count)
    prg.step(app_id, "pick", "done", f"{len(chosen)} ideas selected · "
                                     f"{len({c.language for c in chosen})} languages · {len({c.hook_type for c in chosen})} hook types")
    stats = {
        "app_id": app_id, "generation": 1, "languages": langs, "styles": styles,
        "concepts": len(concept_rows), "hooks_generated": raw_hooks, "hooks_after_dedupe": len(hooks),
        "matrix_potential": potential, "matrix_rows": len(matrix), "scripted": sstats["ok"],
        "script_failures": sstats["failed"], "compliance": cstats, "selected_for_render": len(chosen),
    }
    log.info(f"Gen 1 variation stats: {stats}")
    return stats


def resume_generation_one(app_id: str, render_count: Optional[int] = None, use_llm_compliance: bool = True) -> bool:
    """Finish an interrupted Gen 1 (scripts written but safety check / picks missing) without regenerating."""
    rows = creatives(app_id, 1)
    drafts = [c for c in rows if c.script and c.status == "draft" and c.compliance_status == "unchecked"]
    if not drafts and any(c.selected_for_render for c in rows):
        return False
    render_count = render_count or env_int("GEN1_RENDER_COUNT", 12)
    for k in ("concepts", "hooks", "scripts"):
        prg.step(app_id, k, "done", "Already done (resumed)")
    if drafts:
        prg.step(app_id, "safety", "running", f"Checking {len(drafts)} scripts that were never checked…")
        cstats = compliance.check_creatives(drafts, brief_of(get_app(app_id)), use_llm=use_llm_compliance)
        tracking.assign(app_id, drafts)
        prg.step(app_id, "safety", "done", f"{cstats['pass']} passed · {cstats['flagged']} flagged for your review")
    prg.step(app_id, "pick", "running", f"Choosing {render_count} to film…")
    chosen = select_for_render(app_id, 1, render_count)
    prg.step(app_id, "pick", "done", f"{len(chosen)} ideas selected")
    return True


def assign_looks(app_id: str, generation: int, chosen: list[Creative]) -> None:
    """Every video picked for production gets its own text look, different from every other video of this app's
    generation (fonts, caption layout, headline + hook style, size, animation)."""
    from engine import captions

    ids = {c.id for c in chosen}
    taken = {c.style_signature.split("|", 1)[-1] for c in creatives(app_id, generation)
             if c.style_signature and c.id not in ids and (c.selected_for_render or c.status in ("rendered", "published"))}
    captions.assign_styles(chosen, taken)
    with session() as s:
        for c in chosen:
            s.exec(update(Creative).where(Creative.id == c.id).values(style_signature=c.style_signature))
        s.commit()


def select_for_render(app_id: str, generation: int, n: int) -> list[Creative]:
    pool = [c for c in creatives(app_id, generation) if c.script and c.status in ("draft", "approved")
            and compliance.is_publishable(c)]
    chosen = select_stratified(pool, n, ["language", "creator_style", "hook_type", "format", "visual_mode"],
                               score=lambda c: c.predicted_score)
    from engine import modes
    from engine.production.assets import budget_left

    moved = modes.fit_budget(chosen, budget_left())
    if moved:
        with session() as s:
            for c in chosen:
                s.exec(update(Creative).where(Creative.id == c.id).values(visual_mode=c.visual_mode))
            s.commit()
    assign_looks(app_id, generation, chosen)
    ids = [c.id for c in chosen]
    # Write the selection with UPDATE statements: in-memory objects may already say selected=True, and
    # re-saving an unchanged attribute is a no-op (that silently dropped picks before).
    with session() as s:
        s.exec(update(Creative).where(Creative.app_id == app_id, Creative.generation == generation,
                                      Creative.status.in_(["draft", "approved", "failed"]))
               .values(selected_for_render=False))
        if ids:
            s.exec(update(Creative).where(Creative.id.in_(ids)).values(selected_for_render=True))
            s.exec(update(Creative).where(Creative.id.in_(ids), Creative.status == "draft").values(status="approved"))
        s.commit()
    for c in chosen:
        c.selected_for_render = True
        if c.status == "draft":
            c.status = "approved"
    return chosen


def matrix_counts(app_id: str, generation: Optional[int] = None) -> dict[str, int]:
    rows = creatives(app_id, generation)
    return {
        "matrix": len(rows),
        "scripted": sum(1 for c in rows if c.script),
        "selected": sum(1 for c in rows if c.selected_for_render),
        "rendered": sum(1 for c in rows if c.status in ("rendered", "published")),
        "flagged": sum(1 for c in rows if c.compliance_status == "flagged"),
    }
