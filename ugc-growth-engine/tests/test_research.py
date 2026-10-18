"""Phase 1.5 (D8) tests: autocomplete parser, outlier/baseline maths, relevance filter, tag validation,
prior initialisation, playbook number guard, cache (0 re-analysis calls), copy detection, stale-data purge."""
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from engine.research import analyze, keywords, patterns, playbook, scoring
from engine.research.discover import is_relevant, parse_duration


# ---------------------------------------------------------------- keywords

def test_autocomplete_parser():
    raw = '["upi kharcha",["upi kharcha kaise dekhe","UPI Kharcha  Tracker",["nested", 1]],[],{"k":1}]'
    assert keywords.parse_suggestions(raw) == ["upi kharcha kaise dekhe", "upi kharcha tracker", "nested"]
    assert keywords.parse_suggestions('["q",[]]') == []
    assert keywords.parse_suggestions("<html>blocked</html>") == []
    assert keywords.parse_suggestions('{"not": "a list"}') == []
    assert keywords.hl_for("hinglish") == "en" and keywords.hl_for("ta") == "ta"


def test_demand_score_prefers_searched_and_viewed():
    df = pd.DataFrame({"autocomplete_hits": [5, 1, 5], "trends_interest": [None] * 3,
                       "top10_views": [900000, 1000, None], "searched": [True, True, False]})
    s = keywords.demand_scores(df)
    assert s[0] > s[1] and s[0] > s[2] and all(0 <= x <= 1 for x in s)


# ---------------------------------------------------------------- discovery + scoring

def test_relevance_filter_latin_and_devanagari():
    cat, obj = ["insurance", "bima", "बीमा", "idv"], ["car", "bike", "गाड़ी"]
    assert is_relevant("Car insurance renewal tips", cat, obj)
    assert is_relevant("गाड़ी का बीमा कैसे करें", cat, obj)
    assert is_relevant("IDV kya hota hai? bike owners", cat, obj)
    assert not is_relevant("Scar tissue reinsurance", cat, obj)        # word boundary: no 'car' inside 'scar'
    assert not is_relevant("car wash hacks", cat, obj)                 # object but no category
    assert is_relevant("बीमा ही बीमा", ["बीमा"], [])                     # object terms optional


def test_iso_duration():
    assert parse_duration("PT1M5S") == 65 and parse_duration("PT45S") == 45
    assert parse_duration("PT1H") == 3600 and parse_duration("") == 0 and parse_duration("bogus") == 0


def test_baseline_and_outlier_maths():
    shorts = [{"statistics": {"viewCount": str(v)}, "contentDetails": {"duration": "PT30S"}}
              for v in (1000, 2000, 3000, 4000, 5000)]
    longs = [{"statistics": {"viewCount": "999999"}, "contentDetails": {"duration": "PT20M"}}]
    assert scoring.recent_median(shorts + longs, 180) == (3000.0, 5)          # Shorts only when ≥ 5
    assert scoring.recent_median(shorts[:2] + longs, 180) == (2000.0, 3)      # else all uploads
    assert scoring.baseline({"median_recent_views": 3000, "baseline_sample": 5, "channel_avg_views": 10}, 1) == 3000
    assert scoring.baseline({"median_recent_views": 3000, "baseline_sample": 4, "channel_avg_views": 800}, 1) == 800
    assert scoring.baseline({"median_recent_views": None, "baseline_sample": 0, "channel_avg_views": 50}, 1) == 200
    df = pd.DataFrame({"views": [50000, 500000, 9000], "median_recent_views": [2000, 1_000_000, 3000],
                       "baseline_sample": [10, 10, 10], "channel_avg_views": [1, 1, 1],
                       "views_per_day": [100, 5000, 10], "engagement_rate": [0.05, 0.02, 0.01],
                       "relevant": [True, True, True], "channel_id": ["a", "b", "a"]})
    s = scoring.score_videos(df)
    assert list(s["outlier_score"]) == [25.0, 3.0, 0.5] or s.iloc[0]["outlier_score"] == 25.0
    top = scoring.select_top(s, 3, max_per_channel=1)
    assert len(top) == 2 and set(top["channel_id"]) == {"a", "b"}


# ---------------------------------------------------------------- analysis

def test_tag_validation_and_fallback_to_other():
    a, bad = analyze.validate({"hook_type": "Relatable Pain", "format": "problem-solution", "language": "Hinglish",
                               "production_style": "talking head", "closest_creator_style": "bestie_narrator",
                               "emotional_trigger": "save_money", "cta_type": "follow", "length_bucket": "short",
                               "is_relevant": "true", "structure": "hook → tip → cta"})
    assert bad == [] and a["hook_type"] == "relatable_pain" and a["format"] == "problem_solution"
    assert a["is_relevant"] is True and a["structure"] == ["hook", "tip", "cta"]
    a2, bad2 = analyze.validate({**a, "hook_type": "shocking_number", "format": "vlog"})
    assert set(bad2) == {"hook_type", "format"}
    fixed = analyze.force_other(a2, bad2)
    assert fixed["hook_type"] == "other" and analyze.validate(fixed)[1] == []


