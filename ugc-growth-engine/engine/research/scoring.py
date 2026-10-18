"""Step 3 — outlier scoring. A 50K-view video on a channel that normally gets 2K is a stronger format signal
than 500K views on a channel that normally gets 1M."""
from __future__ import annotations

import statistics
from typing import Any, Iterable

import pandas as pd

from engine.research import cfg
from engine.research.discover import QuotaBudgetReached, YouTube, chunks, parse_duration, to_int, video_details
from engine.settings import log

MIN_BASELINE = 200


def channel_baselines(yt: YouTube, channel_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Median views of each channel's last N uploads (Shorts only if ≥ 5), plus the lifetime average fallback."""
    c = cfg()
    sample, max_dur = int(c["baseline_sample"]), int(c["max_duration_s"])
    info: dict[str, dict[str, Any]] = {}
    try:
        for batch in chunks(channel_ids, 50):
            res = yt.call("channels", {"part": "contentDetails,statistics", "id": ",".join(batch), "maxResults": 50})
            for ch in res.get("items", []):
                st = ch.get("statistics", {})
                info[ch["id"]] = {
                    "uploads": ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads"),
                    "channel_avg_views": to_int(st.get("viewCount")) / max(to_int(st.get("videoCount")), 1),
                    "median_recent_views": None, "baseline_sample": 0,
                }
        for cid, d in info.items():
            if not d["uploads"]:
                continue
            try:
                pl = yt.call("playlistItems", {"part": "contentDetails", "playlistId": d["uploads"], "maxResults": sample})
            except RuntimeError as e:
                log.debug(f"uploads unavailable for {cid}: {e}")
                continue
            vids = [it["contentDetails"]["videoId"] for it in pl.get("items", [])]
            items = video_details(yt, vids) if vids else []
            d["median_recent_views"], d["baseline_sample"] = recent_median(items, max_dur)
    except QuotaBudgetReached as e:
        log.warning(f"{e} — remaining channels fall back to lifetime averages")
    return info


def recent_median(items: list[dict[str, Any]], max_dur: int) -> tuple[float | None, int]:
    """Median views of recent uploads: Shorts only if there are at least 5, else all uploads."""
    views_all = [to_int(v.get("statistics", {}).get("viewCount")) for v in items]
    shorts = [to_int(v.get("statistics", {}).get("viewCount")) for v in items
              if 0 < parse_duration(v.get("contentDetails", {}).get("duration")) <= max_dur]
    pool = shorts if len(shorts) >= 5 else views_all
    return (float(statistics.median(pool)), len(pool)) if pool else (None, 0)


def baseline(row: dict[str, Any] | pd.Series, fallback: float) -> float:
    """Recent median if sampled from ≥ 5 videos, else lifetime average, else `fallback`; floored at 200."""
    med, n, avg = row.get("median_recent_views"), row.get("baseline_sample") or 0, row.get("channel_avg_views")
    b = med if (med is not None and pd.notna(med) and n >= 5) else avg
    if b is None or pd.isna(b) or b <= 0:
        b = fallback
    return float(max(b, MIN_BASELINE))


def pct(s: pd.Series) -> pd.Series:
    return s.rank(pct=True)


def score_videos(df: pd.DataFrame) -> pd.DataFrame:
    """outlier_score = views / max(baseline, 200); market_score = .55 pct(outlier) + .25 pct(views/day) + .20 pct(ER)."""
    if df.empty:
        return df
    df = df.copy()
    fallback = float(df["views"].median())
    df["baseline"] = df.apply(lambda r: baseline(r, fallback), axis=1)
    df["outlier_score"] = (df["views"] / df["baseline"]).round(2)
    df["market_score"] = (0.55 * pct(df["outlier_score"]) + 0.25 * pct(df["views_per_day"])
                          + 0.20 * pct(df["engagement_rate"])).round(3)
    return df.sort_values("market_score", ascending=False)


def select_top(df: pd.DataFrame, n: int, max_per_channel: int) -> pd.DataFrame:
    """Top n relevant videos by market score, at most `max_per_channel` per creator."""
    ranked = df[df["relevant"]].sort_values("market_score", ascending=False)
    return ranked.groupby("channel_id", sort=False).head(max_per_channel).head(n)
