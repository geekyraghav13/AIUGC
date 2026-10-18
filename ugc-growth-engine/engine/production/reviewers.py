"""SPECIALIZED REVIEWERS — text/caption, visual/design, audio, brand/style. Each one checks a rendered version with
hard measurements first (timings, loudness, silences, compliance rules) and then an AI pass (Gemini watching the
frames / listening to the soundtrack). Every problem comes back as a specific, timestamped Issue with a fix."""
from __future__ import annotations

import re
import subprocess
from typing import Any, Callable

from pydantic import BaseModel, Field

from engine import compliance, llm
from engine.cost import BudgetExceeded
from engine.brief import brief_of
from engine.db import ProductionRun, get_app
from engine.localize import language_block
from engine.production.common import (Issue, Issues, cfg, creative_of, extract_audio, plan_for_prompt, props_of,
                                      review_frames, say, scene_bounds, version_dir)
from engine.settings import creator_styles, ffmpeg_bin, languages, log

Reviewer = Callable[[ProductionRun, int, dict[str, Any]], list[Issue]]


def _scene_at(props: dict[str, Any], t: float) -> int:
    for i, (a, b) in enumerate(scene_bounds(props)):
        if a <= t < b:
            return i
    return max(0, len(scene_bounds(props)) - 1)


def screen_texts(run: ProductionRun, props: dict[str, Any]) -> list[dict[str, Any]]:
    """Everything a viewer reads, with timecodes: hook sticker, caption lines (= the voiceover), CTA, app name."""
    out = [{"t": 0.0, "scene": 0, "where": "hook sticker (scene 0 on_screen_text)", "text": props["hook"]["text"]}]
    for ln in props.get("captionLines", []):
        out.append({"t": round(ln["start"], 2), "scene": _scene_at(props, ln["start"]), "where": "caption (voiceover)",
                    "text": " ".join(w["text"] for w in ln["words"])})
    ec = props.get("endCard", {})
    out.append({"t": round(ec.get("startS", 0), 2), "scene": None, "where": "end card CTA (field cta)", "text": ec.get("cta", "")})
    out.append({"t": round(ec.get("startS", 0), 2), "scene": None, "where": "end card app name", "text": ec.get("appName", "")})
    return out


def _files(frames: list[tuple[float, Any]]) -> list[Any]:
    return [f for _, f in frames]


def _ai_or_skip(ctx: dict[str, Any], task: str, prompt: str, files: list[Any], app_id: str) -> Issues:
    """Per-specialist AI pass — skipped in single-pass mode (one combined review call per version saves tokens)."""
    if ctx.get("single_pass"):
        return Issues(issues=[], summary="covered by the single-pass review")
    return _ai(task, prompt, files, app_id)


def _ai(task: str, prompt: str, files: list[Any], app_id: str) -> Issues:
    try:
        return llm.complete_json_media(task, prompt, files, Issues, app_id=app_id,
                                       mock_payload=lambda: {"issues": [], "summary": "mock review"})
    except (llm.LLMError, BudgetExceeded) as e:
        log.warning(f"{task}: AI pass unavailable ({e})")
        return Issues(issues=[], summary=f"AI pass unavailable: {str(e)[:120]}")


# ---------------------------------------------------------------- text / caption

def text_reviewer(run: ProductionRun, version: int, ctx: dict[str, Any]) -> list[Issue]:
    c, props = ctx["creative"], ctx["props"]
    rc = cfg()["review"]
    issues: list[Issue] = []
    lines = props.get("captionLines", [])
    short = [ln for ln in lines if ln["end"] - ln["start"] < float(rc["min_caption_seconds"])]
    if short:
        wpl = int(props["design"].get("wordsPerLine", 3))
        issues.append(Issue(category="timing", severity="minor" if len(short) < 3 else "major",
                            timecode=round(short[0]["start"], 2), scene=_scene_at(props, short[0]["start"]),
                            issue=f"{len(short)} caption lines flash for under {rc['min_caption_seconds']}s (too fast to read)",
                            fix={"type": "set_design", "wordsPerLine": min(4, wpl + 1)}))
    spoken = sum(len((s.get("voiceover") or "").split()) for s in run.plan["scenes"])
    shown = sum(len(ln["words"]) for ln in lines)
    if spoken and shown < 0.85 * spoken:
        issues.append(Issue(category="missing", severity="major", issue=f"captions show {shown} of {spoken} spoken words",
                            fix={"type": "none"}))
    hook = props["hook"]["text"]
    if len(hook.split()) > 6:
        issues.append(Issue(category="length", severity="major", timecode=0.5, scene=0,
                            issue=f"hook text has {len(hook.split())} words (max 6 to read on mute): “{hook}”",
                            fix={"type": "none"}))
    ai = _ai_or_skip(ctx, f"crew_text_{c.id}_v{version}", llm.render_prompt(
        "crew_text", language_name=languages()[c.language]["name"], app_name=ctx["brief"].app_name,
        language_block=language_block(c.language), texts=screen_texts(run, props)),
        _files(ctx["frames"]), c.app_id)
    return issues + ai.issues