def test_analysis_is_cached_no_second_call(monkeypatch, tmp_path):
    import engine.cache as cache_mod

    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("RESEARCH_GEMINI_MODEL", "gemini-test")
    calls = []

    def fake(url, prompt, model, retries=3):
        calls.append(url)
        return {"is_relevant": True, "hook_type": "question", "format": "pov", "language": "hi",
                "production_style": "talking_head", "closest_creator_style": "storytime",
                "emotional_trigger": "curiosity", "cta_type": "follow", "length_bucket": "short"}

    monkeypatch.setattr(analyze, "_gemini_video", fake)
    monkeypatch.setattr(analyze.cost, "log_step", lambda *a, **k: None)
    v = {"video_id": "abc123", "url": "https://www.youtube.com/watch?v=abc123", "title": "t", "channel_title": "c",
         "views": 10000, "outlier_score": 3.0, "duration_s": 40}
    a1, _, called1 = analyze.analyze_video(v, "app", "app1", "youtube")
    a2, _, called2 = analyze.analyze_video(v, "app", "app1", "youtube")
    assert called1 and not called2 and len(calls) == 1 and a1 == a2
    assert a1["length_bucket"] == "long"   # measured from duration, not guessed


# ---------------------------------------------------------------- patterns + playbook

def analysed_frame():
    rows = []
    for i in range(10):
        rows.append({"market_video_id": i, "url": f"u{i}", "market_score": 1.0 if i < 6 else 0.3,
                     "outlier_score": 5.0 if i < 6 else 1.0, "format": "pov" if i < 6 else ("myth_vs_fact" if i < 8 else "before_after"),
                     "hook_type": "question", "language": "hinglish", "closest_creator_style": "storytime",
                     "production_style": "talking_head", "emotional_trigger": "curiosity", "cta_type": "follow",
                     "length_bucket": "short", "uses_numbers": i % 2 == 0, "shows_app_or_screen": False,
                     "topic_angle": "salary gone by month end" if i < 5 else "split rent with flatmates"})
    return pd.DataFrame(rows)


def test_patterns_share_weighted_share_lift_and_weak_signal():
    t = patterns.compute(analysed_frame())["format"].set_index("value")
    assert t.loc["pov", "videos"] == 6 and t.loc["pov", "share"] == 0.6
    assert abs(t.loc["pov", "weighted_share"] - 6 / 7.2) < 1e-9
    assert abs(t.loc["pov", "lift"] - (5 / 3.4 - 1)) < 1e-9
    assert t.loc["pov", "signal"] == "pattern"
    assert t.loc["myth_vs_fact", "signal"] == "weak signal" and t.loc["before_after", "signal"] == "weak signal"


def test_playbook_numbers_come_from_data():
    from engine.insights_perf import _allowed_numbers, numbers_ok

    tables = patterns.compute(analysed_frame())
    f = playbook.facts(tables)
    allowed = _allowed_numbers(f)
    pov = next(r for r in f["patterns"]["format"] if r["value"] == "pov")
    good = f"POV videos take {pov['weighted_share']} of weighted share at {pov['avg_outlier']} avg outlier."
    assert numbers_ok(good, allowed)
    assert not numbers_ok("POV videos get 73% more installs.", allowed)


def test_hook_templates_must_not_copy():
    hooks = ["Salary aate hi khatam ho jaati hai?", "3 UPI habits jo paisa khaate hain"]
    assert playbook.copies_market_hook("Salary aate hi khatam ho jaati hai yaar", hooks)
    assert not playbook.copies_market_hook("___ ke baad bhi ___ kyun?", hooks)


# ---------------------------------------------------------------- integration

def test_market_prior_initialisation_and_override():
    from engine import optimizer
    from engine.research.market import MarketContext

    pat = pd.DataFrame([
        {"id": 1, "dimension": "format", "value": "pov", "weighted_share": 0.7, "signal": "pattern", "lift": 0.1},
        {"id": 2, "dimension": "format", "value": "myth_vs_fact", "weighted_share": 0.2, "signal": "pattern", "lift": 0.0},
        {"id": 3, "dimension": "format", "value": "before_after", "weighted_share": 0.1, "signal": "weak signal", "lift": 0.0},
    ])
    ctx = MarketContext(playbook={"recipes": []}, patterns=pat)
    cands = {"language": ["hi"], "hook_type": ["question"], "format": ["pov", "myth_vs_fact", "before_after"],
             "creator_style": ["storytime"], "message": ["m1"]}
    m0 = optimizer.EffectModel(pd.DataFrame(), "installs_per_1k_views", cands, market=ctx)
    assert m0.favourite("format") == "pov"                                  # priors reflect the market
    assert ctx.prior_score({"format": "pov"}) > 50 > ctx.prior_score({"format": "myth_vs_fact"}) - 1
    # our data says myth_vs_fact is far better → overrides the capped market prior
    rows = []
    for i in range(16):
        fmt = "myth_vs_fact" if i % 2 else "pov"
        rows.append({"language": "hi", "hook_type": "question", "format": fmt, "creator_style": "storytime",
                     "message": "m1", "views": 3000, "installs": 60 if fmt == "myth_vs_fact" else 15,
                     "clicks": 100, "likes": 0, "comments": 0, "shares": 0, "saves": 0, "avg_watch_pct": 40.0,
                     "views_3s": 1000, "impressions": 5000})
    m1 = optimizer.EffectModel(pd.DataFrame(rows), "installs_per_1k_views", cands, market=ctx)
    assert m1.favourite("format") == "myth_vs_fact"


