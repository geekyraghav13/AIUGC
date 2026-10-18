"""Hooks (batched) and full scripts: concept × hook × language × creator style → script JSON."""
from __future__ import annotations

import re
from pathlib import Path
from collections import defaultdict
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator
from sqlmodel import select

from engine import llm, mock_content
from engine.brief import brief_for_prompt, brief_of
from engine.concepts import message_text, understanding_for_prompt
from engine.db import App, Concept, Creative, get_app, latest_understanding, save, session
from engine.localize import check_language, language_block, script_texts
from engine.settings import creator_styles, formats, languages, log, taxonomy

HOOK_BATCH = 5     # concepts per hook call
SCRIPT_BATCH = 3   # scripts per script call
MAX_HOOK_WORDS = 12  # prompt asks for ≤ 10; allow a little slack, reject run-on hooks


# ---------------------------------------------------------------- schemas

class HookOut(BaseModel):
    concept_id: int
    hook: str
    hook_type: str = ""


class HooksOut(BaseModel):
    hooks: list[HookOut] = Field(default_factory=list)


class Visual(BaseModel):
    type: str = "stock"
    query_en: str = ""
    screenshot_index: int = 0

    @field_validator("type")
    @classmethod
    def _t(cls, v: str) -> str:
        return v if v in ("stock", "screenshot") else "stock"


class Scene(BaseModel):
    voiceover: str
    on_screen_text: str = ""
    visual: Visual = Field(default_factory=Visual)
    est_seconds: float = 4

    @field_validator("est_seconds")
    @classmethod
    def _secs(cls, v: float) -> float:
        return float(min(8, max(1.5, v)))


class ScriptOut(BaseModel):
    job_id: int = 0
    hook: str
    hook_type: str = ""
    format: str = ""
    language: str = ""
    creator_style: str = ""
    scenes: list[Scene] = Field(min_length=3)
    cta: str = ""
    cta_type: str = "download"
    post_caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    predicted_score: float = 50
    score_reason: str = ""

    @field_validator("cta_type")
    @classmethod
    def _cta(cls, v: str) -> str:
        return v if v in taxonomy()["cta_types"] else "download"


class ScriptsOut(BaseModel):
    scripts: list[ScriptOut] = Field(default_factory=list)


# ---------------------------------------------------------------- helpers

def length_bucket(seconds: float) -> str:
    for name, (lo, hi) in taxonomy()["length_buckets"].items():
        if lo <= seconds < hi:
            return name
    return "long"


PAIN_WORDS = re.compile(
    r"tension|problem|pareshan|dikkat|month[- ]end|salary|exam|stress|bore|late|kharcha|kharche|"
    r"टेंशन|परेशान|दिक्कत|खर्च|सैलरी|டென்ஷன்|செலவு|சம்பளம்|టెన్షన్|ఖర్చు|జీతం|टेन्शन|खर्च|पगार|টেনশন|খরচ|মাইনে",
    re.I,
)


def heuristic_score(hook: str) -> float:
    """0–100 prior on hook strength: short, has a number or question, names a pain."""
    words = len(hook.split())
    s = 50.0
    s += 12 if 4 <= words <= 9 else (-8 if words > 12 else 0)
    s += 10 if re.search(r"\d|तीन|மூணு|మూడు|তিনটে|three|teen", hook, re.I) else 0
    s += 10 if "?" in hook else 0
    s += 12 if PAIN_WORDS.search(hook) else 0
    return max(0.0, min(100.0, s))


def combined_score(llm_score: float, hook: str, market_prior: Optional[float] = None) -> float:
    """LLM rating + hook heuristic (+ market prior for the creative's tags when research exists)."""
    if market_prior is None:
        return round(0.6 * float(llm_score) + 0.4 * heuristic_score(hook), 1)
    return round(0.5 * float(llm_score) + 0.3 * heuristic_score(hook) + 0.2 * market_prior, 1)


def tags_of(c: Creative) -> dict[str, str]:
    return {"format": c.format, "hook_type": c.hook_type, "language": c.language, "creator_style": c.creator_style}


