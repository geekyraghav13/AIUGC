"""Cost + time ledger. Every pipeline step logs seconds, actual ₹ (always 0) and a paid-equivalent ₹."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlmodel import select

from engine.settings import env_float

# What the same step would roughly cost on paid services (for the Economics comparison only).
PAID_EQUIV: dict[str, float] = {
    "llm_call": 0.8,        # ~a mid-size paid LLM call
    "tts_scene": 0.4,       # paid neural TTS per scene
    "stock_asset": 0.0,     # Pexels/Pixabay are free for commercial use
    "render": 1.5,          # cloud render minutes
}


class BudgetExceeded(RuntimeError):
    pass


def gemini_cost_inr(prompt_tokens: int, output_tokens: int) -> float:
    """Actual ₹ cost of one Gemini call from token usage. Prices are per 1M tokens in USD (.env, check
    https://ai.google.dev/gemini-api/docs/pricing for your model); output includes thinking tokens."""
    usd = (prompt_tokens * env_float("GEMINI_PRICE_INPUT_USD_PER_M", 0.30)
           + output_tokens * env_float("GEMINI_PRICE_OUTPUT_USD_PER_M", 2.50)) / 1e6
    return round(usd * env_float("USD_INR", 88.0), 4)


def usage_cost(resp) -> tuple[int, int, float]:  # noqa: ANN001
    """(prompt tokens, output tokens, ₹) from a google-genai response."""
    um = getattr(resp, "usage_metadata", None)
    pt = int(getattr(um, "prompt_token_count", 0) or 0)
    ot = int(getattr(um, "candidates_token_count", 0) or 0) + int(getattr(um, "thoughts_token_count", 0) or 0)
    return pt, ot, gemini_cost_inr(pt, ot)


def spent_today_inr() -> float:
    from datetime import datetime, timezone

    from sqlmodel import func

    from engine.db import Ledger, session

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with session() as s:
        v = s.exec(select(func.sum(Ledger.cost_inr)).where(Ledger.created_at >= start)).one()
    return float(v or 0.0)


def check_budget() -> None:
    """Refuse new paid calls once today's spend reaches GEMINI_DAILY_BUDGET_INR."""
    cap = env_float("GEMINI_DAILY_BUDGET_INR", 500.0)
    spent = spent_today_inr()
    if spent >= cap:
        raise BudgetExceeded(f"Daily Gemini budget reached: ₹{spent:.2f} of ₹{cap:.0f} "
                             "(raise GEMINI_DAILY_BUDGET_INR in .env to continue)")


def log_step(step: str, provider: str, seconds: float, app_id: Optional[str] = None,
             cost_inr: float = 0.0, paid_equivalent_inr: float = 0.0, units: int = 1) -> None:
    from engine.db import Ledger, save  # local import: db imports settings only

    try:
        save(Ledger(app_id=app_id, step=step, provider=provider, seconds=round(seconds, 3),
                    cost_inr=cost_inr, paid_equivalent_inr=paid_equivalent_inr, units=units))
    except Exception:  # noqa: BLE001 — ledger must never break the pipeline
        pass


@contextmanager
def timed(step: str, provider: str = "local", app_id: Optional[str] = None,
          paid_equivalent_inr: float = 0.0, units: int = 1) -> Iterator[None]:
    t0 = time.time()
    try:
        yield
    finally:
        log_step(step, provider, time.time() - t0, app_id=app_id,
                 paid_equivalent_inr=paid_equivalent_inr, units=units)


def summary(app_id: str) -> dict:
    """Economics numbers for the dashboard."""
    from engine.db import Creative, Ledger, Video, session

    with session() as s:
        rows = list(s.exec(select(Ledger).where(Ledger.app_id == app_id)).all())
        creatives = list(s.exec(select(Creative).where(Creative.app_id == app_id)).all())
        ids = [c.id for c in creatives]
        videos = list(s.exec(select(Video).where(Video.creative_id.in_(ids))).all()) if ids else []

    total_seconds = sum(r.seconds for r in rows)
    total_cost = sum(r.cost_inr for r in rows)
    paid_equiv = sum(r.paid_equivalent_inr for r in rows)
    n_rendered = len(videos)
    n_scripted = sum(1 for c in creatives if c.script)
    render_secs = sum(v.render_seconds for v in videos)
    per_video_secs = (total_seconds / n_rendered) if n_rendered else 0.0
    per_hour = 3600 / per_video_secs if per_video_secs else 0.0
    creator_cost = env_float("CREATOR_REEL_COST_INR", 10000)
    creator_days = env_float("CREATOR_TURNAROUND_DAYS", 7)
    by_step: dict[str, dict[str, float]] = {}
    for r in rows:
        key = r.step.split(":")[0]
        d = by_step.setdefault(key, {"seconds": 0.0, "calls": 0, "cost_inr": 0.0, "paid_equivalent_inr": 0.0})
        d["seconds"] += r.seconds
        d["calls"] += 1
        d["cost_inr"] += r.cost_inr
        d["paid_equivalent_inr"] += r.paid_equivalent_inr
    return {
        "total_seconds": total_seconds,
        "total_cost_inr": total_cost,
        "paid_equivalent_inr": paid_equiv,
        "creatives_total": len(creatives),
        "creatives_scripted": n_scripted,
        "videos_rendered": n_rendered,
        "render_seconds": render_secs,
        "cost_per_creative_inr": (total_cost / n_rendered) if n_rendered else 0.0,
        "seconds_per_video": per_video_secs,
        "videos_per_hour": per_hour,
        # 8 working hours × 5 days on one laptop
        "experiments_per_week": per_hour * 40,
        "creator_cost_inr": creator_cost,
        "creator_turnaround_days": creator_days,
        "traditional_cost_same_volume_inr": creator_cost * n_rendered,
        "by_step": by_step,
    }
