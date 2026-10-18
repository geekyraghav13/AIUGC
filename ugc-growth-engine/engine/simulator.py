"""Simulated test market so the full loop can be demonstrated in minutes.

Each tag value has a hidden "true" effect (seeded per app). Funnel counts are drawn from Binomial/Beta
distributions using the combined effects + noise. The optimizer never reads these effects; the dashboard
can reveal them after the demo to show the loop learned the truth. Everything written here is labelled
source="simulated".
"""
from __future__ import annotations

import hashlib
import math
from datetime import date
from typing import Any, Optional

import numpy as np
from sqlmodel import delete

from engine.db import Creative, Metric, SimTruth, creatives, save, session
from engine.settings import env_int

# Spread of true effects per dimension (log-scale std dev).
SIGMA = {"language": 0.25, "hook_type": 0.30, "format": 0.18, "creator_style": 0.22, "message": 0.25,
         "length_bucket": 0.15, "visual_mode": 0.28}
# How strongly each dimension drives each funnel stage.
STAGE_WEIGHTS: dict[str, dict[str, float]] = {
    "hook": {"hook_type": 1.0, "format": 0.5, "language": 0.4, "creator_style": 0.3, "visual_mode": 0.6},
    "hold": {"creator_style": 0.8, "length_bucket": 1.0, "format": 0.4, "visual_mode": 0.5},
    "engage": {"hook_type": 0.5, "creator_style": 0.5, "language": 0.3},
    "ctr": {"message": 1.0, "hook_type": 0.5, "language": 0.6, "format": 0.3, "visual_mode": 0.3},
    "cvr": {"message": 0.5, "language": 0.6},
}
BASE = {"play": 0.55, "hook": 0.45, "hold": 0.38, "like": 0.035, "comment": 0.004, "share": 0.006,
        "save": 0.008, "ctr": 0.022, "cvr": 0.30, "d1": 0.40, "d7": 0.18}
NOISE = 0.12
IMPRESSIONS = 6000


def _seed(*parts: Any) -> int:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def sim_seed() -> int:
    return env_int("SIM_SEED", 42)


def true_effect(app_id: str, dim: str, value: str, seed: Optional[int] = None) -> float:
    """Hidden log-effect for one tag value — deterministic from (seed, app, dim, value)."""
    seed = sim_seed() if seed is None else seed
    rng = np.random.default_rng(_seed(seed, app_id, dim, value))
    return float(rng.normal(0.0, SIGMA.get(dim, 0.2)))


def stage_multipliers(app_id: str, tags: dict[str, str], seed: Optional[int] = None) -> dict[str, float]:
    out = {}
    for stage, weights in STAGE_WEIGHTS.items():
        z = sum(w * true_effect(app_id, d, tags.get(d, ""), seed) for d, w in weights.items())
        out[stage] = math.exp(z)
    return out


def _clip(p: float) -> float:
    return float(min(0.95, max(1e-5, p)))


def simulate_creative(c: Creative, seed: Optional[int] = None) -> dict[str, Any]:
    """Draw one creative's funnel. Deterministic for a given (seed, app, creative id)."""
    seed = sim_seed() if seed is None else seed
    tags = {d: getattr(c, d) or "" for d in SIGMA}
    m = stage_multipliers(c.app_id, tags, seed)
    rng = np.random.default_rng(_seed(seed, c.app_id, "creative", c.id))
    noise = {k: math.exp(rng.normal(0, NOISE)) for k in m}
    impressions = int(rng.poisson(IMPRESSIONS))
    views = int(rng.binomial(impressions, BASE["play"]))
    views_3s = int(rng.binomial(views, _clip(BASE["hook"] * m["hook"] * noise["hook"])))
    hold = _clip(BASE["hold"] * m["hold"] * noise["hold"])
    watch = float(rng.beta(hold * 60, (1 - hold) * 60)) * 100
    eng = m["engage"] * noise["engage"]
    likes = int(rng.binomial(views, _clip(BASE["like"] * eng)))
    comments = int(rng.binomial(views, _clip(BASE["comment"] * eng)))
    shares = int(rng.binomial(views, _clip(BASE["share"] * eng)))
    saves = int(rng.binomial(views, _clip(BASE["save"] * eng)))
    # clicks come from people who watched past the hook; better hold → more clicks
    p_click = _clip(BASE["ctr"] * m["ctr"] * noise["ctr"] * math.sqrt(watch / 100 / BASE["hold"]) / BASE["hook"])
    clicks = int(rng.binomial(views_3s, p_click))
    installs = int(rng.binomial(clicks, _clip(BASE["cvr"] * m["cvr"] * noise["cvr"])))
    return {
        "impressions": impressions, "views": views, "views_3s": views_3s, "avg_watch_pct": round(watch, 2),
        "likes": likes, "comments": comments, "shares": shares, "saves": saves, "clicks": clicks,
        "installs": installs, "d1_retained": int(rng.binomial(installs, BASE["d1"])),
        "d7_retained": int(rng.binomial(installs, BASE["d7"])), "spend_inr": 0.0,
    }


def simulate_generation(app_id: str, generation: int, include_unrendered: bool = False,
                        seed: Optional[int] = None) -> int:
    """Write SIMULATED metrics for a generation's published creatives. Returns count."""
    pool = [c for c in creatives(app_id, generation)
            if c.status in ("rendered", "published")
            or (include_unrendered and c.selected_for_render and c.script and c.status in ("approved", "draft"))]
    rows = []
    with session() as s:
        ids = [c.id for c in pool]
        if ids:
            s.exec(delete(Metric).where(Metric.creative_id.in_(ids), Metric.source == "simulated"))
            s.commit()
    for c in pool:
        rows.append(Metric(creative_id=c.id, source="simulated", platform="simulated", date=date.today().isoformat(),
                           **simulate_creative(c, seed)))
    if rows:
        save(*rows)
    _store_truth(app_id, pool, seed)
    return len(rows)


def _store_truth(app_id: str, pool: list[Creative], seed: Optional[int]) -> None:
    seed = sim_seed() if seed is None else seed
    with session() as s:
        t = s.get(SimTruth, app_id) or SimTruth(app_id=app_id, seed=seed, effects={})
        eff = dict(t.effects or {})
        for c in pool:
            for d in SIGMA:
                v = getattr(c, d) or ""
                eff.setdefault(d, {})[v] = round(true_effect(app_id, d, v, seed), 4)
        t.effects = eff
        t.seed = seed
        s.add(t)
        s.commit()


def reveal_truth(app_id: str) -> dict[str, list[tuple[str, float]]]:
    """Debug only: hidden effects per dimension, best first (for values that were tested)."""
    with session() as s:
        t = s.get(SimTruth, app_id)
    if not t:
        return {}
    return {d: sorted(vals.items(), key=lambda kv: kv[1], reverse=True) for d, vals in (t.effects or {}).items()}


def true_primary_multiplier(app_id: str, tags: dict[str, str], seed: Optional[int] = None) -> float:
    """Expected installs-per-view multiplier of a tag combo (for the truth check)."""
    m = stage_multipliers(app_id, tags, seed)
    return m["hook"] * m["ctr"] * m["cvr"] * math.sqrt(m["hold"])