# ---------------------------------------------------------------- visual / design

MODE_NOTES = {
    "faceless": "No recurring character; people may appear only in stock footage.",
    "images_vo": "Every scene is a still image with a big headline; that is intended (not a slideshow bug).",
    "character": "A recurring AI character appears in some scenes. Their reference photo is attached (file name starts "
                 "with persona_). Every scene showing them must show the SAME person: face, age, skin tone, hair.",
    "ai_character": "The presenter talks to camera in the first and last scene (short clips attached as talk_*.mp4); "
                    "check the face and lip-sync there. A recurring character may appear in the middle; their "
                    "reference photos are attached (persona_*). The same person must look the same everywhere.",
}


def _mode_files(run: ProductionRun, version: int) -> list[Any]:
    """Persona reference photos + low-res cuts of the talking scenes, for the visual reviewer."""
    import shutil
    import subprocess

    from engine.production.personas import usable

    vd = version_dir(run, version)
    keys = {s.get("persona") for s in run.plan.get("scenes", []) if s.get("persona")}
    out = []
    for p in usable(run.app_id):
        if p.key in keys and p.approved.get("file") and __import__("pathlib").Path(p.approved["file"]).exists():
            dst = vd / "frames" / f"persona_{p.key}.jpg"
            shutil.copy2(p.approved["file"], dst)
            out.append(dst)
    props = props_of(run, version)
    for i, (a, b) in enumerate(scene_bounds(props)):
        if run.plan["scenes"][i].get("talking"):
            dst = vd / "frames" / f"talk_s{i}.mp4"
            try:
                subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i",
                                str(vd / "video.mp4"), "-vf", "scale=360:-2", "-c:v", "libx264", "-preset", "ultrafast",
                                "-crf", "30", "-c:a", "aac", "-b:a", "64k", str(dst)], check=True, capture_output=True,
                               timeout=120)
                out.append(dst)
            except (subprocess.SubprocessError, OSError) as e:
                log.warning(f"talking cut failed: {e}")
    return out


def visual_reviewer(run: ProductionRun, version: int, ctx: dict[str, Any]) -> list[Issue]:
    c, b = ctx["creative"], ctx["brief"]
    mode = run.plan.get("mode") or c.visual_mode or "faceless"
    ai = _ai_or_skip(ctx, f"crew_visual_{c.id}_v{version}", llm.render_prompt(
        "crew_visual", app_name=b.app_name, one_liner=b.one_liner, audience=b.target_audience,
        language_name=languages()[c.language]["name"], plan=plan_for_prompt(run.plan),
        mode=mode, mode_note=MODE_NOTES.get(mode, ""),
        screens_note=("NOTE: real app screenshots are unavailable for this video, so people's phones deliberately show "
                      "no screen or a blank screen. Never flag a blank/hidden phone screen and never ask for an app "
                      "interface to be shown — an invented app UI would mislead viewers.")
        if run.plan.get("no_screenshots") else ""),
        _files(ctx["frames"]) + (_mode_files(run, version) if mode in ("character", "ai_character") else []), c.app_id)
    return ai.issues


# ---------------------------------------------------------------- audio

def loudness(video: Any) -> dict[str, float]:
    p = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(video), "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=180)
    tail = p.stderr[p.stderr.rfind("Summary:"):] if "Summary:" in p.stderr else p.stderr
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    pk = re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    return {"lufs": float(i.group(1)) if i else float("nan"), "true_peak": float(pk.group(1)) if pk else float("nan")}


def silences(audio: Any, min_s: float) -> list[tuple[float, float]]:
    p = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(audio), "-af",
                        f"silencedetect=noise=-38dB:d={min_s}", "-f", "null", "-"], capture_output=True, text=True,
                       timeout=120)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", p.stderr)]
    return list(zip(starts, ends))


