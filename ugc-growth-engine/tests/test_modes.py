"""Visual modes (faceless · images_vo · character · ai_character), per-app personas, mode-aware director + Magnific
jobs, unique text looks, and onboarding different apps from their briefs alone."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- mode mix

def test_research_decides_the_mode_mix(monkeypatch):
    from engine import modes
    from engine.research import market

    pat = pd.DataFrame([
        {"id": 1, "dimension": "production_style", "value": "talking_head", "videos": 19, "share": .56,
         "weighted_share": .55, "avg_outlier": 40, "lift": .2, "signal": "pattern"},
        {"id": 2, "dimension": "production_style", "value": "screen_demo", "videos": 6, "share": .18,
         "weighted_share": .2, "avg_outlier": 30, "lift": 0, "signal": "pattern"},
        {"id": 3, "dimension": "production_style", "value": "skit", "videos": 4, "share": .12,
         "weighted_share": .15, "avg_outlier": 20, "lift": 0, "signal": "pattern"},
        {"id": 4, "dimension": "production_style", "value": "text_broll", "videos": 3, "share": .09,
         "weighted_share": .1, "avg_outlier": 10, "lift": 0, "signal": "pattern"},
    ])
    ctx = market.MarketContext(playbook={"recipes": []}, patterns=pat)
    monkeypatch.setattr(market, "context", lambda app_id: ctx)
    shares, source = modes.planned_shares("any_app")
    assert source == "market research"
    assert max(shares, key=shares.get) == "ai_character"          # talking heads dominate this market
    assert shares["images_vo"] >= modes.FLOOR * 0.9               # never tested in the market → still explorable
    assert ctx.best("visual_mode") == "ai_character"              # also a prior for the optimizer / insights


def test_no_research_uses_format_fit():
    from engine import modes

    shares, source = modes.planned_shares("app_without_research")
    assert source.startswith("format fit") and abs(sum(shares.values()) - 1) < 1e-9


def test_modes_respect_creator_style_and_budget():
    from engine import modes

    assert "character" not in modes.compatible("problem_solution", "screen_demo")
    assert modes.compatible("pov", "storytime")[0] == "character"   # format preference first
    chosen = [NS(id=i, visual_mode="ai_character", format="pov", creator_style="bestie_narrator") for i in range(4)]
    notes = modes.fit_budget(chosen, credits_left=2000)
    assert notes and sum(modes.mode_cost(c.visual_mode) for c in chosen) <= 2000


# ---------------------------------------------------------------- director per mode

def _dp(n, shots, persona=""):
    from engine.production.director import DirectorPlan, ScenePlan

    return DirectorPlan(scenes=[ScenePlan(scene=i, shot=shots[i], persona=persona, voiceover=f"line {i} words here",
                                          headline=f"h{i}") for i in range(n)])


SCRIPT = {"scenes": [{"voiceover": f"line {i}", "on_screen_text": "hook" if i == 0 else ""} for i in range(5)]}
CAST = [{"key": "asha", "role": "protagonist"}, {"key": "rohan", "role": "presenter"}]


def test_ai_character_talks_only_in_hook_and_cta():
    from engine.production.director import normalise

    plan = normalise(_dp(5, ["talking"] * 5, "asha"), SCRIPT, 3, "ai_character", CAST)
    shots = [s["shot"] for s in plan["scenes"]]
    assert shots[0] == shots[-1] == "talking" and "talking" not in shots[1:-1]
    assert plan["scenes"][0]["persona"] == "rohan"                    # the presenter talks


def test_modes_degrade_honestly():
    from engine.production.director import normalise

    no_cast = normalise(_dp(5, ["talking", "persona_clip", "persona_image", "screenshot", "talking"]), SCRIPT, 0,
                        "ai_character", [])
    shots = [s["shot"] for s in no_cast["scenes"]]
    assert "talking" not in shots and "persona_image" not in shots and shots[3] == "motion_graphic"
    assert no_cast["mode_notes"]                                      # the degradation is visible, not silent
    images = normalise(_dp(5, ["stock"] * 5), SCRIPT, 2, "images_vo", CAST)
    assert {s["shot"] for s in images["scenes"]} == {"stock_photo"}   # stills only
    char = normalise(_dp(5, ["persona_clip"] * 5, "asha"), SCRIPT, 2, "character", CAST)
    from engine.settings import load_yaml
    caps = (load_yaml("production")["magnific"].get("character_limits") or {})
    shots = [s["shot"] for s in char["scenes"]]
    assert shots.count("persona_clip") == min(1, int(caps.get("persona_clips", 1)))        # at most the configured clips
    assert shots.count("persona_image") <= int(caps.get("persona_images", 3))              # paid portraits are capped per video


# ---------------------------------------------------------------- Magnific jobs + looks

@pytest.fixture()
def app_with_cast(tmp_db, brief, tmp_path, monkeypatch):
    from engine import understand, variations
    from engine.brief import register_app
    from engine.db import Persona, save
    import engine.brief as brief_mod
    import engine.visuals as visuals_mod
    import engine.production.common as common

    monkeypatch.setattr(brief_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(visuals_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(common, "OUTPUTS_DIR", tmp_path / "outputs")
    brief.app_id, brief.languages = "cast_app", ["hi"]
    app = register_app(brief, enrich=False)
    understand.run(app.id)
    variations.run_generation_one(app.id, languages=["hi"], styles=["bestie_narrator"], n_concepts=2,
                                  hooks_per_concept=2, scripted=4, render_count=2, use_llm_compliance=False)
    save(Persona(app_id=app.id, key="asha", name="Asha", role="protagonist", status="ready", library_id="555",
                 approved={"file": "", "creation": "portraitA"}),
         Persona(app_id=app.id, key="rohan", name="Rohan", role="presenter", status="ready", library_id="556",
                 approved={"file": "", "creation": "portraitR"}))
    c = next(c for c in tmp_db.creatives(app.id, 1) if c.script)
    return app, c


def test_character_jobs_are_chained_and_keep_the_face(app_with_cast):
    from engine.db import ProductionRun
    from engine.production import assets

    app, c = app_with_cast
    c.visual_mode = "ai_character"
    plan = {"mode": "ai_character", "scenes": [
        {"shot": "talking", "persona": "rohan", "voiceover": "ek do teen chaar", "delivery": "excited"},
        {"shot": "persona_clip", "persona": "asha", "voiceover": "x", "image_prompt_en": "counting cash", "motion": "looks up"},
        {"shot": "talking", "persona": "rohan", "voiceover": "download karo abhi"}]}
    reqs = assets.requests_for(c, plan)
    by = {r["id"]: r for r in reqs}
    assert by["pimg_s1"]["mcp"]["arguments"]["references"] == [{"type": "character", "identifier": "555"}]
    assert by["clip_s1"]["depends_on"] == ["pimg_s1"]
    assert by["talk_s0"]["depends_on"] == ["voice_s0", "face_s0"]            # lip-sync needs the voice + a presenter frame
    assert by["face_s0"]["mcp"]["arguments"]["references"] == [{"type": "character", "identifier": "556"}]
    run = ProductionRun(app_id=app.id, creative_id=c.id, plan=plan, asset_requests=reqs)
    first = {j["request_id"] for j in assets.ready(run)}
    assert "clip_s1" not in first and "talk_s0" not in first and "pimg_s1" in first
    for r in run.asset_requests:                                   # images + voices done → clips/talking become ready
        if r["kind"] in ("voice", "image"):
            r.update(status="done", creation=f"C_{r['id']}")
    nxt = {j["request_id"]: j for j in assets.ready(run)}
    assert nxt["clip_s1"]["arguments"]["keyframes"]["start"]["url"] == "C_pimg_s1"
    assert nxt["talk_s0"]["arguments"]["audioUrl"] == "C_voice_s0"
    assert nxt["talk_s0"]["arguments"]["imageUrl"] == "C_face_s0"


def test_mode_cap_downgrades_instead_of_overspending(app_with_cast):
    from engine.production import assets

    _, c = app_with_cast
    c.visual_mode = "faceless"                       # cap 120: voice only
    plan = {"mode": "faceless", "scenes": [{"shot": "scene_image", "voiceover": "a b", "image_prompt_en": "x"}] * 3}
    plan["scenes"] = [dict(s) for s in plan["scenes"]]
    reqs = assets.requests_for(c, plan)
    assert sum(r["credits"] for r in reqs) <= assets.per_video_cap(c)
    assert all(s["shot"] in ("scene_image", "stock") for s in plan["scenes"])


def test_every_video_in_a_batch_gets_its_own_look():
    from engine import captions

    batch = [NS(id=i, language=["hi", "ta", "hinglish"][i % 3], creator_style="bestie_narrator",
                visual_mode=["faceless", "images_vo", "character", "ai_character"][i % 4], style_signature="")
             for i in range(1, 31)]
    captions.assign_styles(batch)
    sigs = [b.style_signature.split("|", 1)[1] for b in batch]
    assert len(set(sigs)) == len(sigs)
    again = [NS(**vars(b)) for b in batch]
    captions.assign_styles(again)
    assert [a.style_signature for a in again] == [b.style_signature for b in batch]   # stable on re-render
    talk = captions.design("hi", "bestie_narrator", 3, "ai_character")
    assert talk["captionLayout"] in ("lowerbar", "boxed", "karaoke")                 # keeps the face clear


def test_talking_scene_carries_its_own_audio_and_headlines_show(app_with_cast, tmp_path, monkeypatch):
    from engine import render, visuals
    from engine.settings import ffmpeg_bin

    app, c = app_with_cast
    clip, img, mp3 = tmp_path / "talk.mp4", tmp_path / "p.jpg", tmp_path / "v.mp3"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=270x480:d=2",
                    "-f", "lavfi", "-i", "sine=d=2", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", str(clip)], check=True)
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=270x480", "-frames:v", "1",
                    str(img)], check=True)
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=2", str(mp3)], check=True)
    monkeypatch.setattr(visuals, "fetch_stock", lambda *a, **k: {"kind": "image", "path": str(img), "attribution": ""})
    c.visual_mode = "images_vo"
    c.script = {"scenes": [{"voiceover": "a b", "on_screen_text": "hook"}, {"voiceover": "c d"}, {"voiceover": "e f"}],
                "cta": "Download"}
    plan = {"mode": "images_vo", "scenes": [
        {"shot": "talking", "asset": str(clip), "talking": True, "headline": ""},
        {"shot": "stock_photo", "query_en": "q", "headline": "Big headline"},
        {"shot": "motion_graphic", "graphic": {"type": "steps", "items": ["one", "two"]}, "headline": "x"}]}
    voice = [{"path": str(mp3), "duration": 2.0, "provider": "magnific",
              "words": [{"start": 0.1, "end": 0.5, "word": "a"}, {"start": 0.6, "end": 1.0, "word": "b"}]}] * 3
    work = tmp_path / "work"
    work.mkdir()
    props = render.build_props(c, app, work, voice, [2.25, 2.25, 2.25], plan)
    kinds = [s["kind"] for s in props["segments"]]
    assert kinds[0] == "video" and props["segments"][0]["audible"] is True
    assert "graphic" in kinds
    assert len(props["voice"]) == 2                                  # scene 0's voice comes from the talking clip
    assert [h["text"] for h in props["headlines"]] == ["Big headline"]


# ---------------------------------------------------------------- any app, from its brief alone

def test_two_different_apps_onboard_independently(tmp_db, tmp_path, monkeypatch):
    from engine import modes, understand
    from engine.brief import load_brief, register_app
    from engine.production import personas
    import engine.brief as brief_mod
    import engine.visuals as visuals_mod
    import engine.production.personas as pmod

    monkeypatch.setattr(brief_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(visuals_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(pmod, "OUTPUTS_DIR", tmp_path / "outputs")
    ids = []
    for f in ("fintech.json", "edtech.json"):
        b = load_brief(ROOT / "data" / "sample_briefs" / f)
        app = register_app(b, enrich=False)
        understand.run(app.id)
        personas.propose(app.id)
        ids.append(app.id)
        sheet = json.loads(personas.write_sheet(app.id).read_text())
        assert sheet["jobs"] and all(j["tool"] == "images_generate" for j in sheet["jobs"])
    a, b = ids
    assert a != b
    assert {p.app_id for p in personas.personas(a)} == {a} and {p.app_id for p in personas.personas(b)} == {b}
    pa = personas.personas(a)[0]
    pa.approved, pa.status = {"creation": "X"}, "approved"
    tmp_db.save(pa)
    lib = personas.library_job(a, pa)
    assert lib["arguments"]["name"].startswith(f"{a}_")               # library names never collide across apps
    assert modes.planned_shares(a)[1].startswith("format fit")        # no research → honest fallback, per app
