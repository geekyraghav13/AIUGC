"""Production crew: director plan, Magnific asset budget, fix routing, QC verdict, and the full
director → editor → reviewers → QC → assets → v2 → owner flow (Remotion replaced by a tiny real MP4)."""
import json
import subprocess
from pathlib import Path

import pytest

from engine.production.common import Issue


@pytest.fixture()
def crew_env(tmp_db, brief, tmp_path, monkeypatch):
    """A registered app with one scripted Hindi creative; outputs go to tmp."""
    from engine import understand, variations
    from engine.brief import register_app
    import engine.brief as brief_mod
    import engine.visuals as visuals_mod
    import engine.production.common as common
    import engine.production.pipeline as pipeline

    for mod in (common, pipeline):
        monkeypatch.setattr(mod, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(brief_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(visuals_mod, "DATA_DIR", tmp_path / "data")
    brief.app_id = "crew_app"
    brief.languages = ["hi"]
    app = register_app(brief, enrich=False)
    understand.run(app.id)
    variations.run_generation_one(app.id, languages=["hi"], styles=["bestie_narrator"], n_concepts=2,
                                  hooks_per_concept=2, scripted=2, render_count=1, use_llm_compliance=False)
    c = next(c for c in tmp_db.creatives(app.id, 1) if c.script)
    c.visual_mode = "faceless"          # the mode-specific tests set their own
    tmp_db.save(c)
    return {"app": app, "creative": c, "tmp": tmp_path}


def _new_run(c):
    from engine.db import ProductionRun, save
    from engine.production import director

    run = ProductionRun(app_id=c.app_id, creative_id=c.id)
    save(run)
    director.direct(run)
    return run


def test_director_plans_every_scene_and_casts_a_voice(crew_env):
    from engine.production import assets

    c = crew_env["creative"]
    run = _new_run(c)
    assert len(run.plan["scenes"]) == len(c.script["scenes"])
    voice = [r for r in run.asset_requests if r["kind"] == "voice"]
    assert len(voice) == len(c.script["scenes"])                      # natural voice for every line
    assert all(r["mcp"]["arguments"]["voiceId"] == assets.voice_id_for("hi", c.creator_style) for r in voice)
    assert run.credits_estimate <= assets.mcfg()["per_video_credit_cap"]


def test_budget_cap_blocks_magnific_when_month_is_spent(crew_env):
    from engine import cost
    from engine.production import assets

    cost.log_step("magnific:video_generate", "magnific", 0, units=assets.mcfg()["monthly_credit_cap"])
    assert assets.budget_left() <= 0
    run = _new_run(crew_env["creative"])
    assert run.asset_requests == []                                   # falls back to free voice / stock


def test_fixes_are_applied_to_the_plan(crew_env):
    from engine.production import editor

    run = _new_run(crew_env["creative"])
    run.plan["scenes"][1]["voice_file"] = "/tmp/old.mp3"
    assert editor.apply_fix(run, {"type": "set_text", "field": "voiceover", "value": "नया वाक्य"}, 1) == ""
    assert run.plan["scenes"][1]["voiceover"] == "नया वाक्य"
    assert run.plan["scenes"][1]["voice_file"] is None                 # changed line → re-record
    assert editor.apply_fix(run, {"type": "move_text", "band": "top"}, 2) == ""
    assert run.plan["scenes"][2]["text_band"] == "top"
    editor.apply_fix(run, {"type": "move_text", "band": "lower"}, 3)
    editor.apply_fix(run, {"type": "move_text", "band": "lower"}, 3)   # asked again → it was still in the way
    assert run.plan["scenes"][3]["text_band"] == "top"
    assert editor.apply_fix(run, {"type": "set_visual", "shot": "stock", "query_en": "kirana shop"}, 0) == ""
    assert run.plan["scenes"][0]["query_en"] == "kirana shop"
    assert editor.apply_fix(run, {"type": "set_mix", "music_volume": 5}, None) == ""
    assert run.plan["mix"]["music_volume"] == 0.6                      # clamped
    assert editor.apply_fix(run, {"type": "none"}, 0) == "needs a human decision"


def test_persist_saves_in_place_json_edits(crew_env):
    from engine.production.common import get_run, persist

    run = _new_run(crew_env["creative"])
    run.plan["scenes"][0]["on_screen_text"] = "EDITED"
    persist(run)
    assert get_run(run.id).plan["scenes"][0]["on_screen_text"] == "EDITED"


def test_qc_dedupes_and_scores():
    from engine.production import qc

    a = Issue(category="spelling", severity="minor", scene=1, issue="x", fix={"type": "set_text", "field": "voiceover"})
    b = Issue(category="spelling", severity="major", scene=1, issue="y", fix={"type": "set_text", "field": "voiceover"})
    c = Issue(category="level", severity="blocker", issue="z", fix={"type": "set_mix"})
    out = qc.dedupe({"text": [a], "brand": [b], "audio": [c]})
    assert [i.severity for _, i in out] == ["blocker", "major"]
    assert qc.score(out) == 100 - 25 - 10


def test_scene_inside_fix_is_lifted_and_issues_dont_collapse():
    from engine.production import qc

    issues = [Issue(category="relevance", severity="major", issue=f"scene {n} off", fix={"type": "set_visual", "scene": n})
              for n in (1, 2, 3)]
    assert [i.scene for i in issues] == [1, 2, 3]
    assert len(qc.dedupe({"visual": issues})) == 3


def test_stock_failing_twice_escalates_to_a_magnific_image(crew_env):
    from engine.db import ReviewTask, save
    from engine.production import editor, assets

    run = _new_run(crew_env["creative"])
    first = ReviewTask(run_id=run.id, version=1, raised_by="visual", assigned_to="director", category="relevance",
                       scene=1, issue="off", fix={"type": "set_visual", "shot": "stock", "query_en": "a"}, status="fixed")
    again = ReviewTask(run_id=run.id, version=2, raised_by="visual", assigned_to="director", category="relevance",
                       scene=1, issue="still off", fix={"type": "set_visual", "shot": "stock",
                                                         "query_en": "shopkeeper writing in ledger"}, status="open")
    save(first, again)
    editor.apply_tasks(run, [again])
    img = [r for r in assets.pending(run) if r["kind"] == "image"]
    assert img and img[0]["scene"] == 1 and "ledger" in img[0]["mcp"]["arguments"]["prompt"]
    assert img[0]["mcp"]["tool"] == "images_generate" and img[0]["mcp"]["arguments"]["aspectRatio"] == "9:16"


def test_issue_timecode_parses_mm_ss():
    assert Issue(issue="x", timecode="00:08.5").timecode == 8.5
    assert Issue(issue="x", severity="LOUD").severity == "major"


def test_caption_words_come_from_the_script_not_the_transcript():
    from engine.tts import align_script_words

    heard = [{"start": 0.1, "end": 0.4, "word": "nay"}, {"start": 0.5, "end": 0.9, "word": "vakya"}]
    out = align_script_words("नया वाक्य", heard, 1.0)
    assert [w["word"] for w in out] == ["नया", "वाक्य"] and out[1]["start"] == 0.5
    spread = align_script_words("एक दो तीन", heard, 1.0)
    assert spread[0]["start"] == 0.1 and abs(spread[-1]["end"] - 0.9) < 1e-6


def _fake_render(tmp: Path):
    """Stand-in for Remotion: a real 20 s 1080×1920 MP4 at −14 LUFS + the props the reviewers read."""
    from engine.settings import ffmpeg_bin

    base = tmp / "base.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x224466:s=1080x1920:r=30:d=20",
                    "-f", "lavfi", "-i", "sine=f=220:sample_rate=44100:d=20", "-af", "volume=8dB", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "40", "-c:a", "aac", "-shortest", str(base)], check=True)

    def render(creative_id, plan=None, out_dir=None, register=True):
        out_dir = Path(out_dir)
        (out_dir / "video.mp4").write_bytes(base.read_bytes())
        (out_dir / "thumbnail.jpg").write_bytes(b"")
        n = len(plan["scenes"])
        durs = [18.0 / n] * n
        lines, t = [], 0.0
        for i, s in enumerate(plan["scenes"]):
            ws = s["voiceover"].split()
            step = durs[i] / max(1, len(ws))
            lines.append({"start": t, "end": t + durs[i], "look": {}, "words": [
                {"text": w, "start": t + k * step, "end": t + (k + 1) * step} for k, w in enumerate(ws)]})
            t += durs[i]
        props = {"fps": 30, "totalFrames": 600, "sceneDurations": durs, "captionLines": lines,
                 "design": {"wordsPerLine": 3}, "voiceVolume": 2.3,
                 "hook": {"text": " ".join(plan["scenes"][0]["on_screen_text"].split()[:6]) or "hook"},
                 "endCard": {"startS": 18.0, "appName": plan.get("_app_name", ""), "cta": "Download free"},
                 "voiceProviders": ["magnific" if s.get("voice_file") else "edge-tts" for s in plan["scenes"]]}
        (out_dir / "props.json").write_text(json.dumps(props, ensure_ascii=False))
        return {"path": str(out_dir / "video.mp4"), "thumb": str(out_dir / "thumbnail.jpg"), "duration": 20.0,
                "render_seconds": 0.1}
    return render