def audio_reviewer(run: ProductionRun, version: int, ctx: dict[str, Any]) -> list[Issue]:
    from engine.production import assets

    c, props = ctx["creative"], ctx["props"]
    rc = cfg()["review"]
    issues: list[Issue] = []
    video = version_dir(run, version) / "video.mp4"
    lv = loudness(video)
    target, tol = float(rc["loudness_target_lufs"]), float(rc["loudness_tolerance"])
    vol = float((run.plan.get("mix") or {}).get("voice_volume", props.get("voiceVolume", 2.3)))
    if lv["lufs"] == lv["lufs"] and abs(lv["lufs"] - target) > tol:
        gain = 10 ** ((target - lv["lufs"]) / 20)
        issues.append(Issue(category="level", severity="major", issue=f"loudness {lv['lufs']:.1f} LUFS (target {target:.0f})",
                            fix={"type": "set_mix", "voice_volume": round(vol * gain, 2)}))
    if lv["true_peak"] == lv["true_peak"] and lv["true_peak"] > -0.5:
        issues.append(Issue(category="level", severity="major", issue=f"true peak {lv['true_peak']:.1f} dBFS (clipping risk)",
                            fix={"type": "set_mix", "voice_volume": round(vol * 0.85, 2)}))
    audio = extract_audio(run, version)
    total = props.get("totalFrames", 0) / props.get("fps", 30)
    content_end = props.get("endCard", {}).get("startS", total)
    if audio:
        for a, b in silences(audio, float(rc["max_silence_s"])):
            if 0.5 < a < content_end - 0.5:
                issues.append(Issue(category="timing", severity="minor", timecode=round(a, 2), scene=_scene_at(props, a),
                                    issue=f"{b - a:.1f}s of silence inside the video", fix={"type": "set_pace", "pace": "fast"}))
    providers = props.get("voiceProviders") or []
    if providers and all(p != "magnific" for p in providers) and assets.voice_id_for(c.language, c.creator_style, c.app_id):
        issues.append(Issue(category="naturalness", severity="major", timecode=0.0, scene=0,
                            issue="voice is the free edge-tts voice (flat, robotic); a cast natural voice is available",
                            fix={"type": "request_asset", "kind": "voice", "scene": -1}))
    if audio:
        bounds = scene_bounds(props)
        script = [{"scene": i, "starts_at": round(bounds[i][0], 1) if i < len(bounds) else None,
                   "voiceover": s["voiceover"]} for i, s in enumerate(run.plan["scenes"])]
        ctx["audio_file"] = audio
        measured = (f"integrated loudness {lv['lufs']:.1f} LUFS (target {target:.0f}), true peak {lv['true_peak']:.1f} dBFS, "
                    f"music volume {(run.plan.get('mix') or {}).get('music_volume', 'default')}, video {total:.1f}s, "
                    f"end card from {content_end:.1f}s")
        ctx["measured"] = measured
        ai = _ai_or_skip(ctx, f"crew_audio_{c.id}_v{version}", llm.render_prompt(
            "crew_audio", language_name=languages()[c.language]["name"], script=script,
            providers=", ".join(providers) or "unknown", measured=measured), [audio], c.app_id)
        # the machine checks already own loudness; keep the AI's ear for naturalness, pronunciation, music
        issues += [i for i in ai.issues if i.category != "level"]
    return issues


# ---------------------------------------------------------------- brand / style

def brand_reviewer(run: ProductionRun, version: int, ctx: dict[str, Any]) -> list[Issue]:
    c, b, props = ctx["creative"], ctx["brief"], ctx["props"]
    texts = screen_texts(run, props)
    issues: list[Issue] = []
    for t in texts:
        for problem in compliance.rule_check(t["text"], b):
            issues.append(Issue(category="claims", severity="blocker", timecode=t["t"], scene=t["scene"],
                                issue=f"{problem} in {t['where']}", fix={"type": "none"}))
    if props.get("endCard", {}).get("appName", "").strip().lower() != b.app_name.strip().lower():
        issues.append(Issue(category="name", severity="blocker", issue="end card app name doesn't match the brief",
                            fix={"type": "none"}))
    if not props.get("endCard", {}).get("cta", "").strip():
        issues.append(Issue(category="cta", severity="major", issue="no CTA on the end card",
                            fix={"type": "set_text", "field": "cta", "value": b.cta}))
    vd = version_dir(run, version)
    pics = [p for p in (vd / "thumbnail.jpg",) if p.exists()]
    frames = ctx["frames"]
    if frames:
        pics += [frames[0][1], frames[-1][1]]  # hook frame, end card
    ai = _ai_or_skip(ctx, f"crew_brand_{c.id}_v{version}", llm.render_prompt(
        "crew_brand", app_name=b.app_name, brand_tone=b.brand_tone, audience=b.target_audience,
        claims_to_avoid=", ".join(b.claims_to_avoid) or "(none)", texts=texts, style=c.creator_style, cta=b.cta),
        pics, c.app_id)
    return issues + ai.issues


