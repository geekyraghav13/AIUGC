"""Render one creative to a 1080×1920 30 fps vertical video with Remotion (React + headless Chromium, CPU only).

Python does the planning (voice, stock footage, cuts, caption timing) and writes a props JSON; the composition in
`remotion/src/UgcVideo.tsx` draws scenes, karaoke captions, hook overlay, end card and the AI label, and
`remotion/render.mjs` renders the MP4 plus a thumbnail.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from sqlmodel import delete

from engine.locks import locked
from engine import captions, cost, textfit, tracking, tts, visuals
from engine.brief import brief_of
from engine.db import Creative, Video, creatives, get_app, save, session
from engine.settings import (FONTS_DIR, MUSIC_DIR, OUTPUTS_DIR, ROOT, creator_styles, env_float, env_int,
                             ffmpeg_bin, languages, log)

W, H, FPS = 1080, 1920, 30
END_CARD_S = 2.0
# Remotion has no loudnorm pass: gains are tuned so the mix lands near −14 LUFS (checked with ffmpeg ebur128).
VOICE_VOLUME = env_float("VOICE_VOLUME", 2.3)
MUSIC_VOLUME = VOICE_VOLUME * 0.1  # ≈ −20 dB under the voice
# On-video "AI-generated" tag. Off by default at the owner's request (SHOW_AI_LABEL=1 turns it back on); the
# caption.txt disclosure line stays, and platforms' own AI-content toggle should be set when publishing.
MAGNIFIC_VOICE_VOLUME = env_float("MAGNIFIC_VOICE_VOLUME", 1.0)
AI_LABEL = "AI-generated" if env_int("SHOW_AI_LABEL", 0) else ""
REMOTION_DIR = ROOT / "remotion"


class RenderError(RuntimeError):
    pass


def remotion_ready() -> None:
    """Fail early, with the fix, if Node or the Remotion dependencies are missing."""
    if not shutil.which("node"):
        raise RenderError("Node.js is required for rendering (install Node 18+, e.g. `brew install node`).")
    if not (REMOTION_DIR / "node_modules" / "@remotion" / "renderer").exists():
        raise RenderError("Remotion is not installed: run `npm install` inside the remotion/ folder.")


def run_remotion(job: dict[str, Any], cwd: Path, timeout: int = 1200) -> None:
    remotion_ready()
    job_file = cwd / "job.json"
    job_file.write_text(json.dumps(job), encoding="utf-8")
    p = subprocess.run(["node", str(REMOTION_DIR / "render.mjs"), str(job_file)], capture_output=True, text=True,
                       cwd=REMOTION_DIR, timeout=timeout)
    if p.returncode != 0:
        raise RenderError(f"remotion failed: {(p.stderr or p.stdout).strip()[-800:]}")


def default_concurrency() -> int:
    """Chromium tabs per render; the worker pool multiplies this, so split the cores between workers."""
    return env_int("REMOTION_CONCURRENCY", max(1, (os.cpu_count() or 4) // max(1, env_int("RENDER_WORKERS", 2))))


# ---------------------------------------------------------------- planning

def plan_visuals(scenes: list[dict[str, Any]], style: str, n_screens: int) -> list[dict[str, Any]]:
    """Nudge the scene mix toward the creator style's screenshot ratio (hook scene stays as written)."""
    ratio = float(creator_styles()[style].get("screenshot_ratio", 0.3))
    vis = [dict(sc.get("visual") or {}) for sc in scenes]
    for v in vis:
        v.setdefault("type", "stock")
        v.setdefault("query_en", "young indian person using smartphone")
        v.setdefault("screenshot_index", 0)
    if n_screens == 0:
        for v in vis:
            v["type"] = "stock"
        return vis
    want = round(ratio * len(vis))
    have = [i for i, v in enumerate(vis) if v["type"] == "screenshot"]
    stock = [i for i, v in enumerate(vis) if v["type"] != "screenshot" and i > 0]
    while len(have) < want and stock:
        i = stock.pop()
        vis[i]["type"] = "screenshot"
        vis[i]["screenshot_index"] = len(have) % n_screens
        have.append(i)
    while len(have) > want + 1:
        i = have.pop(0)
        if i == len(vis) - 1:
            continue  # keep app on screen for the CTA
        vis[i]["type"] = "stock"
    return vis