def with_market(app_id: str, guidance: str) -> tuple[str, Any]:
    """Append the market playbook guidance (recipes + hook templates) and return the market context."""
    from engine.research.market import context

    ctx = context(app_id)
    g = ctx.guidance()
    ref = Path(__file__).resolve().parent.parent / "data" / "reference_styles" / f"{app_id}.md"
    if ref.exists():                  # optional owner-supplied reference video: style/structure to follow, not copy
        g = (g + "\n\n" + ref.read_text()).strip()
    return ((guidance + "\n\n" + g).strip() if g else guidance), ctx


def coerce_hook_type(v: str) -> str:
    types = list(taxonomy()["hook_types"])
    v = (v or "").strip().lower().replace(" ", "_").replace("-", "_")
    return v if v in types else next((t for t in types if t in v or v in t), "question")


# ---------------------------------------------------------------- hooks

def generate_hooks(app: App, concepts: list[Concept], lang: str, k: int, guidance: str = "") -> list[dict[str, Any]]:
    """k hooks per concept in one language, batched HOOK_BATCH concepts per LLM call."""
    b = brief_of(app)
    guidance, _ = with_market(app.id, guidance)
    hook_types = taxonomy()["hook_types"]
    out: list[dict[str, Any]] = []
    for i in range(0, len(concepts), HOOK_BATCH):
        chunk = concepts[i : i + HOOK_BATCH]
        payload = [{"concept_id": c.id, "title": c.title, "persona": c.persona, "pain_point": c.pain_point,
                    "india_scenario": c.india_scenario, "format": c.format} for c in chunk]
        prompt = llm.render_prompt(
            "hooks", app_name=b.app_name, one_liner=b.one_liner, language_block=language_block(lang),
            hook_types=hook_types, concepts=payload, guidance=guidance or "(none)", k=str(k),
            claims_to_avoid=", ".join(b.claims_to_avoid) or "(none)",
        )
        types = list(hook_types)

        def mock() -> dict[str, Any]:
            return {"hooks": [{"concept_id": c.id,
                               "hook": mock_content.hook(lang, types[(j + c.id) % len(types)], b.app_name, j + c.id),
                               "hook_type": types[(j + c.id) % len(types)]}
                              for c in chunk for j in range(k)]}

        try:
            res: HooksOut = llm.complete_json(f"hooks_{lang}", prompt, HooksOut, temperature=1.0,
                                              app_id=app.id, mock_payload=mock)
        except llm.LLMError as e:
            log.error(f"hook generation failed for {lang} batch {i}: {e}")
            continue
        valid_ids = {c.id for c in chunk}
        for h in res.hooks:
            ok, why = check_language(h.hook, lang)
            if h.concept_id not in valid_ids or not h.hook.strip():
                continue
            if len(h.hook.split()) > MAX_HOOK_WORDS:
                log.debug(f"dropped long hook ({len(h.hook.split())} words): {h.hook}")
                continue
            if not ok:
                log.debug(f"dropped hook ({why}): {h.hook}")
                continue
            out.append({"concept_id": h.concept_id, "hook": h.hook.strip(), "hook_type": coerce_hook_type(h.hook_type),
                        "language": lang})
    return out


# ---------------------------------------------------------------- scripts

def _job(c: Creative, concept: Optional[Concept], msg: str) -> dict[str, Any]:
    return {"job_id": c.id, "hook": c.hook, "hook_type": c.hook_type, "format": c.format,
            "language": c.language, "creator_style": c.creator_style, "persona": c.persona,
            "message": msg, "pain_point": concept.pain_point if concept else "",
            "india_scenario": concept.india_scenario if concept else ""}


def _validate_script(s: ScriptOut, c: Creative) -> list[str]:
    problems = []
    ok, why = check_language(script_texts(s.model_dump()), c.language)
    if not ok:
        problems.append(why)
    total = sum(sc.est_seconds for sc in s.scenes)
    if not 15 <= total <= 45:
        problems.append(f"duration {total:.0f}s outside 20–35s")
    return problems


def _apply(c: Creative, s: ScriptOut, ctx: Any = None) -> None:
    sd = s.model_dump()
    # Tags come from the variation matrix, not from the LLM's echo.
    sd.update({"hook_type": c.hook_type, "format": c.format, "language": c.language, "creator_style": c.creator_style})
    n = len(sd["scenes"])
    if n > 8:
        sd["scenes"] = sd["scenes"][:7] + sd["scenes"][-1:]
    c.script = sd
    c.hook = s.scenes[0].voiceover.strip() or c.hook
    c.cta_type = s.cta_type
    c.post_caption = s.post_caption
    c.hashtags = [h if h.startswith("#") else f"#{h}" for h in s.hashtags][:10]
    c.length_bucket = length_bucket(sum(sc["est_seconds"] for sc in sd["scenes"]))
    market = ctx.prior_score(tags_of(c)) if ctx is not None and ctx.available else None
    c.predicted_score = combined_score(s.predicted_score, c.hook, market)
    c.status = "draft"