REVIEWERS: dict[str, Reviewer] = {"text": text_reviewer, "visual": visual_reviewer, "audio": audio_reviewer,
                                  "brand": brand_reviewer}


class PanelIssue(Issue):
    role: str = "visual"


class PanelIssues(BaseModel):
    issues: list[PanelIssue] = Field(default_factory=list)
    summary: str = ""


def panel_review(run: ProductionRun, version: int, ctx: dict[str, Any]) -> dict[str, list[Issue]]:
    """ONE Gemini call for all four specialists (frames + audio + references) instead of four — same checks, about a
    quarter of the tokens. Issues come back tagged with the specialist role."""
    c, b, props = ctx["creative"], ctx["brief"], ctx["props"]
    mode = run.plan.get("mode") or c.visual_mode or "faceless"
    bounds = scene_bounds(props)
    plan = [{**p, "starts_at": round(bounds[i][0], 1) if i < len(bounds) else None}
            for i, p in enumerate(plan_for_prompt(run.plan))]
    files = _files(ctx["frames"])
    thumb = version_dir(run, version) / "thumbnail.jpg"
    if thumb.exists():
        files.append(thumb)
    if mode in ("character", "ai_character"):
        files += _mode_files(run, version)
    if ctx.get("audio_file"):
        files.append(ctx["audio_file"])
    prompt = llm.render_prompt(
        "crew_review", app_name=b.app_name, one_liner=b.one_liner, language_name=languages()[c.language]["name"],
        audience=b.target_audience, brand_tone=b.brand_tone, claims_to_avoid=", ".join(b.claims_to_avoid) or "(none)",
        mode=mode, mode_note=MODE_NOTES.get(mode, ""), plan=plan, texts=screen_texts(run, props),
        measured=ctx.get("measured", "(n/a)"),
        screens_note=("NOTE: real app screenshots are unavailable, so phones deliberately show no screen; never ask for "
                      "an app interface to be drawn.") if run.plan.get("no_screenshots") else "")
    try:
        out: PanelIssues = llm.complete_json_media(f"crew_review_{c.id}_v{version}", prompt, files, PanelIssues,
                                                   app_id=c.app_id, mock_payload=lambda: {"issues": [], "summary": "mock"})
    except (llm.LLMError, BudgetExceeded) as e:   # no AI review (e.g. daily Gemini cap): machine checks only
        log.warning(f"panel review unavailable ({e})")
        return {"brand": [Issue(category="no_ai_review", severity="minor", fix={"type": "none"},
                                issue="AI review skipped (Gemini unavailable/budget) — only machine checks ran; "
                                      "watch this video before posting")]}
    by_role: dict[str, list[Issue]] = {}
    for i in out.issues:
        role = i.role if i.role in REVIEWERS else "visual"
        if role == "audio" and i.category == "level":
            continue  # machines own loudness
        by_role.setdefault(role, []).append(Issue(**i.model_dump(exclude={"role"})))
    return by_role


def review(run: ProductionRun, version: int) -> dict[str, list[Issue]]:
    """Measured checks per specialist (free), then ONE combined AI review call (cfg review.single_pass, default on).
    A reviewer that crashes reports that as its own issue."""
    c = creative_of(run)
    props = props_of(run, version)
    single = bool(cfg()["review"].get("single_pass", True))
    ctx = {"creative": c, "brief": brief_of(get_app(c.app_id)), "props": props,
           "frames": review_frames(run, version, int(cfg()["review"]["frames_per_scene"])),
           "style": creator_styles()[c.creator_style], "single_pass": single}
    out: dict[str, list[Issue]] = {}
    for role, fn in REVIEWERS.items():
        try:
            found = fn(run, version, ctx)
        except Exception as e:  # noqa: BLE001 — one reviewer failing never stops the review
            log.exception(f"{role} reviewer crashed")
            found = [Issue(category="reviewer_error", severity="minor", issue=f"{role} review failed: {str(e)[:160]}",
                           fix={"type": "none"})]
        out[role] = found
    if single:
        for role, found in panel_review(run, version, ctx).items():
            out.setdefault(role, []).extend(found)
    for role, found in out.items():
        bad = [i for i in found if i.severity in ("blocker", "major")]
        say(run, role, f"v{version}: {len(found)} issues ({len(bad)} blocker/major)"
                       + (f" — e.g. {bad[0].issue[:90]}" if bad else ""))
    return out
