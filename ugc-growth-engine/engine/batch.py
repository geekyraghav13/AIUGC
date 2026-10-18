"""ITERATIONS — 10 scripts at a time, approved by the owner, then produced one video at a time.

    research (cached weekly)
      → build(): language mix + video-mode mix from THIS app's research, split across 10 slots
                 → 10 concepts → 1 hook each (in its language) → 10 full scripts → safety check → look per video
      → dashboard: the owner reads all 10 scripts, approves / rejects / edits each, sees the Magnific estimate
      → approve(): approved scripts + a hard credit ceiling (estimate + margin)
      → process(): one video after another through the crew — director → assets (stock first, Magnific when stock
                   doesn't match or the scene needs an AI shot/character) → editor → review → QC → owner
Token-light: about 10 Gemini calls to write the 10 scripts; per video 1 director call + 1 review call per version.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import select

from engine import compliance, concepts as concepts_mod, modes, progress as prg, scripts, tracking
from engine.brief import brief_of
from engine.db import Batch, Creative, current_generation, get_app, save, session
from engine.locks import app_lock
from engine.settings import creator_styles, languages, log

SIZE = 10
MARGIN = 0.0           # approved ceiling = estimate exactly (no cushion; the owner approves an exact credit total)


# ---------------------------------------------------------------- mixes

def split(shares: dict[str, float], n: int) -> dict[str, int]:
    """Largest-remainder split of n slots by shares (values that round to 0 get none)."""
    total = sum(v for v in shares.values() if v > 0) or 1.0
    raw = {k: n * v / total for k, v in shares.items() if v > 0}
    out = {k: int(math.floor(v)) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: n - sum(out.values())]:
        out[k] += 1
    return {k: v for k, v in out.items() if v}


def language_shares(app_id: str) -> tuple[dict[str, float], str]:
    """Which languages over-perform in this app's market (any supported language, not only the brief's)."""
    from engine.research.market import context

    ctx = context(app_id)
    app = get_app(app_id)
    b = brief_of(app) if app is not None else None
    # opt-in (brief.languages_strict): the brief's languages are a hard limit, e.g. English only
    allowed = [l for l in b.languages if l in languages()] if (b is not None and b.languages_strict) else []
    rows = ctx.rows("language") if ctx.available else None
    if rows is not None and not rows.empty:
        shares = {str(r.value): float(r.weighted_share) for r in rows.itertuples()
                  if str(r.value) in languages() and (not allowed or str(r.value) in allowed)}
        if sum(shares.values()) > 0:
            return shares, "market research"
    langs = allowed or [l for l in (b.languages if b is not None else []) if l in languages()] or ["hinglish"]
    return {l: 1.0 for l in langs}, "brief languages (no language signal in the research)"


def plan_slots(app_id: str, n: int = SIZE) -> tuple[list[dict[str, str]], dict[str, Any]]:
    lang_sh, lang_src = language_shares(app_id)
    mode_sh, mode_src = modes.planned_shares(app_id)
    lang_n, mode_n = split(lang_sh, n), split(mode_sh, n)
    b = brief_of(get_app(app_id))
    if b.mode_mix and sum(b.mode_mix.values()) == n:                  # the owner fixed the mix (e.g. 4 UGC + 4 ads)
        mode_n, mode_src = dict(b.mode_mix), "owner's mode mix (brief)"
    langs = [l for l, k in sorted(lang_n.items(), key=lambda x: -x[1]) for _ in range(k)]
    mds = [m for m, k in sorted(mode_n.items(), key=lambda x: -x[1]) for _ in range(k)]
    # interleave so each language gets a spread of modes
    slots = [{"language": langs[i], "visual_mode": mds[(i * 3) % len(mds)] if mds else "faceless"} for i in range(n)]
    mix = {"languages": lang_n, "language_source": lang_src, "modes": mode_n, "mode_source": mode_src}
    return slots, mix


def style_for(mode: str, brand_tone: str, i: int) -> str:
    from engine.variations import default_styles

    ok = [s for s, v in creator_styles().items() if mode in (v.get("modes") or [])]
    pref = [s for s in default_styles(brand_tone) if s in ok] or ok or ["bestie_narrator"]
    return pref[i % len(pref)]


# ---------------------------------------------------------------- build

def build(app_id: str, n: int = SIZE, track: bool = True) -> Batch:
    """Write the next iteration's n scripts (nothing is filmed until the owner approves them)."""
    app = get_app(app_id)
    if app is None:
        raise ValueError(f"unknown app {app_id}")
    b = brief_of(app)
    from contextlib import nullcontext

    tracker = prg.tracked(app_id, f"Next {n} scripts", ["concepts", "hooks", "scripts", "safety"]) if track \
        else nullcontext()           # track=False: a caller (the dashboard's Go) owns the progress bar
    with app_lock(app_id, "batch scripts"), tracker:
        from engine.db import latest_understanding
        from engine import understand

        if latest_understanding(app_id) is None:
            understand.run(app_id)
        gen = current_generation(app_id) + 1
        slots, mix = plan_slots(app_id, n)
        batch = Batch(app_id=app_id, generation=gen, status="building", mix=mix)
        save(batch)
        prg.step(app_id, "concepts", "running", f"{n} ideas from the research playbook…")
        cps = concepts_mod.run(app_id, n=n, generation=gen)
        prg.step(app_id, "concepts", "done", f"{len(cps)} ideas")
        rows: list[Creative] = []
        prg.step(app_id, "hooks", "running", "One opening line per idea, in its language…")
        by_lang: dict[str, list[int]] = {}
        for i, s in enumerate(slots[: len(cps)]):
            by_lang.setdefault(s["language"], []).append(i)
        for lang, idx in by_lang.items():
            hooks = scripts.generate_hooks(app, [cps[i] for i in idx], lang, 1)
            first = {}
            for h in hooks:
                first.setdefault(h["concept_id"], h)
            for i in idx:
                c, s = cps[i], slots[i]
                h = first.get(c.id)
                if not h:
                    continue
                rows.append(Creative(app_id=app_id, concept_id=c.id, generation=gen, language=lang, hook=h["hook"],
                                     hook_type=h["hook_type"], format=c.format, persona=c.persona, message=c.message,
                                     creator_style=style_for(s["visual_mode"], b.brand_tone, i),
                                     visual_mode=s["visual_mode"], status="hook_only", origin="explore",
                                     market_evidence=c.market_evidence or []))
        save(*rows)
        prg.step(app_id, "hooks", "done", f"{len(rows)} hooks")
        prg.step(app_id, "scripts", "running", f"Full scripts for {len(rows)} videos…")
        st = scripts.write_scripts(app_id, rows)
        prg.step(app_id, "scripts", "done", f"{st['ok']} scripts" + (f" · {st['failed']} failed" if st["failed"] else ""))
        ok = [c for c in rows if c.status == "draft"]
        prg.step(app_id, "safety", "running", "Safety check…")
        cs = compliance.check_creatives(ok, b, use_llm=True)
        tracking.assign(app_id, ok)
        from engine.variations import assign_looks

        assign_looks(app_id, gen, ok)
        prg.step(app_id, "safety", "done", f"{cs['pass']} passed · {cs['flagged']} flagged")
        batch.creative_ids = [c.id for c in ok]
        batch.credits_estimate = sum(modes.mode_cost(c.visual_mode) for c in ok)
        batch.status = "scripts_ready"
        batch.notes = [f"{len(ok)} scripts ready for approval"]
        save(batch)
        log.info(f"batch {batch.id} for {app_id}: {len(ok)} scripts · {mix}")
        return batch


