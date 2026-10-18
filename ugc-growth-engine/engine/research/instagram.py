"""Instagram Reels discovery via Apify (official apify/* actors). Paid per result → capped, cached, logged.

- apify/instagram-hashtag-scraper  → Reels for problem hashtags (India demand keywords → hashtags)
- apify/instagram-reel-scraper     → each creator's recent Reels (baseline), optional transcripts for analysis

We store metadata + our own analysis only; videos are never downloaded (D8.5). Gemini cannot open Instagram
URLs, so Reels are analysed from caption + hashtags (+ transcript when APIFY_TRANSCRIPTS=1).
"""
from __future__ import annotations

import re
import statistics
import unicodedata
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import requests
from sqlmodel import func, select

from engine import cache, cost
from engine.research import cfg
from engine.research.discover import _fresh, is_relevant
from engine.settings import env, env_float, env_int, log

API = "https://api.apify.com/v2"
HASHTAG_ACTOR = "apify~instagram-hashtag-scraper"
REEL_ACTOR = "apify~instagram-reel-scraper"
# Apify FREE-tier event prices (USD), used when a run reports no cost yet
EVENT_USD = {"result": 0.0026, "reel": 0.0026, "actor-start": 0.001, "transcript": 0.048, "shares-count": 0.007}


class ApifyBudgetReached(RuntimeError):
    pass


def enabled() -> bool:
    return bool(env("APIFY_API_TOKEN")) and env("APIFY_ENABLED", "1") != "0"


def spent_this_month_usd() -> float:
    from engine.db import Ledger, session

    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with session() as s:
        v = s.exec(select(func.sum(Ledger.cost_inr)).where(Ledger.step.startswith("apify:"),
                                                            Ledger.created_at >= start)).one()
    return float(v or 0.0) / env_float("USD_INR", 88.0)


def run_actor(actor: str, payload: dict[str, Any], max_charge_usd: float, app_id: Optional[str] = None,
              timeout_s: int = 600) -> list[dict[str, Any]]:
    """Run an Apify actor and return its dataset items. Cached for refresh_days; hard-capped per run and per month."""
    k = cache.key("apify", actor, payload)
    hit = cache.get_json("apify", k)
    if hit and _fresh(hit.get("at"), float(cfg()["refresh_days"])):
        return hit["items"]
    budget = env_float("APIFY_MONTHLY_BUDGET_USD", 4.5)
    spent = spent_this_month_usd()
    if spent + max_charge_usd > budget:
        raise ApifyBudgetReached(f"Apify monthly budget: ${spent:.2f} spent of ${budget:.2f}")
    token = env("APIFY_API_TOKEN")
    t0 = time.time()
    r = requests.post(f"{API}/acts/{actor}/runs", params={"token": token, "waitForFinish": 240,
                                                         "maxTotalChargeUsd": round(max_charge_usd, 2)},
                      json=payload, timeout=300)
    r.raise_for_status()
    run = r.json()["data"]
    while run["status"] in ("READY", "RUNNING") and time.time() - t0 < timeout_s:
        run = requests.get(f"{API}/actor-runs/{run['id']}", params={"token": token, "waitForFinish": 60},
                           timeout=120).json()["data"]
    if run["status"] not in ("SUCCEEDED",):
        log.warning(f"Apify {actor} run ended {run['status']} — using partial results")
    items = requests.get(f"{API}/datasets/{run['defaultDatasetId']}/items",
                         params={"token": token, "clean": "true", "format": "json"}, timeout=120).json()
    run = requests.get(f"{API}/actor-runs/{run['id']}", params={"token": token}, timeout=60).json()["data"]
    usd = float(run.get("usageTotalUsd") or 0.0)
    if not usd:
        # pay-per-event charges settle later; log a conservative estimate so the budget guard stays accurate
        events = run.get("chargedEventCounts") or {}
        usd = sum(n * EVENT_USD.get(e, 0.0026) for e, n in events.items()) or len(items) * 0.0026 + 0.001
    cost.log_step(f"apify:{actor.split('~')[1]}", "apify", time.time() - t0, app_id=app_id,
                  cost_inr=round(usd * env_float("USD_INR", 88.0), 2), units=len(items))
    log.info(f"Apify {actor}: {len(items)} items, ${usd:.3f}")
    cache.put_json("apify", k, {"at": datetime.now(timezone.utc).isoformat(), "items": items})
    return items


# ---------------------------------------------------------------- mapping

def to_hashtag(text: str) -> str:
    """'UPI kharcha kaise dekhe' → 'upikharcha' style tag (letters/digits only, first 3 words)."""
    joined = "".join(text.lower().split()[:3])
    # keep letters, digits and combining marks (Indic vowel signs are marks, not \w)
    return "".join(ch for ch in joined if unicodedata.category(ch)[0] in "LNM")[:30]


