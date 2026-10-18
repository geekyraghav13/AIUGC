"""Visual modes — how a video is shot: faceless · images_vo · character · ai_character.

Each app gets its own mix, decided by that app's market research (which production styles over-perform for its
topic), shaped by what each format/creator style suits, and fitted to the Magnific credits available. Every creative
carries its mode as a tag, so insights and the growth loop learn which mode wins for that app.
"""
from __future__ import annotations

import random
from typing import Any, Iterable, Optional

from engine.settings import creator_styles, formats, load_yaml, log, taxonomy

MODES = ("faceless", "images_vo", "character", "ai_character")
FLOOR = 0.05          # every compatible mode keeps a small share, so a mode the market shows little of can still win
FREE_MODES = ("faceless", "images_vo")   # renderable without Magnific (stock footage / stock photos)


def mode_cost(mode: str) -> int:
    """Typical Magnific credits for one video in this mode (config production.yaml → magnific.modes)."""
    modes = (load_yaml("production").get("magnific") or {}).get("modes") or {}
    return int((modes.get(mode) or {}).get("estimate", 0))


def compatible(fmt: str, style: str) -> list[str]:
    """Modes a creative can be shot in: allowed by its creator style, ordered by its format's preference."""
    by_style = creator_styles().get(style, {}).get("modes") or list(MODES)
    pref = formats().get(fmt, {}).get("modes") or list(MODES)
    ordered = [m for m in pref if m in by_style] + [m for m in by_style if m not in pref]
    return ordered or ["faceless"]


def market_shares(app_id: str) -> Optional[dict[str, float]]:
    """Performance-weighted share of each mode among this app's over-performing market videos (None = no research)."""
    from engine.research.market import context

    ctx = context(app_id)
    if not ctx.available:
        return None
    rows = ctx.patterns[ctx.patterns["dimension"] == "production_style"]
    # "other" = the analyst couldn't tell (e.g. Reels judged from their caption only): it's no evidence for any mode
    rows = rows[rows["value"].astype(str) != "other"] if not rows.empty else rows
    if rows.empty:
        return None
    from engine.research import cfg as rcfg

    if int(rows["videos"].sum()) < int(rcfg().get("min_research_videos", 8)):
        return None                    # too few analysed videos to trust a mix: format fit decides
    m2v = taxonomy()["market_to_visual_mode"]
    shares = {m: 0.0 for m in MODES}
    for r in rows.itertuples():
        shares[m2v.get(str(r.value), "faceless")] += float(r.weighted_share)
    total = sum(shares.values()) or 1.0
    return {m: v / total for m, v in shares.items()}


def planned_shares(app_id: str) -> tuple[dict[str, float], str]:
    """Target mix for this app + where it came from ('research' or 'format fit')."""
    ms = market_shares(app_id)
    if ms is None:
        return {m: 1 / len(MODES) for m in MODES}, "format fit (no market research for this app yet)"
    floored = {m: max(FLOOR, v) for m, v in ms.items()}
    total = sum(floored.values())
    return {m: v / total for m, v in floored.items()}, "market research"


def pick(fmt: str, style: str, shares: dict[str, float], rng: random.Random, source: str) -> str:
    """One creative's mode: weighted by the app's research (or the format's preference order when there is none)."""
    ok = compatible(fmt, style)
    if source.startswith("format fit"):
        weights = [1.0 / (i + 1) for i in range(len(ok))]  # best-fitting mode first, the rest still explored
    else:
        weights = [shares.get(m, FLOOR) for m in ok]
    return rng.choices(ok, weights=weights, k=1)[0]


def assign(app_id: str, creatives: Iterable[Any], seed: int = 0) -> dict[str, Any]:
    """Tag every creative with a visual mode (in place). Returns the plan for logging / the dashboard."""
    shares, source = planned_shares(app_id)
    rng = random.Random(f"modes:{app_id}:{seed}")
    counts: dict[str, int] = {m: 0 for m in MODES}
    for c in creatives:
        if not getattr(c, "visual_mode", ""):
            c.visual_mode = pick(c.format, c.creator_style, shares, rng, source)
        counts[c.visual_mode] = counts.get(c.visual_mode, 0) + 1
    return {"source": source, "target": {m: round(v, 3) for m, v in shares.items()}, "assigned": counts}


def fit_budget(chosen: list[Any], credits_left: int) -> list[str]:
    """Make the videos picked for production affordable: if the chosen modes need more Magnific credits than are left,
    move the most expensive ones to their next-best cheaper compatible mode. Changes creatives in place; returns notes."""
    notes = []
    need = sum(mode_cost(c.visual_mode) for c in chosen)
    for c in sorted(chosen, key=lambda c: -mode_cost(c.visual_mode)):
        if need <= credits_left:
            break
        cheaper = [m for m in compatible(c.format, c.creator_style) if mode_cost(m) < mode_cost(c.visual_mode)]
        if not cheaper:
            continue
        new = cheaper[0]
        need -= mode_cost(c.visual_mode) - mode_cost(new)
        notes.append(f"#{c.id}: {c.visual_mode} → {new} (credits)")
        c.visual_mode = new
    if notes:
        log.info(f"mode budget fit: {len(notes)} videos moved to cheaper modes; need {need} of {credits_left} credits")
    return notes


def summary(app_id: str, creatives: Iterable[Any]) -> dict[str, Any]:
    shares, source = planned_shares(app_id)
    counts: dict[str, int] = {m: 0 for m in MODES}
    for c in creatives:
        if c.visual_mode:
            counts[c.visual_mode] = counts.get(c.visual_mode, 0) + 1
    return {"source": source, "target": shares, "counts": counts,
            "credits_estimate": sum(mode_cost(m) * n for m, n in counts.items())}
