import pandas as pd

from engine.insights_perf import dimension_table, numbers_ok, prob_best


def df(rows):
    base = {"impressions": 2000, "views_3s": 400, "avg_watch_pct": 40.0, "likes": 10, "comments": 1, "shares": 1,
            "saves": 1, "clicks": 30, "spend_inr": 0.0}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_needs_more_data_below_threshold():
    t = dimension_table(df([{"language": "ta", "views": 1000, "installs": 20},
                            {"language": "hi", "views": 1000, "installs": 5}]), ["language"], "installs_per_1k_views")
    assert set(t["status"]) == {"needs more data"}


def test_winner_with_enough_data():
    rows = [{"language": "ta", "views": 1000, "installs": 20}] * 3 + [{"language": "hi", "views": 1000, "installs": 5}] * 3
    t = dimension_table(df(rows), ["language"], "installs_per_1k_views").set_index("value")
    assert t.loc["ta", "status"] == "winner"
    assert t.loc["ta", "prob_best"] > 0.95
    assert abs(t.loc["ta", "lift"] - (20 / 12.5 - 1)) < 1e-9
    assert t.loc["hi", "status"] == "underperformer"


def test_min_views_threshold(monkeypatch):
    rows = [{"language": "ta", "views": 100, "installs": 3}] * 3 + [{"language": "hi", "views": 100, "installs": 1}] * 3
    t = dimension_table(df(rows), ["language"], "installs_per_1k_views")
    assert set(t["status"]) == {"needs more data"}   # 300 views < 500


def test_prob_best_sums_to_one():
    p = prob_best({"a": (10, 1000), "b": (12, 1000), "c": (30, 1000)})
    assert abs(sum(p.values()) - 1) < 1e-9 and max(p, key=p.get) == "c"


def test_narrative_number_guard():
    allowed = {2.1, 82.0, 15.28}
    assert numbers_ok("Tamil hooks get 2.1× installs (82% probability best)", allowed)
    assert not numbers_ok("Tamil hooks get 3.4× installs", allowed)
    assert numbers_ok("Top 3 creatives were all Tamil", allowed)   # small ordinals allowed
