"""Step 2 — discover Shorts: YouTube Data API v3 (search.list + videos.list), relevance filter, quota tracking.

Uses plain REST calls via `requests` (same endpoints as google-api-python-client). Every response is cached on
disk for `refresh_days`, so re-runs cost 0 quota. `MockYouTube` gives deterministic data for offline dry runs.
"""
from __future__ import annotations

import hashlib
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import requests
from sqlmodel import func, select

from engine import cache, cost
from engine.research import cfg
from engine.settings import env, env_int, log, market

API = "https://www.googleapis.com/youtube/v3/"
UNITS = {"search": 100, "videos": 1, "channels": 1, "playlistItems": 1}


class QuotaBudgetReached(RuntimeError):
    pass


# ---------------------------------------------------------------- relevance

def has_term(text: str, terms: Iterable[str]) -> bool:
    """Word-boundary match for Latin-script terms, substring match for Indic scripts."""
    for t in terms:
        if not t:
            continue
        if t.isascii():
            if re.search(r"\b" + re.escape(t) + r"\b", text):
                return True
        elif t in text:
            return True
    return False


def is_relevant(text: str, category_terms: list[str], object_terms: list[str], loose: bool = False) -> bool:
    """Strict: a category term AND an object term. Loose (niche apps, widened search): any of them."""
    t = (text or "").lower()
    if not category_terms:
        return True
    if loose:
        return has_term(t, category_terms) or has_term(t, object_terms or [])
    return has_term(t, category_terms) and (not object_terms or has_term(t, object_terms))


# ---------------------------------------------------------------- helpers

_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")


def parse_duration(iso: Optional[str]) -> int:
    """ISO-8601 duration (PT1M5S) → seconds; 0 if unparseable."""
    m = _DUR.fullmatch(iso or "")
    if not m or not any(m.groups()):
        return 0
    d, h, mi, s = m.groups()
    return int(int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0))


def to_int(x: Any) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def chunks(lst: Iterable[Any], n: int) -> Iterable[list[Any]]:
    lst = list(lst)
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def quota_used_today() -> int:
    from engine.db import Ledger, session

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with session() as s:
        v = s.exec(select(func.sum(Ledger.units)).where(Ledger.step.startswith("youtube:"),
                                                         Ledger.created_at >= start)).one()
    return int(v or 0)


# ---------------------------------------------------------------- clients

class YouTube:
    """Minimal YouTube Data API v3 client with cache, retries, backoff and a daily quota budget."""

    source = "youtube"

    def __init__(self, app_id: Optional[str] = None):
        self.key = env("YOUTUBE_API_KEY")
        self.app_id = app_id
        self.budget = env_int("YOUTUBE_DAILY_QUOTA_BUDGET", 8000)
        self.used = 0  # units spent by this client (cache hits cost 0)
        self.ttl_days = float(cfg()["refresh_days"])

    def call(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        k = cache.key("yt", endpoint, sorted(params.items()))
        hit = cache.get_json("youtube", k)
        if hit and _fresh(hit.get("at"), self.ttl_days):
            return hit["data"]
        units = UNITS[endpoint]
        if quota_used_today() + units > self.budget:
            raise QuotaBudgetReached(f"YouTube quota budget {self.budget} reached for today")
        data = self._get(endpoint, params)
        self.used += units
        cost.log_step(f"youtube:{endpoint}", "youtube-data-api", 0.0, app_id=self.app_id, units=units)
        cache.put_json("youtube", k, {"at": datetime.now(timezone.utc).isoformat(), "data": data})
        return data

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(4):
            try:
                r = requests.get(API + endpoint, params={**params, "key": self.key}, timeout=30)
                if r.status_code == 403 and "quota" in r.text.lower():
                    raise QuotaBudgetReached("YouTube API daily quota exceeded")
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"{r.status_code}", response=r)
                r.raise_for_status()
                return r.json()
            except QuotaBudgetReached:
                raise
            except requests.RequestException as e:
                last = e
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code in (400, 401, 403, 404):
                    break
                time.sleep(min(30, 2 ** attempt * 2))
        raise RuntimeError(f"YouTube {endpoint} failed: {last}")