def test_full_crew_flow_routes_audio_fix_through_magnific_to_owner(crew_env, monkeypatch):
    """v1 uses the robotic free voice → audio reviewer raises it → QC fails → task routed to 'assets' → run waits for
    Magnific → voices imported (credits in the ledger) → v2 → QC pass → owner approves → final export."""
    from engine.db import Video, session
    from engine.production import director, editor, pipeline, assets
    from engine.production.common import get_run, tasks
    from sqlmodel import select

    c, tmp = crew_env["creative"], crew_env["tmp"]
    app_name = crew_env["app"].name
    real_direct = director.direct

    def direct_then_skip_voice(run):  # start with the free voice so the audio reviewer has something to catch
        real_direct(run)
        run.plan["_app_name"] = app_name
        run.asset_requests = []
        return run.plan

    monkeypatch.setattr(director, "direct", direct_then_skip_voice)
    monkeypatch.setattr(editor, "render_creative", _fake_render(tmp))

    run = pipeline.start(c.id)
    assert run.status == "waiting_assets" and run.version == 1
    t1 = tasks(run.id, version=1)
    robotic = [t for t in t1 if t.category == "naturalness"]
    assert robotic and robotic[0].raised_by == "audio" and robotic[0].assigned_to == "assets"
    assert robotic[0].status == "fixed"
    pend = assets.pending(run)
    assert pend and all(r["kind"] == "voice" for r in pend)
    sheet = json.loads((tmp / "outputs" / c.app_id / f"gen_{c.generation}" / str(c.id) / "production"
                        / "magnific_jobs.json").read_text())
    assert sheet["jobs"][0]["tool"] == "audio_tts" and sheet["jobs"][0]["arguments"]["voiceId"]

    for r in pend:  # the assistant ran audio_tts through the Magnific MCP; import the files
        f = tmp / f"{r['id']}.mp3"
        subprocess.run([__import__("engine.settings", fromlist=["x"]).ffmpeg_bin(), "-y", "-loglevel", "error", "-f",
                        "lavfi", "-i", "sine=f=300:d=2", str(f)], check=True)
        assets.import_file(get_run(run.id), r["id"], str(f), credits=18)
    assert assets.credits_spent_this_month() == 18 * len(pend)

    run = pipeline.resume(run.id)
    assert run.version == 2 and run.status == "awaiting_owner"
    v2 = [(t.raised_by, t.severity, t.category, t.issue) for t in tasks(run.id, version=2)]
    assert run.versions[-1]["passed"] is True, v2
    assert not [t for t in tasks(run.id, version=2) if t.category == "naturalness"]

    run = pipeline.approve(run.id, "looks good")
    assert run.status == "approved"
    with session() as s:
        v = s.exec(select(Video).where(Video.creative_id == c.id)).first()
    assert v and Path(v.path).exists() and v.path.endswith("/video.mp4")


def test_owner_note_without_actionable_fix_goes_back_to_owner(crew_env):
    from engine.production import pipeline
    from engine.production.common import tasks

    run = _new_run(crew_env["creative"])
    run = pipeline.reject(run.id, "make it feel more premium", rerun=False)
    assert run.status == "awaiting_owner"
    t = tasks(run.id)
    assert t and t[-1].raised_by == "owner" and t[-1].status == "needs_human"
