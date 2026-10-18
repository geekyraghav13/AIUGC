from engine import simulator
from engine.db import Creative


def creative(i=1, **tags):
    base = dict(id=i, app_id="app", language="ta", hook_type="question", format="pov", creator_style="meme_text",
                message="msg_1", length_bucket="medium")
    base.update(tags)
    return Creative(**base)


def test_simulator_deterministic_with_seed():
    a = simulator.simulate_creative(creative(), seed=42)
    b = simulator.simulate_creative(creative(), seed=42)
    c = simulator.simulate_creative(creative(), seed=43)
    assert a == b
    assert a != c


def test_simulator_funnel_is_consistent():
    m = simulator.simulate_creative(creative(7), seed=1)
    assert m["impressions"] >= m["views"] >= m["views_3s"] >= m["clicks"] >= m["installs"] >= m["d1_retained"]
    assert 0 < m["avg_watch_pct"] < 100 and m["spend_inr"] == 0


def test_true_effects_are_hidden_but_stable():
    e1 = simulator.true_effect("app", "language", "ta", seed=5)
    assert e1 == simulator.true_effect("app", "language", "ta", seed=5)
    assert e1 != simulator.true_effect("other_app", "language", "ta", seed=5)


def test_allocation_split_and_learning(tmp_db, brief, tmp_path, monkeypatch):
    """Optimizer: 70/30 split, and exploit children link to top-performing parents."""
    from engine import understand, variations, optimizer, loop
    from engine.brief import register_app

    import engine.brief as brief_mod
    import engine.visuals as visuals_mod

    monkeypatch.setattr(optimizer, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(brief_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(visuals_mod, "DATA_DIR", tmp_path / "data")
    brief.app_id = "test_app"

    app = register_app(brief, enrich=False)
    understand.run(app.id)
    variations.run_generation_one(app.id, scripted=24, render_count=12, use_llm_compliance=False)
    loop.publish_without_render(app.id, 1)
    simulator.simulate_generation(app.id, 1, include_unrendered=True, seed=3)
    slots, alloc = optimizer.plan_slots(app.id, size=10, explore_ratio=0.3, seed=1)
    assert alloc["exploit"] == 7 and alloc["explore"] == 3
    assert len(slots) == 10
    exploit = [s for s in slots if s["origin"] == "exploit"]
    assert all(s["parent_creative_id"] in alloc["parents"] for s in exploit if s.get("parent_creative_id"))
    assert any(s.get("parent_creative_id") for s in exploit)

    res = optimizer.generate_next_batch(app.id, size=10, use_llm_compliance=False)
    kids = [c for c in tmp_db.creatives(app.id, 2) if c.parent_creative_id]
    assert res["generation"] == 2 and kids


def test_reselection_keeps_previously_selected_picks(tmp_db, brief, tmp_path, monkeypatch):
    """Regression: re-picking a smaller set used to drop picks that were already selected."""
    import engine.brief as brief_mod
    import engine.visuals as visuals_mod
    from engine import understand, variations
    from engine.brief import register_app

    monkeypatch.setattr(brief_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(visuals_mod, "DATA_DIR", tmp_path / "data")
    brief.app_id = "sel_app"
    app = register_app(brief, enrich=False)
    understand.run(app.id)
    variations.run_generation_one(app.id, scripted=18, render_count=12, use_llm_compliance=False)
    chosen = variations.select_for_render(app.id, 1, 5)
    in_db = [c.id for c in tmp_db.creatives(app.id, 1, selected_for_render=True)]
    assert sorted(in_db) == sorted(c.id for c in chosen) and len(in_db) == 5