# ---------------------------------------------------------------- owner decisions

def get(batch_id: int) -> Batch:
    with session() as s:
        b = s.get(Batch, batch_id)
    if b is None:
        raise ValueError(f"no batch {batch_id}")
    return b


def latest(app_id: str) -> Optional[Batch]:
    with session() as s:
        return s.exec(select(Batch).where(Batch.app_id == app_id).order_by(Batch.id.desc())).first()


def creatives_of(batch: Batch) -> list[Creative]:
    with session() as s:
        rows = [s.get(Creative, i) for i in batch.creative_ids]
    return [r for r in rows if r is not None]


def decide(creative_id: int, approve: bool) -> None:
    """Owner approves or rejects one script."""
    with session() as s:
        c = s.get(Creative, creative_id)
        c.status = "approved" if approve else "rejected"
        c.selected_for_render = approve
        if approve and c.compliance_status == "flagged":
            c.compliance_status = "approved"   # the owner read it and accepted it
        s.add(c)
        s.commit()


def edit_scene(creative_id: int, scene: int, voiceover: Optional[str] = None, on_screen_text: Optional[str] = None) -> None:
    import copy

    with session() as s:
        c = s.get(Creative, creative_id)
        sd = copy.deepcopy(c.script)
        if voiceover is not None:
            sd["scenes"][scene]["voiceover"] = voiceover
        if on_screen_text is not None:
            sd["scenes"][scene]["on_screen_text"] = on_screen_text
        c.script = sd
        if scene == 0 and voiceover:
            c.hook = voiceover
        s.add(c)
        s.commit()


