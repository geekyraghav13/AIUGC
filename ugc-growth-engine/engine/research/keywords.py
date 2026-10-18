"""Step 1 — demand keywords: LLM problem-style seeds → YouTube autocomplete → (optional) Google Trends."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import pandas as pd
import requests
from pydantic import BaseModel, Field

from engine import cache, cost, llm
from engine.brief import Brief, brief_for_prompt
from engine.research import cfg
from engine.research.discover import is_relevant
from engine.settings import languages, log, market

AUTOCOMPLETE_URL = "https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl={hl}&gl={gl}&q={q}"
_last_ac = 0.0


class SeedKeyword(BaseModel):
    text: str
    language: str = ""


class SeedsOut(BaseModel):
    keywords: list[SeedKeyword] = Field(min_length=1)
    category_terms: list[str] = Field(default_factory=list)
    object_terms: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- seeds

def _mock_seeds(b: Brief, langs: list[str], n: int) -> dict[str, Any]:
    words = {
        "hinglish": ["kaise kare", "kya hai", "tips", "problem", "month end", "salary", "hindi me"],
        "hi": ["कैसे करें", "क्या है", "टिप्स", "समस्या", "महीने के आखिर", "सैलरी"],
        "ta": ["எப்படி", "என்ன", "டிப்ஸ்", "பிரச்சனை", "சம்பளம்"],
        "te": ["ఎలా", "ఏమిటి", "టిప్స్", "సమస్య", "జీతం"],
        "mr": ["कसे करावे", "काय आहे", "टिप्स", "समस्या", "पगार"],
        "bn": ["কিভাবে", "কি", "টিপস", "সমস্যা", "মাইনে"],
    }
    base = [f.split("(")[0].strip().lower() for f in b.key_features] + [b.category.lower(), "budget", "expense"]
    kws = []
    for lang in langs:
        for i in range(n):
            kws.append({"text": f"{base[i % len(base)].split(',')[0][:30]} {words.get(lang, words['hinglish'])[i % 5]}",
                        "language": lang})
    cat = sorted({w for f in b.key_features for w in f.lower().replace(",", " ").split() if len(w) > 3})[:15]
    return {"keywords": kws, "category_terms": cat + ["budget", "expense", "kharcha", "खर्च", "पैसे", "செலவு"],
            "object_terms": [], "hashtags": ["moneytips", "budgeting", "upi", "paisa", "savingmoney", "financetips"]}


def seed_keywords(b: Brief, langs: list[str], app_id: str) -> SeedsOut:
    n = int(cfg()["seeds_per_language"])
    prompt = llm.render_prompt("research_keywords", brief=brief_for_prompt(b), n=str(n),
                               languages={l: languages()[l]["name"] for l in langs})
    out: SeedsOut = llm.complete_json("research_keywords", prompt, SeedsOut, temperature=0.7, app_id=app_id,
                                      mock_payload=lambda: _mock_seeds(b, langs, n))
    out.keywords = [k for k in out.keywords if k.text.strip()]
    for k in out.keywords:
        k.text = " ".join(k.text.lower().split())
        if k.language not in langs:
            k.language = langs[0]
    out.category_terms = sorted({t.strip().lower() for t in out.category_terms if t.strip()})
    out.object_terms = sorted({t.strip().lower() for t in out.object_terms if t.strip()})
    out.hashtags = list(dict.fromkeys(h.strip().lstrip("#").lower() for h in out.hashtags if h.strip()))
    return out


# ---------------------------------------------------------------- autocomplete

def parse_suggestions(text: str) -> list[str]:
    """Parse the firefox-client autocomplete reply: ["query", ["s1", "s2", ...], ...]."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        return []
    out = []
    for s in data[1]:
        if isinstance(s, str) and s.strip():
            out.append(" ".join(s.lower().split()))
        elif isinstance(s, list) and s and isinstance(s[0], str):  # other client formats
            out.append(" ".join(s[0].lower().split()))
    return out


def hl_for(lang: str) -> str:
    return {"hinglish": "en", "english": "en"}.get(lang, lang)


