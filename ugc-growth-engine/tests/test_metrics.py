import numpy as np
import pandas as pd

from engine.metrics import add_derived, primary_metric_name, totals


def frame(installs=(10, None)):
    return pd.DataFrame({
        "impressions": [1000, 2000], "views": [500, 1000], "views_3s": [200, 300], "avg_watch_pct": [40.0, 30.0],
        "likes": [20, 10], "comments": [2, 1], "shares": [3, 1], "saves": [5, 3], "clicks": [25, 20],
        "installs": list(installs), "spend_inr": [0.0, 0.0], "d1_retained": [4, None], "d7_retained": [2, None],
    }).astype({"installs": "float"})


def test_derived_metrics():
    d = add_derived(frame())
    r = d.iloc[0]
    assert r.hook_rate == 0.2
    assert r.engagement_rate == (20 + 2 + 3 + 5) / 500
    assert r.ctr == 25 / 500
    assert r.cvr == 10 / 25
    assert r.installs_per_1k_views == 20.0
    assert np.isnan(r.cpi) or r.cpi == 0.0
    assert r.d1_rate == 0.4
    assert np.isnan(d.iloc[1].installs_per_1k_views)


def test_totals_are_ratio_of_sums():
    t = totals(add_derived(frame((10, 30))))
    assert t["views"] == 1500
    assert abs(t["ctr"] - 45 / 1500) < 1e-9
    assert abs(t["installs_per_1k_views"] - 40 * 1000 / 1500) < 1e-9
    assert abs(t["avg_watch_pct"] - (40 * 500 + 30 * 1000) / 1500) < 1e-9


def test_primary_metric_fallback(monkeypatch):
    monkeypatch.setenv("PRIMARY_METRIC", "installs_per_1k_views")
    d = add_derived(frame((None, None)))
    assert primary_metric_name(d) == "ctr"
    assert primary_metric_name(add_derived(frame())) == "installs_per_1k_views"