def approve(batch_id: int) -> Batch:
    """Lock the approved scripts and the credit ceiling. Undecided scripts count as approved."""
    b = get(batch_id)
    for c in creatives_of(b):
        if c.status not in ("approved", "rejected"):
            decide(c.id, True)
    chosen = [c for c in creatives_of(b) if c.status == "approved"]
    b.credits_estimate = sum(modes.mode_cost(c.visual_mode) for c in chosen)
    b.credits_approved = int(round(b.credits_estimate * (1 + MARGIN)))
    b.status, b.approved_at = "approved", datetime.now(timezone.utc)
    b.notes = list(b.notes or []) + [f"approved {len(chosen)} scripts · up to {b.credits_approved} Magnific credits"]
    save(b)
    return b


# ---------------------------------------------------------------- production (one by one)

def process(batch_id: int) -> Batch:
    """Produce the approved scripts one video at a time through the crew. Magnific jobs run through the API when
    MAGNIFIC_API_KEY is set (never past the approved ceiling); without it a video waits at its job sheet."""
    from engine import magnific
    from engine.production import assets, pipeline
    from engine.production.common import latest_run

    b = get(batch_id)
    if b.status not in ("approved", "processing", "stopped", "done") or not b.approved_at:
        raise ValueError(f"batch {batch_id} is {b.status}: approve its scripts first")
    # re-running a batch picks up every approved video that isn't finished yet (e.g. it waited for a Magnific key)
    todo = [c for c in creatives_of(b) if c.status == "approved"
            and not ((r := latest_run(c.id)) and r.status in ("awaiting_owner", "approved"))]
    steps = [f"video:{c.id}" for c in todo]
    with app_lock(b.app_id, "batch production"), prg.tracked(b.app_id, f"Batch #{b.id} · {len(todo)} videos", steps):
        b.status = "processing"
        save(b)
        for c in todo:
            prg.step(b.app_id, f"video:{c.id}", "running", f"{c.visual_mode.replace('_', ' ')} · {c.language} · "
                                                            f"“{c.hook[:50]}”")
            b.current_creative = c.id
            save(b)
            try:
                run = latest_run(c.id)
                if run is None or run.status in ("failed", "rejected"):
                    run = pipeline.start(c.id, track=False)
                # a revision round can order new assets again → keep fulfilling (bounded) until the video settles
                for _ in range(4):
                    if run.status != "waiting_assets" or not magnific.enabled():
                        break
                    if not any(r["kind"] != "voice" for r in assets.pending(run)):
                        break
                    left = b.credits_approved - b.credits_spent
                    res = assets.fulfil_api(run, credit_limit=left)
                    b.credits_spent += res["spent"]
                    save(b)
                    if res["stopped"]:
                        b.status = "stopped"
                        b.notes = list(b.notes) + [f"stopped at #{c.id}: approved credits used up"]
                        save(b)
                        prg.step(b.app_id, f"video:{c.id}", "failed", "Approved Magnific credits used up")
                        return b
                    run = pipeline.resume(run.id, track=False)
                if run.status == "waiting_assets" and not assets.pending(run):
                    run = pipeline.resume(run.id, track=False)   # nothing left to order (e.g. fixes cancelled)
                if run.status == "waiting_assets" and any(r["kind"] == "voice" for r in assets.pending(run)):
                    prg.step(b.app_id, f"video:{c.id}", "failed", "Waiting for voice lines — ask Claude to "
                                                                  f"“record the voices for batch {b.id}”")
                    continue
                state = run.status.replace("_", " ")
                prg.step(b.app_id, f"video:{c.id}", "done" if run.status in ("awaiting_owner", "approved") else "failed",
                         f"{state} · v{run.version} · QC {run.qc_score or 0:.0f}")
            except Exception as e:  # noqa: BLE001 — one video failing never stops the batch
                log.exception(f"batch {b.id}: creative {c.id} failed")
                prg.step(b.app_id, f"video:{c.id}", "failed", str(e)[:200])
        b.status, b.current_creative = "done", None
        save(b)
    return b


def summary(b: Batch) -> dict[str, Any]:
    cs = creatives_of(b)
    return {"batch": b.id, "app": b.app_id, "status": b.status, "scripts": len(cs),
            "approved": sum(1 for c in cs if c.status in ("approved", "rendered")),
            "rejected": sum(1 for c in cs if c.status == "rejected"), "mix": b.mix,
            "credits_estimate": b.credits_estimate, "credits_approved": b.credits_approved,
            "credits_spent": max(b.credits_spent, imported_credits(b))}


def imported_credits(b: Batch) -> int:
    """Magnific credits actually imported for this app since the batch was created (the ledger is the source of
    truth; `b.credits_spent` only counts jobs the engine ran itself, not ones imported by hand through the MCP)."""
    from sqlmodel import select

    from engine.db import Ledger, session

    with session() as s:
        rows = s.exec(select(Ledger).where(Ledger.app_id == b.app_id, Ledger.provider == "magnific",
                                           Ledger.step.startswith("magnific:"), Ledger.created_at >= b.created_at)).all()
    return int(sum(r.units for r in rows))