def autocomplete(q: str, lang: str, offline: bool = False) -> list[str]:
    """YouTube search suggestions (unofficial endpoint): cached, throttled, silent on failure."""
    global _last_ac
    k = cache.key("yt_autocomplete", q, lang)
    hit = cache.get_json("autocomplete", k)
    if hit is not None and _fresh(hit.get("at"), float(cfg()["refresh_days"])):
        return hit["suggestions"]
    if offline:
        return [f"{q} {s}" for s in ("kaise", "tips", "2026")][: 1 + len(q) % 3]
    wait = _last_ac + float(cfg()["autocomplete_interval_s"]) - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_ac = time.time()
    try:
        r = requests.get(AUTOCOMPLETE_URL.format(hl=hl_for(lang), gl=market()["autocomplete_gl"], q=quote(q)), timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        sugg = parse_suggestions(r.content.decode("utf-8", errors="replace"))
    except requests.RequestException as e:
        log.debug(f"autocomplete skipped for {q!r}: {e}")
        return []
    cache.put_json("autocomplete", k, {"at": _now_iso(), "suggestions": sugg})
    return sugg


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh(at: Optional[str], days: float) -> bool:
    if not at:
        return False
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds() < days * 86400
    except ValueError:
        return False


# ---------------------------------------------------------------- trends (optional)

def trends_interest(queries: list[str]) -> dict[str, float]:
    """Mean Google Trends interest (geo IN, 90 days) per query. Optional: {} when pytrends is missing/blocked."""
    try:
        from pytrends.request import TrendReq  # type: ignore
    except ImportError:
        return {}
    out: dict[str, float] = {}
    try:
        tr = TrendReq(hl=market()["trends_hl"], tz=330, timeout=(5, 15), retries=1, backoff_factor=1)
        for i in range(0, len(queries), 5):
            batch = queries[i : i + 5]
            k = cache.key("trends", batch)
            hit = cache.get_json("trends", k)
            if hit is None:
                tr.build_payload(batch, geo=market()["trends_geo"], timeframe="today 3-m")
                df = tr.interest_over_time()
                hit = {q: float(df[q].mean()) if q in df else 0.0 for q in batch}
                cache.put_json("trends", k, hit)
                time.sleep(2)
            out.update(hit)
    except Exception as e:  # noqa: BLE001 — unofficial API, never a hard dependency
        log.info(f"pytrends unavailable ({type(e).__name__}); continuing without trend signal")
    return out


# ---------------------------------------------------------------- candidates + scores

def pct(s: pd.Series) -> pd.Series:
    return s.rank(pct=True) if s.notna().any() and s.nunique() > 1 else pd.Series(0.5, index=s.index)


def expand(seeds: SeedsOut, offline: bool, app_id: Optional[str] = None) -> pd.DataFrame:
    """Seeds + autocomplete suggestions → one row per candidate keyword with hit counts and relevance."""
    rows: dict[str, dict[str, Any]] = {}
    hits: dict[str, set[str]] = defaultdict(set)
    t0 = time.time()
    for s in seeds.keywords:
        rows.setdefault(s.text, {"text": s.text, "language": s.language, "source": "seed"})
        for sug in autocomplete(s.text, s.language, offline=offline):
            hits[sug].add(s.text)
            rows.setdefault(sug, {"text": sug, "language": s.language, "source": "autocomplete"})
    cost.log_step("research:autocomplete", "youtube-suggest", time.time() - t0, app_id=app_id,
                  units=len(seeds.keywords))
    df = pd.DataFrame(rows.values())
    # suggestions that appear for several seeds are strong demand signals; a seed that is itself suggested counts too
    df["autocomplete_hits"] = df["text"].map(lambda t: len(hits.get(t, ())))
    df["relevant"] = df["text"].map(lambda t: is_relevant(t, seeds.category_terms, []))
    df.loc[df["source"] == "seed", "relevant"] = True  # seeds are on-topic by construction
    return df


def preliminary_score(df: pd.DataFrame, trends: dict[str, float]) -> pd.Series:
    t = df["text"].map(trends) if trends else pd.Series(float("nan"), index=df.index)
    if t.notna().any():
        return 0.7 * pct(df["autocomplete_hits"]) + 0.3 * pct(t.fillna(0))
    return pct(df["autocomplete_hits"])


def select_for_search(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """Top-k relevant keywords by preliminary score, round-robin across languages for coverage."""
    pool = df[df["relevant"]].sort_values(["prelim", "autocomplete_hits"], ascending=False)
    by_lang = {l: list(g.index) for l, g in pool.groupby("language", sort=False)}
    chosen: list[int] = []
    while len(chosen) < k and any(by_lang.values()):
        for l in list(by_lang):
            if by_lang[l] and len(chosen) < k:
                chosen.append(by_lang[l].pop(0))
    return df.loc[chosen]


def demand_scores(df: pd.DataFrame) -> pd.Series:
    """Final 0–1 demand: autocomplete frequency + trends (if any) + total views of the top-10 results."""
    parts, weights = [pct(df["autocomplete_hits"])], [0.35]
    if df["trends_interest"].notna().any():
        parts.append(pct(df["trends_interest"].fillna(0)))
        weights.append(0.15)
    if df["top10_views"].notna().any():
        parts.append(pct(df["top10_views"].fillna(0)))
        weights.append(0.5)
    total = sum(weights)
    score = sum(w / total * p for w, p in zip(weights, parts))
    # keywords we never searched can't prove views: cap them below searched ones
    return (score * df["searched"].map({True: 1.0, False: 0.6})).round(3)