def write_scripts(app_id: str, creatives: list[Creative], guidance: str = "") -> dict[str, int]:
    """Fill `script` for each creative (batched by language). One failure never stops the batch."""
    app = get_app(app_id)
    u = latest_understanding(app_id)
    if app is None or u is None:
        raise ValueError("run `understand` first")
    b = brief_of(app)
    guidance, ctx = with_market(app_id, guidance)
    with session() as s:
        concepts = {c.id: c for c in s.exec(select(Concept).where(Concept.app_id == app_id)).all()}
    n_screens = max(1, len(app.screenshots or []))
    by_lang: dict[str, list[Creative]] = defaultdict(list)
    for c in creatives:
        by_lang[c.language].append(c)
    stats = {"ok": 0, "failed": 0}
    from engine import progress as prg

    for lang, items in by_lang.items():
        for i in range(0, len(items), SCRIPT_BATCH):
            prg.detail(app_id, f"Scripts {stats['ok'] + stats['failed']}/{len(creatives)} done · writing "
                               f"{languages()[lang]['name']}…")
            chunk = items[i : i + SCRIPT_BATCH]
            results = _script_batch(app, b, u, chunk, concepts, lang, n_screens, guidance)
            for c in chunk:
                s_out = results.get(c.id)
                problems = _validate_script(s_out, c) if s_out else ["no script returned"]
                if problems and s_out is not None:
                    # one targeted retry for this creative alone
                    retry = _script_batch(app, b, u, [c], concepts, lang, n_screens,
                                          guidance + "\nFix these problems: " + "; ".join(problems), attempt=1)
                    s_out = retry.get(c.id)
                    problems = _validate_script(s_out, c) if s_out else ["no script returned"]
                if s_out is not None and not problems:
                    _apply(c, s_out, ctx)
                    stats["ok"] += 1
                else:
                    c.status = "failed"
                    c.compliance_issues = [f"script: {p}" for p in problems]
                    stats["failed"] += 1
                    log.warning(f"creative {c.id} script failed: {problems}")
            save(*chunk)
    return stats


def _script_batch(app: App, b: Any, u: Any, chunk: list[Creative], concepts: dict, lang: str, n_screens: int,
                  guidance: str, attempt: int = 0) -> dict[int, ScriptOut]:
    jobs = [_job(c, concepts.get(c.concept_id), message_text(u, c.message)) for c in chunk]
    styles = {c.creator_style: creator_styles()[c.creator_style]["description"] for c in chunk}
    fmts = {c.format: formats()[c.format]["structure"] for c in chunk}
    prompt = llm.render_prompt(
        "scripts", brief=brief_for_prompt(b), understanding=understanding_for_prompt(u), jobs=jobs,
        language_block=language_block(lang), styles=styles, formats=fmts, guidance=guidance or "(none)",
        cta=b.cta, claims_to_avoid=", ".join(b.claims_to_avoid) or "(none)", max_screen=str(n_screens - 1),
    )

    def mock() -> dict[str, Any]:
        return {"scripts": [dict(mock_content.script(c.language, c.hook, c.hook_type, c.format, c.creator_style,
                                                     b.app_name, seed=c.id, n_screens=n_screens), job_id=c.id)
                            for c in chunk]}

    try:
        res: ScriptsOut = llm.complete_json(f"scripts_{lang}_a{attempt}", prompt, ScriptsOut, temperature=0.9,
                                            app_id=app.id, mock_payload=mock)
    except llm.LLMError as e:
        log.error(f"script batch failed ({lang}): {e}")
        return {}
    out: dict[int, ScriptOut] = {}
    ids = [c.id for c in chunk]
    for j, s in enumerate(res.scripts):
        cid = s.job_id if s.job_id in ids else (ids[j] if j < len(ids) else None)
        if cid is not None:
            for sc in s.scenes:
                sc.visual.screenshot_index = min(max(0, sc.visual.screenshot_index), n_screens - 1)
            out[cid] = s
    return out
