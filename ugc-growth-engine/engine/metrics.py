"""Performance data: manual entry, CSV import, aggregation and derived metrics (never stored)."""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any, Optional

import numpy as np
import pandas as pd
from sqlmodel import select

from engine.db import Creative, Metric, Video, save, session
from engine.settings import env

COUNT_FIELDS = ["impressions", "views", "views_3s", "likes", "comments", "shares", "saves", "clicks"]
OPTIONAL_COUNTS = ["installs", "d1_retained", "d7_retained"]
TAG_COLS = ["language", "hook_type", "format", "creator_style", "persona", "message", "cta_type", "length_bucket",
            "visual_mode"]


def _div(a: Any, b: Any) -> Any:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b > 0, a / b, np.nan)


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Hook rate, hold, engagement, CTR, CVR, installs/1k views, CPI, retention."""
    if df.empty:
        return df
    df = df.copy()
    df["hook_rate"] = _div(df["views_3s"], df["impressions"])
    df["engagement_rate"] = _div(df[["likes", "comments", "shares", "saves"]].sum(axis=1), df["views"])
    df["ctr"] = _div(df["clicks"], df["views"])
    df["cvr"] = _div(df["installs"], df["clicks"])
    df["installs_per_1k_views"] = _div(df["installs"] * 1000, df["views"])
    df["cpi"] = _div(df["spend_inr"], df["installs"])
    df["d1_rate"] = _div(df["d1_retained"], df["installs"])
    df["d7_rate"] = _div(df["d7_retained"], df["installs"])
    df["engagement_x_watch"] = df["engagement_rate"] * df["avg_watch_pct"] / 100
    return df


def primary_metric_name(df: Optional[pd.DataFrame] = None) -> str:
    """Configured primary metric, falling back when its inputs are missing."""
    want = env("PRIMARY_METRIC", "installs_per_1k_views")
    if df is None or df.empty:
        return want
    chain = [want, "installs_per_1k_views", "ctr", "engagement_x_watch"]
    for m in chain:
        if m in df and df[m].notna().any():
            return m
    return "engagement_x_watch"


METRIC_LABELS = {
    "installs_per_1k_views": "Installs / 1k views", "ctr": "CTR", "engagement_x_watch": "Engagement × watch %",
    "hook_rate": "Hook rate", "engagement_rate": "Engagement rate", "cvr": "Install rate (CVR)",
    "avg_watch_pct": "Avg watch %", "cpi": "CPI (₹)",
}


def metrics_frame(app_id: str, source: str = "all", generation: Optional[int] = None) -> pd.DataFrame:
    """One row per creative with tags + summed metrics + derived metrics."""
    with session() as s:
        q = select(Creative).where(Creative.app_id == app_id)
        if generation is not None:
            q = q.where(Creative.generation == generation)
        cr = {c.id: c for c in s.exec(q).all()}
        if not cr:
            return pd.DataFrame()
        mq = select(Metric).where(Metric.creative_id.in_(list(cr)))
        if source != "all":
            mq = mq.where(Metric.source == source)
        rows = list(s.exec(mq).all())
        vids = {v.creative_id: v for v in s.exec(select(Video).where(Video.creative_id.in_(list(cr)))).all()}
    if not rows:
        return pd.DataFrame()
    m = pd.DataFrame([r.model_dump() for r in rows])
    m["watch_x_views"] = m["avg_watch_pct"] * m["views"]
    agg = {f: "sum" for f in COUNT_FIELDS + ["spend_inr", "watch_x_views"]}
    agg.update({f: (lambda x: x.sum(min_count=1)) for f in OPTIONAL_COUNTS})
    agg["source"] = lambda x: "/".join(sorted(set(x)))
    g = m.groupby("creative_id").agg(agg).reset_index()
    g["avg_watch_pct"] = _div(g["watch_x_views"], g["views"])
    g = g.drop(columns=["watch_x_views"])
    tags = pd.DataFrame([{
        "creative_id": c.id, "generation": c.generation, "hook": c.hook, "origin": c.origin,
        "parent_creative_id": c.parent_creative_id, "tracking_slug": c.tracking_slug, "status": c.status,
        "predicted_score": c.predicted_score, "concept_id": c.concept_id,
        "video_path": vids[c.id].path if c.id in vids else None,
        "thumbnail_path": vids[c.id].thumbnail_path if c.id in vids else None,
        **{t: getattr(c, t) for t in TAG_COLS},
    } for c in cr.values()])
    return add_derived(tags.merge(g, on="creative_id", how="inner"))


def totals(df: pd.DataFrame) -> dict[str, float]:
    """KPI cards over a frame (ratios of sums, not means of ratios)."""
    if df.empty:
        return {}
    s = df[COUNT_FIELDS + ["spend_inr"]].sum()
    inst = df["installs"].sum(min_count=1)
    views = s["views"]
    watch = float(np.nansum(df["avg_watch_pct"] * df["views"]) / views) if views else float("nan")
    return {
        "impressions": s["impressions"], "views": views, "hook_rate": s["views_3s"] / s["impressions"] if s["impressions"] else np.nan,
        "avg_watch_pct": watch,
        "engagement_rate": (s["likes"] + s["comments"] + s["shares"] + s["saves"]) / views if views else np.nan,
        "ctr": s["clicks"] / views if views else np.nan, "clicks": s["clicks"],
        "installs": inst, "installs_per_1k_views": inst * 1000 / views if views and pd.notna(inst) else np.nan,
        "cvr": inst / s["clicks"] if s["clicks"] and pd.notna(inst) else np.nan,
        "spend_inr": s["spend_inr"], "cpi": s["spend_inr"] / inst if inst else np.nan,
        "creatives": len(df),
    }


# ---------------------------------------------------------------- input

def add_metric(creative_id: int, source: str = "real", **fields: Any) -> Metric:
    m = Metric(creative_id=creative_id, source=source, date=fields.pop("date", None) or date.today().isoformat(),
               **{k: v for k, v in fields.items() if k in Metric.model_fields and v is not None and v != ""})
    save(m)
    return m


def import_csv(text: str, default_platform: str = "instagram") -> tuple[int, list[str]]:
    """Import real metrics. Columns match the Metric table; `slug` (tracking slug) identifies the creative."""
    reader = csv.DictReader(io.StringIO(text))
    with session() as s:
        slugs = {c.tracking_slug: c.id for c in s.exec(select(Creative)).all() if c.tracking_slug}
    n, errors = 0, []
    numeric = set(COUNT_FIELDS + OPTIONAL_COUNTS)
    for i, row in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        slug = row.get("slug") or row.get("tracking_slug") or row.get("utm_content")
        cid = slugs.get(slug) if slug else (int(row["creative_id"]) if row.get("creative_id", "").isdigit() else None)
        if cid is None:
            errors.append(f"line {i}: unknown slug {slug!r}")
            continue
        try:
            fields: dict[str, Any] = {"platform": row.get("platform") or default_platform, "date": row.get("date") or None}
            for k in numeric:
                if row.get(k, "") != "":
                    fields[k] = int(float(row[k].replace(",", "")))
            for k in ("avg_watch_pct", "spend_inr"):
                if row.get(k, "") != "":
                    fields[k] = float(row[k].replace(",", "").rstrip("%"))
            add_metric(cid, source="real", **fields)
            n += 1
        except ValueError as e:
            errors.append(f"line {i}: {e}")
    return n, errors


CSV_TEMPLATE_COLUMNS = ["slug", "platform", "date", "impressions", "views", "views_3s", "avg_watch_pct", "likes",
                        "comments", "shares", "saves", "clicks", "installs", "spend_inr", "d1_retained", "d7_retained"]