class MockYouTube(YouTube):
    """Deterministic fake API for offline dry runs and tests (clearly labelled source='mock')."""

    source = "mock"

    def __init__(self, app_id: Optional[str] = None, terms: Optional[list[str]] = None):
        super().__init__(app_id)
        self.terms = terms or ["budget"]

    def call(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        self.used += UNITS[endpoint]
        return getattr(self, f"_{endpoint}")(params)

    @staticmethod
    def _rng(*parts: Any) -> random.Random:
        return random.Random(int(hashlib.md5(":".join(map(str, parts)).encode()).hexdigest()[:8], 16))

    def _search(self, p: dict[str, Any]) -> dict[str, Any]:
        rng = self._rng("search", p["q"])
        n = min(int(p.get("maxResults", 25)), 25)
        # overlapping pools so some videos surface for several keywords
        ids = [f"mock{rng.randint(0, 399):04d}" for _ in range(n)]
        return {"items": [{"id": {"kind": "youtube#video", "videoId": v}} for v in dict.fromkeys(ids)]}

    def _videos(self, p: dict[str, Any]) -> dict[str, Any]:
        items = []
        for vid in p["id"].split(","):
            rng = self._rng("video", vid)
            ch = f"UCmock{rng.randint(0, 59):03d}"
            term = self.terms[rng.randint(0, len(self.terms) - 1)]
            relevant = rng.random() < 0.8
            days = rng.randint(3, 170)
            views = int(rng.lognormvariate(10.3, 1.4))
            items.append({
                "id": vid,
                "snippet": {"title": f"{term if relevant else 'cooking'} {rng.choice(['tips', 'hack', 'mistake', 'kya hai'])} #{vid[-3:]}",
                            "description": "mock video", "channelId": ch, "channelTitle": f"Mock Creator {ch[-3:]}",
                            "publishedAt": (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "tags": [], "defaultAudioLanguage": rng.choice(["hi", "en", "ta"])},
                "statistics": {"viewCount": str(views), "likeCount": str(int(views * rng.uniform(0.01, 0.06))),
                               "commentCount": str(int(views * rng.uniform(0.0005, 0.004)))},
                "contentDetails": {"duration": f"PT{rng.randint(12, 175)}S"},
            })
        return {"items": items}

    def _channels(self, p: dict[str, Any]) -> dict[str, Any]:
        out = []
        for cid in p["id"].split(","):
            rng = self._rng("channel", cid)
            vc = rng.randint(20, 900)
            out.append({"id": cid, "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}},
                        "statistics": {"subscriberCount": str(rng.randint(1000, 900000)),
                                       "viewCount": str(vc * int(rng.lognormvariate(9.5, 1.0))), "videoCount": str(vc)}})
        return {"items": out}

    def _playlistItems(self, p: dict[str, Any]) -> dict[str, Any]:
        rng = self._rng("uploads", p["playlistId"])
        n = int(p.get("maxResults", 30))
        return {"items": [{"contentDetails": {"videoId": f"up{p['playlistId'][-3:]}{i:02d}{rng.randint(0, 9)}"}}
                          for i in range(n)]}


def client(app_id: Optional[str] = None, terms: Optional[list[str]] = None) -> YouTube:
    from engine.research import youtube_mode

    mode = youtube_mode()
    if mode == "youtube":
        return YouTube(app_id)
    if mode == "mock":
        return MockYouTube(app_id, terms)
    raise RuntimeError("Market research needs YOUTUBE_API_KEY in .env (free: Google Cloud console → enable "
                       "YouTube Data API v3 → Credentials → API key). Use LLM_PROVIDER=mock for an offline dry run.")


def _fresh(at: Optional[str], days: float) -> bool:
    if not at:
        return False
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds() < days * 86400
    except ValueError:
        return False


# ---------------------------------------------------------------- discovery

def search_keyword(yt: YouTube, kw: str, days_back: Optional[int] = None, region: Optional[str] = None) -> list[str]:
    c = cfg()
    days = int(days_back or c["days_back"])
    # the window is snapped to the day so repeat searches hit the cache
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    res = yt.call("search", {"q": kw, "part": "id", "type": "video", "maxResults": int(c["results_per_keyword"]),
                             "order": "viewCount", "regionCode": region or market()["regions"][0], "videoDuration": "short",
                             "publishedAfter": after})
    return [i["id"]["videoId"] for i in res.get("items", []) if i.get("id", {}).get("videoId")]


def video_details(yt: YouTube, ids: Iterable[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for batch in chunks(ids, 50):
        res = yt.call("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch),
                                 "maxResults": 50})
        out.extend(res.get("items", []))
    return out


def to_rows(items: list[dict[str, Any]], found: dict[str, set[str]], category_terms: list[str],
            object_terms: list[str]) -> list[dict[str, Any]]:
    """API items → candidate rows (duration/views filters applied by caller)."""
    now = datetime.now(timezone.utc)
    rows = []
    for v in items:
        sn, st, cd = v.get("snippet", {}), v.get("statistics", {}), v.get("contentDetails", {})
        try:
            published = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        text = " ".join([sn.get("title", ""), sn.get("description", ""), " ".join(sn.get("tags", []) or [])])
        views = to_int(st.get("viewCount"))
        likes, comments = to_int(st.get("likeCount")), to_int(st.get("commentCount"))
        age = max((now - published).days, 1)
        rows.append({
            "video_id": v["id"], "url": f"https://www.youtube.com/watch?v={v['id']}",
            "title": sn.get("title", ""), "channel_id": sn.get("channelId", ""),
            "channel_title": sn.get("channelTitle", ""), "published_at": published.date().isoformat(),
            "age_days": age, "duration_s": parse_duration(cd.get("duration")), "views": views,
            "likes": likes, "comments": comments, "views_per_day": views / age,
            "engagement_rate": (likes + comments) / max(views, 1),
            "keywords": sorted(found.get(v["id"], set())),
            "relevant": is_relevant(text, category_terms, object_terms),
            "relevant_loose": is_relevant(text, category_terms, object_terms, loose=True),
        })
    return rows


def discover(yt: YouTube, keywords: list[str], category_terms: list[str], object_terms: list[str],
             progress=None, min_views: Optional[int] = None,
             days_back: Optional[int] = None) -> tuple[list[dict[str, Any]], dict[str, list[str]], bool]:
    """Search every keyword, fetch stats, filter to Shorts. Returns (rows, keyword→ids, stopped_on_budget)."""
    c = cfg()
    floor = int(min_views if min_views is not None else c["min_views"])
    found: dict[str, set[str]] = {}
    per_kw: dict[str, list[str]] = {}
    stopped = False
    regions = market()["regions"]
    for n_kw, kw in enumerate(keywords):
        try:
            ids = search_keyword(yt, kw, days_back, regions[n_kw % len(regions)])
        except QuotaBudgetReached as e:
            log.warning(f"{e} — stopping search early with {len(found)} videos")
            stopped = True
            break
        except RuntimeError as e:
            log.warning(f"search failed for {kw!r}: {e}")
            continue
        per_kw[kw] = ids
        for vid in ids:
            found.setdefault(vid, set()).add(kw)
        if progress:
            progress(f"searched “{kw}”: {len(ids)} videos")
    try:
        items = video_details(yt, found)
    except QuotaBudgetReached as e:
        log.warning(str(e))
        items, stopped = [], True
    rows = [r for r in to_rows(items, found, category_terms, object_terms)
            if 0 < r["duration_s"] <= int(c["max_duration_s"]) and r["views"] >= floor]
    return rows, per_kw, stopped