def test_concepts_enforce_market_share():
    from engine.concepts import ConceptOut, enforce_market_share

    cs = [ConceptOut(title=f"c{i}", format="day_in_life", market_evidence=[99] if i == 0 else []) for i in range(10)]
    proven = [{"kind": "format", "ids": [7], "format": "pov"}, {"kind": "topic_angle", "ids": [8], "topic_angle": "x"}]
    enforce_market_share(cs, proven, 0.6)
    assert sum(1 for c in cs if c.market_evidence) == 6            # invalid id 99 dropped, 6 re-based
    assert all(c.format == "pov" for c in cs if c.market_evidence)


def test_stale_purge(tmp_db, tmp_path, monkeypatch):
    from engine.db import MarketAnalysis, MarketVideo, ResearchRun, save, session
    from engine.research import run as rr
    from sqlmodel import select

    monkeypatch.setattr(rr, "CACHE_DIR", tmp_path)
    run = ResearchRun(app_id="a")
    save(run)
    old = MarketVideo(run_id=run.id, video_id="old", fetched_at=datetime.now(timezone.utc) - timedelta(days=40))
    new = MarketVideo(run_id=run.id, video_id="new")
    save(old, new)
    save(MarketAnalysis(market_video_id=old.id, analysis={"x": 1}), MarketAnalysis(market_video_id=new.id, analysis={}))
    f = tmp_path / "youtube" / "ab" / "x.json"
    f.parent.mkdir(parents=True)
    f.write_text("{}")
    past = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(f, (past, past))
    res = rr.purge_stale(30)
    assert res == {"videos": 1, "analyses": 1, "cache_files": 1}
    with session() as s:
        assert [v.video_id for v in s.exec(select(MarketVideo)).all()] == ["new"]


def test_indic_tokenizer_and_copy_check_regression():
    """\\w+ used to split Tamil/Devanagari words at vowel signs → false copy flags and broken dedupe."""
    from engine.localize import words
    from engine.variations import similarity

    assert words("தினமும் UPI ல சின்ன செலவு!") == ["தினமும்", "upi", "ல", "சின்ன", "செலவு"]
    assert words("सैलरी आई और दस दिन में खत्म?")[-1] == "खत्म"
    assert not playbook.copies_market_hook("தினமும் UPI ல சின்ன சின்ன செலவு", ["3 UPI habits jo paisa khaate hain"])
    assert playbook.copies_market_hook("சம்பளம் வந்து பத்து நாள்ல காலியா யாரு", ["சம்பளம் வந்து பத்து நாள்ல காலியா?"])
    assert similarity("सैलरी आई और दस दिन में खत्म?", "सैलरी आई और दस दिन में खत्म!") == 1.0
    assert similarity("சம்பளம் வந்து காலியா?", "செலவு எங்க போச்சு?") == 0.0


def test_app_lock_blocks_other_process_but_is_reentrant(tmp_path, monkeypatch):
    import subprocess
    import sys

    import engine.locks as locks

    monkeypatch.setattr(locks, "CACHE_DIR", tmp_path)
    with locks.app_lock("appx", "test"):
        with locks.app_lock("appx", "nested"):   # same process: re-entrant
            pass
        code = ("import engine.locks as L, pathlib; L.CACHE_DIR = pathlib.Path(r'%s')\n"
                "try:\n    L.app_lock('appx').__enter__(); print('GOT')\nexcept L.AppBusy: print('BUSY')") % tmp_path
        root = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
        run = lambda: subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=root).stdout.strip()  # noqa: E731
        assert run() == "BUSY"
    assert run() == "GOT"


def test_copy_check_ignores_our_own_app_name():
    hooks = ["Motor Insurance Calculator", "IDV kya hota hai? 90% log galat samajhte hain"]
    line = "ஆனா இப்போ Motor Insurance Calculator மூலமா பிரீமியம் பாக்கலாம்"
    assert playbook.copies_market_hook(line, hooks)                                        # naive: false positive
    assert not playbook.copies_market_hook(line, hooks, ignore="Motor Insurance Calculator")
    assert playbook.copies_market_hook("IDV kya hota hai? 90% log galat samajhte hain yaar", hooks,
                                       ignore="Motor Insurance Calculator")                  # real copy still caught
