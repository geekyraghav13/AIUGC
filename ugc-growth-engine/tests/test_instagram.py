"""Apify/Instagram mapping + spend guards (no network)."""
from datetime import datetime, timedelta, timezone

import pytest

from engine.research import instagram


def item(code="C1", views=12000, tag="upi", **kw):
    ts = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    base = {"shortCode": code, "url": f"https://www.instagram.com/reel/{code}/", "caption": "UPI kharcha track karo #paisa",
            "hashtags": ["paisa", "upi"], "ownerUsername": "creator1", "timestamp": ts, "videoDuration": 31.4,
            "videoPlayCount": views, "likesCount": 400, "commentsCount": 20, "displayUrl": "https://cdn/x.jpg",
            "inputUrl": f"https://www.instagram.com/explore/tags/{tag}/"}
    base.update(kw)
    return base


def test_to_hashtag():
    assert instagram.to_hashtag("UPI kharcha kaise dekhe") == "upikharchakaise"
    assert instagram.to_hashtag("सैलरी कहाँ गई") .startswith("सैलरी")


def test_reel_mapping_and_relevance():
    rows = instagram.to_rows([item(), item("C1"), item("C2", views=0, videoViewCount=5000, likesCount=-1),
                              {"error": "not_found"}], ["upi", "paisa"], [])
    assert [r["video_id"] for r in rows] == ["C1", "C2"]                      # dedupe + skip errors
    r = rows[0]
    assert r["platform"] == "instagram" and r["views"] == 12000 and r["duration_s"] == 31
    assert r["keywords"] == ["#upi"] and r["relevant"] and r["channel_id"] == "creator1"
    assert abs(r["engagement_rate"] - 420 / 12000) < 1e-9
    assert rows[1]["views"] == 5000 and rows[1]["likes"] == 0               # fallback view field, hidden likes (-1)


def test_monthly_budget_guard(monkeypatch):
    monkeypatch.setenv("APIFY_MONTHLY_BUDGET_USD", "1.0")
    monkeypatch.setattr(instagram, "spent_this_month_usd", lambda: 0.9)
    monkeypatch.setattr(instagram.cache, "get_json", lambda *a: None)
    with pytest.raises(instagram.ApifyBudgetReached):
        instagram.run_actor("apify~instagram-hashtag-scraper", {"hashtags": ["x"]}, max_charge_usd=0.2)


def test_gemini_budget_guard(monkeypatch):
    from engine import cost

    monkeypatch.setenv("GEMINI_DAILY_BUDGET_INR", "10")
    monkeypatch.setattr(cost, "spent_today_inr", lambda: 10.5)
    with pytest.raises(cost.BudgetExceeded):
        cost.check_budget()
    assert cost.gemini_cost_inr(1_000_000, 0) > 0