def music_for(style: str, seed: int) -> Optional[Path]:
    energy = creator_styles()[style].get("music_energy", "medium")
    tracks = sorted(MUSIC_DIR.glob(f"{energy}_*.mp3")) or sorted(MUSIC_DIR.glob("*.mp3"))
    return tracks[seed % len(tracks)] if tracks else None


class Stage:
    """The job's public dir: Remotion can only load files served from it, so hard-link (or copy) assets in.
    (Symlinks don't survive the bundler's copy of the public dir.)"""

    def __init__(self, root: Path):
        self.root = root
        self._names: dict[str, str] = {}

    def add(self, src: str | Path, prefix: str) -> str:
        src = Path(src).resolve()
        if str(src) in self._names:
            return self._names[str(src)]
        name = f"{prefix}_{len(self._names):03d}{src.suffix}"
        dst = self.root / name
        try:
            os.link(src, dst)
        except OSError:  # different volume
            shutil.copy2(src, dst)
        self._names[str(src)] = name
        return name


def font_files(theme: dict[str, Any], lang: str) -> list[dict[str, str]]:
    """Fonts one video needs: Noto Sans (fallback), the language's Noto font, and the video's two chosen faces.
    All are declared with the full weight range so the browser never fakes a bold."""
    want = [{"family": "Noto Sans", "file": "NotoSans-Bold.ttf"},
            {"family": languages()[lang]["font"], "file": f"{languages()[lang]['font'].replace(' ', '')}-Bold.ttf"},
            *theme["_fonts"]]
    seen, out = set(), []
    for f in want:
        if f["file"] in seen:
            continue
        if not (FONTS_DIR / f["file"]).exists():
            log.warning(f"font {f['file']} missing — run scripts/get_fonts.py")
            continue
        seen.add(f["file"])
        out.append({"family": f["family"], "file": f["file"], "weight": "100 900"})
    return out


POSITION_BAND = {"upper": "upper", "middle": "middle", "lower": "lower"}


def cover_background(segments: list[dict[str, Any]], work: Path) -> Optional[str]:
    """Staged 9:16 image for the thumbnail: a frame of the first footage scene (or its still image)."""
    seg = next((x for x in segments if x["kind"] in ("video", "image")), segments[0] if segments else None)
    if seg is None:
        return None
    if seg["kind"] != "video":
        return seg.get("src")           # motion-graphic / text scenes have no image file → no staged cover
    dest = work / "cover_bg.jpg"
    try:
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{seg['startS'] + 0.6:.2f}", "-i",
                        str(work / seg["src"]), "-frames:v", "1", "-vf",
                        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", "-q:v", "2", str(dest)],
                       check=True, capture_output=True, timeout=60)
        return dest.name
    except (subprocess.SubprocessError, OSError) as e:
        log.warning(f"cover frame grab failed ({e}); using the clip's first frame")
        return None


def embed_cover(video: Path, thumb: Path) -> None:
    """Attach the thumbnail to the MP4 as its cover art (best effort)."""
    tmp = video.with_name("video.cover.mp4")
    try:
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(video), "-i", str(thumb), "-map", "0",
                        "-map", "1", "-c", "copy", "-disposition:v:1", "attached_pic", "-movflags", "+faststart",
                        str(tmp)], check=True, capture_output=True, timeout=120)
        tmp.replace(video)
    except (subprocess.SubprocessError, OSError) as e:
        log.warning(f"could not embed cover art: {e}")
        tmp.unlink(missing_ok=True)