def views_of(it: dict[str, Any]) -> int:
    for k in ("videoPlayCount", "videoViewCount", "playCount", "viewCount"):
        if it.get(k):
            return int(it[k])
    return 0


def source_tag(it: dict[str, Any]) -> str:
    """The hashtag that surfaced an item (hashtag scraper sets inputUrl …/explore/tags/<tag>/)."""
    m = re.search(r"/tags/([^/?#]+)", str(it.get("inputUrl") or ""))
    return m.group(1) if m else ""


def to_rows(items: Iterable[dict[str, Any]], category_terms: list[str], object_terms: list[str]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows, seen = [], set()
    for it in items:
        code = it.get("shortCode") or it.get("id")
        if not code or code in seen or it.get("error"):
            continue
        seen.add(code)
        try:
            published = datetime.fromisoformat(str(it.get("timestamp", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        views = views_of(it)
        likes, comments = int(it.get("likesCount") or 0), int(it.get("commentsCount") or 0)
        age = max((now - published).days, 1)
        caption = it.get("caption") or ""
        text = caption + " " + " ".join(it.get("hashtags") or [])
        rows.append({
            "platform": "instagram", "video_id": code, "url": it.get("url") or f"https://www.instagram.com/reel/{code}/",
            "title": caption[:200], "channel_id": it.get("ownerUsername") or str(it.get("ownerId", "")),
            "channel_title": it.get("ownerFullName") or it.get("ownerUsername") or "",
            "published_at": published.date().isoformat(), "age_days": age,
            "duration_s": int(float(it.get("videoDuration") or 0)), "views": views, "likes": max(likes, 0),
            "comments": comments, "views_per_day": views / age, "engagement_rate": (max(likes, 0) + comments) / max(views, 1),
            "keywords": [f"#{source_tag(it)}"] if source_tag(it) else [],
            "relevant": is_relevant(text, category_terms, object_terms),
            "relevant_loose": is_relevant(text, category_terms, object_terms, loose=True),
            "thumbnail_url": it.get("displayUrl"), "caption": caption, "hashtags": it.get("hashtags") or [],
        })
    return rows


# ---------------------------------------------------------------- pipeline pieces

def discover(hashtags: list[str], category_terms: list[str], object_terms: list[str],
             app_id: Optional[str] = None) -> list[dict[str, Any]]:
    c = cfg()
    per = int(c.get("instagram_results_per_hashtag", 20))
    tags = [h for h in dict.fromkeys(to_hashtag(h) for h in hashtags) if h][: int(c.get("instagram_hashtags", 12))]
    if not tags:
        return []
    items = run_actor(HASHTAG_ACTOR, {"hashtags": tags, "resultsType": "reels", "resultsLimit": per},
                      max_charge_usd=0.0026 * per * len(tags) * 1.2 + 0.05, app_id=app_id)
    rows = to_rows(items, category_terms, object_terms)
    return [r for r in rows if 0 < r["duration_s"] <= int(c["max_duration_s"]) and r["views"] >= int(c["min_views"])]


def baselines(owners: list[str], app_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """Median views of each creator's recent Reels (Instagram equivalent of the channel baseline)."""
    c = cfg()
    owners = owners[: int(c.get("instagram_baseline_creators", 15))]
    n = int(c.get("instagram_baseline_reels", 12))
    if not owners:
        return {}
    items = run_actor(REEL_ACTOR, {"username": owners, "resultsLimit": n, "skipPinnedPosts": True},
                      max_charge_usd=0.0026 * n * len(owners) * 1.2 + 0.05, app_id=app_id)
    by_owner: dict[str, list[int]] = {}
    for it in items:
        o = it.get("ownerUsername")
        if o and views_of(it):
            by_owner.setdefault(o, []).append(views_of(it))
    return {o: {"median_recent_views": float(statistics.median(v)), "baseline_sample": len(v),
                "channel_avg_views": float(sum(v) / len(v))} for o, v in by_owner.items()}


def transcripts(urls: list[str], app_id: Optional[str] = None) -> dict[str, str]:
    """Optional (APIFY_TRANSCRIPTS=1, $0.048 each): transcripts of the Reels we analyse."""
    if env_int("APIFY_TRANSCRIPTS", 0) != 1 or not urls:
        return {}
    items = run_actor(REEL_ACTOR, {"username": urls, "resultsLimit": 1, "includeTranscript": True},
                      max_charge_usd=0.051 * len(urls) * 1.2 + 0.05, app_id=app_id)
    return {it.get("shortCode"): it.get("transcript") or "" for it in items if it.get("shortCode")}
