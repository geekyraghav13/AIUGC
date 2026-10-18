"""Step 5 — pattern statistics over analysed market videos (statistics first, same rules as insights)."""
from __future__ import annotations

from typing import Any

import pandas as pd

from engine.research import cfg

DIMENSIONS = ["platform", "format", "hook_type", "language", "closest_creator_style", "production_style", "emotional_trigger",
              "cta_type", "length_bucket", "uses_numbers", "shows_app_or_screen"]
COMBO = ["format", "hook_type", "language"]
# market dimension → our Creative tag
TAG_MAP = {"format": "format", "hook_type": "hook_type", "language": "language",
           "closest_creator_style": "creator_style", "cta_type": "cta_type", "length_bucket": "length_bucket"}


def min_videos() -> int:
    return int(cfg()["min_pattern_videos"])


def table(A: pd.DataFrame, cols: list[str], dimension: str) -> pd.DataFrame:
    """Per value: videos, share, performance-weighted share, avg outlier, lift vs overall avg outlier, signal."""
    if A.empty:
        return pd.DataFrame()
    total_score = float(A["market_score"].sum()) or 1.0
    overall = float(A["outlier_score"].mean()) or 1.0
    rows = []
    for key, g in A.groupby(cols):
        key = key if isinstance(key, tuple) else (key,)
        ex = g.sort_values("market_score", ascending=False)
        rows.append({
            "dimension": dimension, "value": " + ".join(map(str, key)), "videos": len(g),
            "share": len(g) / len(A), "weighted_share": float(g["market_score"].sum()) / total_score,
            "avg_outlier": float(g["outlier_score"].mean()),
            "lift": float(g["outlier_score"].mean()) / overall - 1,
            "signal": "pattern" if len(g) >= min_videos() else "weak signal",
            "examples": [{"market_video_id": int(r.market_video_id), "url": r.url} for r in ex.head(2).itertuples()],
        })
    return pd.DataFrame(rows).sort_values(["weighted_share", "avg_outlier"], ascending=False).reset_index(drop=True)


def tokens(t: str) -> set[str]:
    """Content words (≥ 3 characters) — Indic-safe."""
    from engine.localize import words

    return {w for w in words(t) if len(w) > 2}


def topic_clusters(A: pd.DataFrame, threshold: float = 0.34) -> pd.DataFrame:
    """Greedy clustering of topic_angle by token Jaccard; label = the best-scoring angle in the cluster."""
    clusters: list[dict[str, Any]] = []
    for r in A.sort_values("market_score", ascending=False).itertuples():
        tk = tokens(r.topic_angle)
        for c in clusters:
            if tk and len(tk & c["tokens"]) / len(tk | c["tokens"]) >= threshold:
                c["rows"].append(r)
                c["tokens"] |= tk
                break
        else:
            clusters.append({"label": r.topic_angle, "tokens": tk, "rows": [r]})
    B = A.copy()
    label = {}
    for c in clusters:
        for r in c["rows"]:
            label[r.Index] = c["label"]
    B["topic_cluster"] = B.index.map(label)
    return table(B, ["topic_cluster"], "topic_angle")


def compute(A: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """A = one row per analysed, relevant market video (analysis fields + scores)."""
    if A.empty:
        return {}
    A = A.copy()
    for b in ("uses_numbers", "shows_app_or_screen"):
        if b in A:
            A[b] = A[b].map(lambda x: "yes" if x else "no")
    out = {d: table(A, [d], d) for d in DIMENSIONS if d in A}
    out["combo"] = table(A, COMBO, "format × hook_type × language").head(10)
    out["topic_angle"] = topic_clusters(A)
    return out


def all_rows(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.concat([t for t in tables.values() if not t.empty], ignore_index=True) if tables else pd.DataFrame()