def build_props(c: Creative, app: Any, work: Path, voice: list[dict[str, Any]], durs: list[float],
                plan: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Everything the composition needs, with all timings in frames and all assets staged in `work`.

    `plan` (from the production crew's director/editor) can override per-scene visuals and text bands, pacing,
    transition, typography and the audio mix; without it the defaults below apply.
    """
    plan = plan or {}
    pscenes = plan.get("scenes") or []
    b = brief_of(app)
    style, lang = c.creator_style, c.language
    scenes = c.script["scenes"]
    stage = Stage(work)
    fonts_dir = work / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    theme = {**captions.design(lang, style, captions.style_seed(c), c.visual_mode), **(plan.get("design") or {})}
    fonts = []
    for f in font_files(theme, lang):
        shutil.copy2(FONTS_DIR / f["file"], fonts_dir / f["file"])
        fonts.append({**f, "file": f"fonts/{f['file']}"})

    shots = [] if plan.get("no_screenshots") else [s for s in (app.screenshots or []) if Path(s).exists()]
    vis = plan_visuals(scenes, style, len(shots))
    attribution: list[str] = []
    crew = bool(pscenes)  # a director's plan: never fill a stock miss with unrelated generic footage
    for i, ps in enumerate(pscenes[:len(vis)]):
        shot = ps.get("shot")
        card = ps.get("headline") or ps.get("on_screen_text") or ps.get("voiceover", "")[:40]
        if ps.get("asset") and Path(ps["asset"]).exists():
            vis[i] = {"type": "asset", "path": ps["asset"], "talking": bool(ps.get("talking"))}
            if ps.get("attribution"):
                attribution.append(ps["attribution"])
        elif shot == "motion_graphic":
            vis[i] = {"type": "graphic", "graphic": ps.get("graphic") or {"type": "card", "items": [card]}}
        elif shot == "screenshot" and shots:
            vis[i] = {**vis[i], "type": "screenshot", "screenshot_index": ps.get("screenshot_index", 0)}
        elif shot == "screenshot":
            vis[i] = {"type": "graphic", "graphic": {"type": "card", "items": [card]}}
        else:  # stock / stock_photo, or an AI shot whose asset isn't there (skipped) → free stock of the same moment
            vis[i] = {**vis[i], "type": "stock", "photo_only": shot == "stock_photo" or plan.get("mode") == "images_vo",
                      "query_en": ps.get("query_en") or ps.get("image_prompt_en") or vis[i].get("query_en"),
                      "card": card}
    cut = float(plan.get("cut_seconds") or creator_styles()[style].get("cut_seconds", 3.0))
    frame = lambda sec: int(round(sec * FPS))  # noqa: E731
    segments: list[dict[str, Any]] = []
    scene_frames: list[list[Any]] = []  # sample frames per scene, for text placement
    t = 0.0

    def add(kind: str, start_s: float, dur_s: float, **kw: Any) -> None:
        a, e = frame(start_s), frame(start_s + dur_s)
        segments.append({"kind": kind, "from": a, "frames": max(1, e - a), **kw})

    for i, (v, d) in enumerate(zip(vis, durs)):
        frames_i: list[Any] = []
        scene_frames.append(frames_i)
        if v["type"] == "graphic":  # motion graphic drawn by Remotion in the video's colours and fonts
            add("graphic", t, d, graphic=v["graphic"], index=i)
            frames_i.append(textfit.load_image(visuals.gradient(b.app_name)))
        elif v["type"] == "asset":  # produced for this video (Magnific photo / clip / talking presenter)
            src = stage.add(v["path"], "asset")
            if Path(v["path"]).suffix.lower() in (".mp4", ".mov", ".webm"):
                clip_len = tts.duration(v["path"])
                add("video", t, d, src=src, startS=0.0, clipS=round(clip_len, 2), audible=bool(v.get("talking")))
                frames_i.append(textfit.video_frame(v["path"], min(d, clip_len) / 2))
            else:
                add("image", t, d, src=src, direction=i)
                frames_i.append(textfit.load_image(v["path"]))
        elif v["type"] == "screenshot" and shots:
            add("screenshot", t, d, src=stage.add(shots[min(int(v.get("screenshot_index", 0)), len(shots) - 1)], "shot"))
        else:
            stock = visuals.fetch_stock(v.get("query_en") or "india city life", app_id=c.app_id,
                                        photo_only=bool(v.get("photo_only")), generic_fallback=not crew)
            if stock["kind"] == "none" and crew:
                # no matching stock: a clean text card beats unrelated footage (reviewers can order an AI photo)
                add("graphic", t, d, graphic={"type": "card", "items": [v.get("card") or ""]}, index=i)
                frames_i.append(textfit.load_image(visuals.gradient(b.app_name)))
                vis[i]["type"] = "graphic"
            elif stock["kind"] == "none":
                # nothing clearly Indian in stock → show the app instead of off-market footage
                if shots:
                    add("screenshot", t, d, src=stage.add(shots[i % len(shots)], "shot"))
                    vis[i]["type"] = "screenshot"
                else:
                    add("gradient", t, d, src=stage.add(visuals.gradient(b.app_name), "bg"), direction=i)
                    frames_i.append(textfit.load_image(visuals.gradient(b.app_name)))
            else:
                if stock.get("attribution"):
                    attribution.append(stock["attribution"])
                n_cuts = max(1, min(3, math.floor(d / cut + 0.35)))
                sub = d / n_cuts
                clip_len = tts.duration(stock["path"]) if stock["kind"] == "video" else 0
                src = stage.add(stock["path"], "stock")
                for k in range(n_cuts):
                    if stock["kind"] == "video":
                        start = (k * sub * 1.7 + i * 0.9) % max(0.1, clip_len - sub) if clip_len > sub else 0
                        add("video", t + k * sub, sub, src=src, startS=round(start, 2), clipS=round(clip_len, 2))
                        frames_i.append(textfit.video_frame(stock["path"], start + sub / 2))
                    else:
                        add("image", t + k * sub, sub, src=src, direction=i + k)
                        frames_i.append(textfit.load_image(stock["path"]))
        scene_frames[i] = [f for f in frames_i if f is not None]
        t += d
    total = t + END_CARD_S

    # voice: each scene's audio starts at its scene start
    voice_tracks, words, screen_ranges, offset = [], [], [], 0.0
    for v, d, vi in zip(voice, durs, vis):
        if not vi.get("talking"):  # a talking presenter clip carries its own (lip-synced) audio
            voice_tracks.append({"src": stage.add(v["path"], "voice"), "from": frame(offset)})
        words += [captions.Word(offset + w["start"], offset + w["end"], w["word"]) for w in v["words"]]
        if vi["type"] == "screenshot" and shots:
            screen_ranges.append((offset, offset + d))
        offset += d

    st = creator_styles()[style]["caption"]
    style_hex = str(st["highlight"]).lstrip("#").upper()
    # one palette per video (varies with the footage and the creative), then per-scene text positions
    palette = textfit.video_palette([f for fs in scene_frames for f in fs], style_hex, c.id)
    # the hook sits top/upper by default; a reviewer who saw it over a face moves it (scene 0 headline_band)
    hook_band = pscenes[0].get("headline_band") if pscenes else None
    hook_band = hook_band if hook_band in textfit.BANDS else None
    hook_look = textfit.choose([scene_frames[0]], palette, hook_band or "top",
                               allowed=(hook_band,) if hook_band else ("top", "upper"))[0]
    # app screens and motion graphics own the middle of the frame: their captions sit in the bottom band
    forced = ["shot" if (v["type"] == "screenshot" and shots) or v["type"] == "graphic" else None for v in vis]
    for i, ps in enumerate(pscenes[:len(forced)]):
        if ps.get("text_band") in textfit.BANDS and forced[i] != "shot":
            forced[i] = ps["text_band"]
    prefer = POSITION_BAND.get(st.get("position", "lower"), "lower")
    looks = textfit.choose(scene_frames, palette, prefer, forced=forced, avoid={0: hook_look.band})
    bounds, o = [], 0.0
    for lk, d in zip(looks, durs):
        bounds.append((o, o + d, lk.as_dict()))
        o += d
    # big per-scene headlines (images + voice-over and faceless videos): on the opposite side from the captions
    headlines = []
    if plan.get("mode") in ("images_vo", "faceless"):
        for i, (ps, (a_, b_, lk)) in enumerate(zip(pscenes, bounds)):
            text = tts.clean_text(ps.get("headline") or "")
            if i == 0 or not text or vis[i].get("talking") or vis[i]["type"] in ("graphic", "screenshot"):  # store screens carry their own titles
                continue
            if ps.get("headline_band") in textfit.BANDS:           # a reviewer moved it
                y = textfit.BANDS[ps["headline_band"]]
            else:                                                  # calmest band away from the caption (faces are busy)
                y = textfit.choose([scene_frames[i]], palette, "top", allowed=("top", "upper", "lower"),
                                   avoid={0: lk.get("band", "")})[0].y
            headlines.append({"from": frame(a_), "frames": max(1, frame(b_) - frame(a_)), "text": text, "y": y})
    log.info(f"creative {c.id}: text bands {[lk.band for lk in looks]} · hook {hook_look.band} · "
             f"palette {palette.style} accent #{palette.accent}")

    mix = dict(plan.get("mix") or {})
    if all(v.get("provider") == "magnific" for v in voice):
        # studio (ElevenLabs) voices come in ~7 LU hotter than edge-tts: unity gain lands the mix near −14 LUFS
        mix.setdefault("voice_volume", MAGNIFIC_VOICE_VOLUME)
        mix.setdefault("music_volume", MAGNIFIC_VOICE_VOLUME * 0.1)
    music = music_for(style, c.id)
    hook_seconds = min(2.2, durs[0])
    props = {
        "fps": FPS, "width": W, "height": H, "totalFrames": frame(total),
        "segments": segments, "voice": voice_tracks, "headlines": headlines,
        "music": ({"src": stage.add(music, "music"), "volume": float(mix.get("music_volume", MUSIC_VOLUME))}
                  if music and mix.get("music", True) else None),
        "captionLines": captions.caption_lines(words, theme["wordsPerLine"], bounds),
        "design": {k: v for k, v in theme.items() if not k.startswith("_")},
        "hook": {"text": tts.clean_text(scenes[0].get("on_screen_text") or c.hook), "seconds": hook_seconds,
                 "look": hook_look.as_dict()},
        "endCard": {"startS": total - END_CARD_S, "appName": b.app_name,
                    "cta": tts.clean_text(c.script.get("cta") or b.cta),
                    "icon": stage.add(app.icon_path, "icon") if app.icon_path and Path(app.icon_path).exists() else None,
                    "bg": stage.add(visuals.gradient(b.app_name), "bg")},
        "aiLabel": AI_LABEL, "voiceVolume": float(mix.get("voice_volume", VOICE_VOLUME)), "fonts": fonts,
        "hookFrame": frame(min(1.0, durs[0] * 0.5)) + 8,
    }
    bg = cover_background(segments, work)
    if bg:
        props["cover"] = {"bg": bg, "hook": props["hook"]["text"], "appName": b.app_name,
                          "icon": props["endCard"]["icon"], "accent": palette.accent, "design": props["design"],
                          "fonts": fonts, "aiLabel": AI_LABEL}
    props["attribution"] = sorted(set(attribution))
    return props


# ---------------------------------------------------------------- main render

def scripted_with_plan(script: dict[str, Any], plan: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The creative's script with the plan's per-scene voiceover / on-screen text applied (fixes land here)."""
    import copy

    sd = copy.deepcopy(script)
    for sc, ps in zip(sd.get("scenes", []), (plan or {}).get("scenes") or []):
        for k in ("voiceover", "on_screen_text"):
            if ps.get(k):
                sc[k] = ps[k]
        if ps.get("query_en"):
            sc.setdefault("visual", {})["query_en"] = ps["query_en"]
    if (plan or {}).get("cta"):
        sd["cta"] = plan["cta"]
    return sd


def render_creative(creative_id: int, plan: Optional[dict[str, Any]] = None, out_dir: Optional[Path] = None,
                    register: bool = True) -> dict[str, Any]:
    """Render one creative. Raises on failure (caller marks the creative failed).

    `plan` comes from the production crew (director + fixes); `out_dir` + `register=False` render a review version
    without touching the creative's published video.
    """
    t0 = time.time()
    with session() as s:
        c = s.get(Creative, creative_id)
    if c is None or not c.script:
        raise RenderError(f"creative {creative_id} has no script")
    if plan:
        s_ = scripted_with_plan(c.script, plan)
        c.script = s_  # in-memory only; the DB script is unchanged until the owner approves
    remotion_ready()
    app = get_app(c.app_id)
    b = brief_of(app)
    style, lang = c.creator_style, c.language
    out_dir = Path(out_dir) if out_dir else OUTPUTS_DIR / c.app_id / f"gen_{c.generation}" / str(c.id)
    work = out_dir / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    scenes = c.script["scenes"]
    # 1. voice per scene
    t_tts = time.time()
    vfiles = [ps.get("voice_file") for ps in ((plan or {}).get("scenes") or [])] + [None] * len(scenes)

    def voice_for_scenes(extra: int = 0) -> list[dict[str, Any]]:
        return [tts.from_file(f, sc["voiceover"], lang) if f and Path(f).exists()
                else tts.synth(sc["voiceover"], lang, style, extra_rate_pct=extra) for sc, f in zip(scenes, vfiles)]

    voice = voice_for_scenes()
    # Length control: Indian TTS voices are slower than scripts estimate — speed up to fit the target length.
    max_content = env_float("MAX_VIDEO_S", 38.0) - END_CARD_S
    spoken = sum(max(v["duration"] + 0.25, 1.5) for v in voice)
    if spoken > max_content and any(v["provider"] != "magnific" for v in voice):
        extra = min(40, int((spoken / max_content - 1) * 100) + 3)
        log.info(f"creative {c.id}: {spoken:.0f}s of voice > {max_content:.0f}s → speaking {extra}% faster")
        voice = voice_for_scenes(extra)
    cost.log_step("tts", voice[0]["provider"], time.time() - t_tts, app_id=c.app_id,
                  paid_equivalent_inr=cost.PAID_EQUIV["tts_scene"] * len(scenes), units=len(scenes))
    durs = [max(v["duration"] + 0.25, 1.5) for v in voice]

    # 2. plan scenes, captions, hook, end card → props; 3. Remotion renders the MP4 + thumbnail
    props = build_props(c, app, work, voice, durs, plan)
    attribution = props.pop("attribution")
    (work / "props.json").write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    # reviewers read timings (captions, scenes, voice) from this copy
    (out_dir / "props.json").write_text(json.dumps({**props, "sceneDurations": durs,
                                                    "voiceProviders": [v["provider"] for v in voice]},
                                                   ensure_ascii=False), encoding="utf-8")
    final, thumb = out_dir / "video.mp4", out_dir / "thumbnail.jpg"
    run_remotion({"props": str(work / "props.json"), "publicDir": str(work), "out": str(final), "thumb": str(thumb),
                  "concurrency": default_concurrency(), "crf": 23}, cwd=work)
    embed_cover(final, thumb)

    # 4. metadata files
    link = tracking.link(app, c)
    (out_dir / "script.json").write_text(json.dumps(c.script, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "caption.txt").write_text(
        f"{c.post_caption}\n\n{' '.join(c.hashtags or [])}\n\n{link}\n\n#AIGenerated — this video was made with AI.\n"
        + ("\nCredits: " + "; ".join(sorted(set(attribution))) if attribution else ""),
        encoding="utf-8",
    )
    if not _keep_work():
        shutil.rmtree(work, ignore_errors=True)
    real_dur = tts.duration(final)
    secs = time.time() - t0
    cost.log_step("render", "remotion", secs, app_id=c.app_id, paid_equivalent_inr=cost.PAID_EQUIV["render"])
    if not register:
        log.info(f"rendered review version of creative {c.id} → {final} ({real_dur:.1f}s) in {secs:.1f}s")
        return {"creative_id": c.id, "path": str(final), "thumb": str(thumb), "duration": real_dur,
                "render_seconds": secs, "attribution": sorted(set(attribution))}
    with session() as s:
        s.exec(delete(Video).where(Video.creative_id == c.id))
        s.commit()
    save(Video(creative_id=c.id, path=str(final), thumbnail_path=str(thumb), duration_s=round(real_dur, 2),
               render_seconds=round(secs, 1), attribution=sorted(set(attribution))))
    c.status = "rendered"
    if not plan:
        save(c)
    else:
        with session() as s:
            row = s.get(Creative, c.id)
            row.status = "rendered"
            s.add(row)
            s.commit()
    log.info(f"rendered creative {c.id} ({lang}/{style}) {real_dur:.1f}s video in {secs:.1f}s")
    return {"creative_id": c.id, "path": str(final), "duration": real_dur, "render_seconds": secs}


def _keep_work() -> bool:
    return env_int("KEEP_RENDER_WORK", 0) == 1


def _safe_render(creative_id: int) -> dict[str, Any]:
    try:
        return render_creative(creative_id)
    except Exception as e:  # noqa: BLE001 — one failed creative never stops the batch
        with session() as s:
            c = s.get(Creative, creative_id)
            if c:
                c.status = "failed"
                c.compliance_issues = list(c.compliance_issues or []) + [f"render: {str(e)[:300]}"]
                s.add(c)
                s.commit()
        return {"creative_id": creative_id, "error": str(e)[:500]}


def render_many(ids: list[int], workers: Optional[int] = None, progress=None) -> list[dict[str, Any]]:
    """Render in a worker pool (default 2 processes). `progress(done, total, result)` is optional."""
    workers = workers or env_int("RENDER_WORKERS", 2)
    results: list[dict[str, Any]] = []
    if workers <= 1 or len(ids) <= 1:
        for i, cid in enumerate(ids):
            r = _safe_render(cid)
            results.append(r)
            if progress:
                progress(i + 1, len(ids), r)
        return results
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_safe_render, cid): cid for cid in ids}
        for i, f in enumerate(as_completed(futs)):
            try:
                r = f.result()
            except Exception as e:  # noqa: BLE001 — worker crash
                r = {"creative_id": futs[f], "error": str(e)}
            results.append(r)
            if progress:
                progress(i + 1, len(ids), r)
    return results


CREW_MODES = ("character", "ai_character")   # need Magnific characters → produced by the production crew


@locked("rendering")
def render_selected(app_id: str, generation: int, workers: Optional[int] = None, progress=None) -> dict[str, Any]:
    """Faceless and images + voice-over videos render now (free). Character / AI-character videos go to the production
    crew (director plan + Magnific job sheet); set FAST_RENDER_ALL=1 to render them here with free stock instead."""
    queue = [c for c in creatives(app_id, generation, selected_for_render=True) if c.status in ("approved", "draft", "failed")]
    crew_ids = [] if env_int("FAST_RENDER_ALL", 0) else [c.id for c in queue if c.visual_mode in CREW_MODES]
    ids = [c.id for c in queue if c.id not in crew_ids]
    t0 = time.time()
    res = render_many(ids, workers=workers, progress=progress)
    ok = [r for r in res if "error" not in r]
    runs = []
    if crew_ids:
        from engine.production import pipeline
        from engine.production.common import latest_run

        for cid in crew_ids:
            prev = latest_run(cid)
            if prev and prev.status not in ("approved", "rejected", "failed"):
                runs.append(prev.id)
                continue
            try:
                runs.append(pipeline.start(cid, track=False).id)
            except Exception as e:  # noqa: BLE001 — one failed creative never stops the batch
                log.warning(f"crew start failed for {cid}: {e}")
        log.info(f"{len(runs)} character/AI-character videos handed to the production crew (Magnific)")
    return {"requested": len(queue), "rendered": len(ok), "failed": len(res) - len(ok),
            "sent_to_crew": len(runs), "crew_runs": runs, "wall_seconds": round(time.time() - t0, 1),
            "errors": [r for r in res if "error" in r][:5]}
