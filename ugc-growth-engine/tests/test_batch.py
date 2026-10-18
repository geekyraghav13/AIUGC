"""Iterations: 10 scripts, languages + modes from research, owner approval with a credit ceiling."""
import pandas as pd


def test_split_is_exact_and_proportional():
    from engine.batch import split

    assert split({"hi": .6, "hinglish": .3, "ta": .1}, 10) == {"hi": 6, "hinglish": 3, "ta": 1}
    assert sum(split({"a": 1, "b": 1, "c": 1}, 10).values()) == 10
    assert split({"a": 0.97, "b": 0.03}, 10) == {"a": 10}


def test_languages_come_from_research_any_supported_language(monkeypatch):
    from engine import batch
    from engine.research import market

    pat = pd.DataFrame([
        {"id": 1, "dimension": "language", "value": "hi", "videos": 12, "weighted_share": .55, "signal": "pattern"},
        {"id": 2, "dimension": "language", "value": "te", "videos": 5, "weighted_share": .25, "signal": "pattern"},
        {"id": 3, "dimension": "language", "value": "english", "videos": 6, "weighted_share": .2, "signal": "pattern"},
    ])
    monkeypatch.setattr(market, "context", lambda app_id: market.MarketContext(playbook={"x": 1}, patterns=pat))
    shares, src = batch.language_shares("any")
    assert src == "market research" and set(shares) == {"hi", "te"}   # Telugu allowed even if not in the brief


def test_build_writes_ten_scripts_and_approval_sets_a_ceiling(tmp_db, brief, tmp_path, monkeypatch):
    from engine import batch, modes
    from engine.brief import register_app
    import engine.brief as brief_mod
    import engine.visuals as visuals_mod

    monkeypatch.setattr(brief_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(visuals_mod, "DATA_DIR", tmp_path / "data")
    brief.app_id = "iter_app"
    app = register_app(brief, enrich=False)
    b = batch.build(app.id, 10)
    rows = batch.creatives_of(b)
    assert b.status == "scripts_ready" and len(rows) == 10
    assert all(c.script and c.visual_mode in modes.MODES for c in rows)
    assert all(c.status == "draft" and not c.selected_for_render for c in rows)   # nothing filmed before approval
    batch.decide(rows[0].id, False)                                            # owner rejects one
    b = batch.approve(b.id)
    chosen = [c for c in batch.creatives_of(b) if c.status == "approved"]
    assert len(chosen) == 9 and all(c.selected_for_render for c in chosen)
    assert b.credits_approved == round(sum(modes.mode_cost(c.visual_mode) for c in chosen))


def test_api_fulfilment_chains_jobs_and_never_passes_the_ceiling(tmp_db, monkeypatch, tmp_path):
    from engine import magnific
    from engine.db import ProductionRun, save
    from engine.production import assets
    import engine.production.assets as amod

    calls = []

    def fake_run(kind, body):
        calls.append((kind, body))
        f = tmp_path / f"{len(calls)}.bin"
        f.write_bytes(b"x")
        return str(f), f"t{len(calls)}"

    monkeypatch.setattr(magnific, "run", fake_run)
    monkeypatch.setattr(amod, "run_dir", lambda run: tmp_path)
    monkeypatch.setattr(amod, "import_file", lambda run, rid, src, credits=None, creation=None: next(
        r.update(status="done", creation=src) or r for r in run.asset_requests if r["id"] == rid))
    reqs = [{"id": "voice_s0", "kind": "voice", "scene": 0, "status": "done", "credits": 16,   # recorded via MCP
             "creation": "https://cdn/voice.mp3",
             "mcp": {"tool": "audio_tts", "arguments": {"text": "a", "voiceId": 808}}},
            {"id": "face_s0", "kind": "image", "scene": 0, "status": "pending", "credits": 50,
             "mcp": {"tool": "images_generate", "arguments": {"prompt": "p", "aspectRatio": "9:16"}}},
            {"id": "talk_s0", "kind": "talking", "scene": 0, "status": "pending", "credits": 540,
             "depends_on": ["voice_s0", "face_s0"],
             "mcp": {"tool": "video_speak", "arguments": {"imageUrl": "{face_s0.creation}",
                                                           "audioUrl": "{voice_s0.creation}"}}}]
    run = ProductionRun(app_id="a", creative_id=1, plan={"scenes": [{}]}, asset_requests=reqs)
    save(run)
    res = assets.fulfil_api(run, credit_limit=100)          # the face fits; the 540-credit talk does not
    assert res["spent"] == 50 and res["stopped"] == 1 and [k for k, _ in calls] == ["image"]
    res = assets.fulfil_api(run, credit_limit=1000)
    talk = calls[-1]
    assert talk[0] == "talking" and talk[1]["image_url"].endswith("1.bin") and talk[1]["audio_url"] == "https://cdn/voice.mp3"
