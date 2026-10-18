"""MAGNIFIC ASSETS — the jobs the director (or a reviewer's fix) needs from Magnific: natural voice lines and the hook
clip. The engine can't call Magnific itself (MCP only): it writes a job sheet with the exact tool calls, the
assistant runs them through the Magnific MCP, and `import_file` brings the results back and logs the credits."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests
from sqlmodel import select

from engine import cost
from engine.db import Creative, Ledger, ProductionRun, save, session
from engine.production.common import cfg, persist, run_dir, say
from engine.settings import creator_styles, market


def mcfg() -> dict[str, Any]:
    return cfg()["magnific"]


def credits_spent_this_month(app_id: Optional[str] = None) -> int:
    """Magnific credits imported this month — for one app, or all apps."""
    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with session() as s:
        # only asset imports carry credits (step "magnific:<tool>"); the render's "tts" rows just count voice lines used
        q = select(Ledger).where(Ledger.provider == "magnific", Ledger.step.startswith("magnific:"),
                                 Ledger.created_at >= start)
        if app_id:
            q = q.where(Ledger.app_id == app_id)
        rows = s.exec(q).all()
    return int(sum(r.units for r in rows))


def budget_left(app_id: Optional[str] = None) -> int:
    """Credits left this month in the shared Magnific pool (all apps draw from it; spend is still logged per app)."""
    return int(mcfg()["monthly_credit_cap"]) - credits_spent_this_month()


def voice_id_for(lang: str, style: str, app_id: Optional[str] = None) -> Optional[int]:
    """Cast voice for this language + creator style (None → keep edge-tts). A brief's `voices` overrides the default."""
    cast = None
    if app_id:
        from engine.brief import brief_of
        from engine.db import get_app

        app = get_app(app_id)
        cast = (brief_of(app).voices or {}).get(lang) if app else None
    cast = cast or (mcfg()["voice"].get("cast") or {}).get(lang)
    if not cast:
        return None
    gender = creator_styles()[style].get("voice_gender", "female")
    ids = cast.get(gender) or next(iter(cast.values()))
    calm = style in (mcfg()["voice"].get("calm_styles") or [])
    return int(ids[1] if calm and len(ids) > 1 else ids[0])


def per_video_cap(c: Creative) -> int:
    """Hard credit cap for one video: its visual mode's cap, else the default."""
    modes = mcfg().get("modes") or {}
    return int((modes.get(c.visual_mode) or {}).get("cap", mcfg()["per_video_credit_cap"]))


def _voice_request(c: Creative, scene: int, text: str, delivery: str = "",
                   voice_id: Optional[int] = None) -> Optional[dict[str, Any]]:
    """eleven_v3 reads a short inline [tag] as a performance cue (Magnific `audio_tts_direction`); the tag is never
    spoken and never reaches the captions (those use the plan's voiceover text)."""
    v = mcfg()["voice"]
    if delivery and v["model"] == "eleven_v3":
        text = f"[{delivery}] {text}"
    vid = voice_id or voice_id_for(c.language, c.creator_style, c.app_id)
    if not v.get("enabled") or vid is None:
        return None
    return {"id": f"voice_s{scene}", "kind": "voice", "scene": scene, "status": "pending",
            "credits": int(v["credits_per_line"]), "file": None, "creation": None,
            "mcp": {"tool": "audio_tts", "arguments": {"text": text, "model": v["model"], "voiceId": vid,
                                                        "stability": v["stability"], "speed": v["speed"]}}}


def _clip_request(scene: int, prompt: str) -> dict[str, Any]:
    h = mcfg()["hook_clip"]
    return {"id": f"clip_s{scene}", "kind": "video", "scene": scene, "status": "pending", "credits": int(h["credits"]),
            "file": None, "creation": None,
            "mcp": {"tool": "video_generate", "arguments": {"slug": h["slug"], "duration": h["duration"],
                                                             "aspectRatio": "9:16", "resolution": h["resolution"],
                                                             "prompt": prompt}}}


def _image_request(scene: int, prompt: str) -> dict[str, Any]:
    im = mcfg()["scene_image"]
    m = market()
    full = (f"{prompt.strip().rstrip('.')}. Vertical 9:16 photorealistic candid smartphone-style photo, set {m['locale']}, "
            "natural light, real everyday people, no text, no logos, no watermarks; any phone screen faces away from the camera "
            f"(never draw an app or store interface — only real app screenshots may show the product); any money shown is {m['money']}.")
    return {"id": f"image_s{scene}", "kind": "image", "scene": scene, "status": "pending", "credits": int(im["credits"]),
            "file": None, "creation": None,
            "mcp": {"tool": "images_generate", "arguments": {"prompt": full, "aspectRatio": "9:16", "mode": im["mode"]}}}


def persona_suffix() -> str:
    m = market()
    return (f". Vertical 9:16 photorealistic candid photo {m['locale']}, natural light, same person as the reference, "
            f"no text, no logos, no watermarks; any phone screen faces away from the camera; any money shown is {m['money']}.")


TALK_CREDITS_PER_S = 180      # measured: OmniHuman / Veed lip-sync ≈ 150–200 credits per second of speech
WORDS_PER_S = 2.3


def _persona(app_id: str, key: str) -> Any:
    from engine.production.personas import usable

    return next((p for p in usable(app_id) if p.key == key), None)


def _persona_image_request(c: Creative, scene: int, key: str, moment: str) -> Optional[dict[str, Any]]:
    p = _persona(c.app_id, key)
    if p is None:
        return None
    im = mcfg()["scene_image"]
    # with a library/portrait reference the face comes from the reference, so the prompt only describes the moment
    # (the casting look also contains the portrait's own setting and pose, which would fight the scene)
    who = p.name if (p.library_id or p.approved.get("creation")) else f"{p.name}, {p.look_prompt.split('Setting')[0].strip()}"
    args: dict[str, Any] = {"prompt": f"{who}: {moment.strip().rstrip('.')}{persona_suffix()}",
                            "aspectRatio": "9:16", "mode": im["mode"]}
    if p.library_id:
        args["references"] = [{"type": "character", "identifier": str(p.library_id)}]
    elif p.approved.get("creation"):
        args["references"] = [{"type": "image", "identifier": p.approved["creation"]}]
    return {"id": f"pimg_s{scene}", "kind": "image", "scene": scene, "status": "pending", "credits": int(im["credits"]),
            "file": None, "creation": None, "persona": key, "ref_files": [p.approved.get("file")] if p.approved.get("file") else [],
            "mcp": {"tool": "images_generate", "arguments": args}}


def _persona_clip_request(scene: int, motion: str, start_job: str) -> dict[str, Any]:
    return {"id": f"clip_s{scene}", "kind": "video", "scene": scene, "status": "pending", "credits": 140,
            "file": None, "creation": None, "depends_on": [start_job],
            "mcp": {"tool": "video_generate", "arguments": {
                "slug": "kling-25", "duration": 5, "aspectRatio": "9:16", "resolution": "720p",
                "prompt": (motion or "subtle natural movement, slow push-in") + ", realistic, no text",
                "keyframes": {"start": {"type": "image", "url": f"{{{start_job}.creation}}"}}}}}


def _talking_request(c: Creative, scene: int, key: str, words: int) -> Optional[dict[str, Any]]:
    p = _persona(c.app_id, key)
    if p is None or not p.approved.get("creation"):
        return None
    secs = max(2.0, words / WORDS_PER_S)
    return {"id": f"talk_s{scene}", "kind": "talking", "scene": scene, "status": "pending",
            "credits": int(round(secs * TALK_CREDITS_PER_S / 10) * 10), "file": None, "creation": None,
            "persona": key, "depends_on": [f"voice_s{scene}", f"face_s{scene}"],
            "mcp": {"tool": "video_speak", "arguments": {"mode": "omnihuman", "imageUrl": f"{{face_s{scene}.creation}}",
                                                          "audioUrl": f"{{voice_s{scene}.creation}}"}}}


def _talking_face_request(c: Creative, scene: int, key: str) -> Optional[dict[str, Any]]:
    """The presenter's on-camera frame for a talking scene (same face via the reference), fed to lip-sync."""
    r = _persona_image_request(c, scene, key, "talking directly to camera, chest-up framing, natural expression, "
                                              + market()["background"])
    if r:
        r["id"], r["talk_face"] = f"face_s{scene}", True
    return r


def requests_for(c: Creative, plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Magnific jobs this plan needs, highest value first, within the video's mode cap and the monthly pool:
    natural voice → talking hook/CTA → persona photos → persona clip → one-off scene photos. Anything that doesn't
    fit is downgraded in the plan (talking → persona photo, clip → photo, photo → stock) so the video still works."""
    cap = min(per_video_cap(c), budget_left(c.app_id))
    out: list[dict[str, Any]] = []
    spent = lambda: sum(r["credits"] for r in out)  # noqa: E731
    scenes = plan["scenes"]
    presenter = next((s.get("persona") for s in scenes if s.get("shot") == "talking"), None)
    host = _persona(c.app_id, presenter) if presenter else None

    def speaker(s: dict[str, Any]) -> Optional[int]:
        """AI-character videos are voiced end to end by their presenter (on camera at the hook/CTA, voice-over in
        between); other modes use the creator style's narrator."""
        return host.voice_id if host is not None and host.voice_id else None

    voice = [_voice_request(c, i, s["voiceover"], s.get("delivery", ""), speaker(s)) for i, s in enumerate(scenes)]
    has_voice = all(voice) and sum(r["credits"] for r in voice) <= cap
    if has_voice:
        out += voice
    for i, s in enumerate(scenes):          # talking needs the voice line as its audio
        if s["shot"] != "talking":
            continue
        r = _talking_request(c, i, s["persona"], len(s["voiceover"].split())) if has_voice else None
        face = _talking_face_request(c, i, s["persona"]) if r else None
        if r and face and spent() + r["credits"] + face["credits"] <= cap:
            out += [face, r]
        else:
            s["shot"], s["talking"] = "persona_image", False
    for i, s in enumerate(scenes):
        if s["shot"] not in ("persona_image", "persona_clip"):
            continue
        img = _persona_image_request(c, i, s["persona"], s.get("image_prompt_en") or s.get("query_en", ""))
        if img is None:                      # character not approved yet → one-off photo of the moment
            s["shot"], s["priority"] = "scene_image", True      # the person's photo is bought before any other scene photo
            continue
        if spent() + img["credits"] > cap:
            s["shot"] = "stock_photo" if plan.get("mode") == "images_vo" else "stock"
            continue
        out.append(img)
        if s["shot"] == "persona_clip":
            clip = _persona_clip_request(i, s.get("motion", ""), img["id"])
            if spent() + clip["credits"] <= cap:
                out.append(clip)
            else:
                s["shot"] = "persona_image"
    stock_first(c, plan)                 # free stock wherever it matches; the rest stays scene_image (Magnific)
    for i, s in sorted(enumerate(scenes), key=lambda x: (not x[1].get("priority"), x[0])):
        if s["shot"] != "scene_image":
            continue
        r = _image_request(i, s.get("image_prompt_en") or s.get("query_en", ""))
        if mcfg()["scene_image"]["enabled"] and spent() + r["credits"] <= cap:
            out.append(r)
        else:
            s["shot"] = "stock_photo" if plan.get("mode") == "images_vo" else "stock"
    return out


def stock_first(c: Creative, plan: dict[str, Any]) -> None:
    """For every scene stock could cover (settings, stills, one-off moments — not the app's recurring characters),
    try free Pexels first and keep it when it matches the scene well enough; otherwise the scene becomes a Magnific
    photo. Uses no LLM tokens. The match score is kept on the scene for the reviewers/dashboard."""
    from engine import visuals

    thr = float(mcfg().get("stock_match_threshold", 0.6))
    stills = plan.get("mode") == "images_vo"
    for s in plan["scenes"]:
        if s["shot"] not in ("stock", "stock_photo", "scene_image") or s.get("asset"):
            continue
        query = s.get("query_en") or s.get("image_prompt_en") or ""
        if not query:
            continue
        r = visuals.best_stock(query, photo_only=stills or s["shot"] == "stock_photo", app_id=c.app_id)
        s["stock_match"] = r.get("match", 0.0)
        if r["kind"] != "none" and r["match"] >= thr:
            s["asset"], s["attribution"] = r["path"], r.get("attribution", "")
            s["shot"] = "stock_photo" if r["kind"] == "image" else "stock"
        else:
            s["shot"] = "scene_image"
            s["image_prompt_en"] = s.get("image_prompt_en") or query


def ready(run: ProductionRun) -> list[dict[str, Any]]:
    """Pending jobs whose inputs are done, with `{job.creation}` placeholders filled in (run these now)."""
    done = {r["id"]: r for r in run.asset_requests or [] if r["status"] == "done"}
    out = []
    for r in pending(run):
        deps = r.get("depends_on") or []
        if any(d not in done or not done[d].get("creation") for d in deps):
            continue
        args = json.loads(json.dumps(r["mcp"]["arguments"]))
        txt = json.dumps(args)
        for d in deps:
            txt = txt.replace(f"{{{d}.creation}}", done[d]["creation"])
        out.append({"request_id": r["id"], "tool": r["mcp"]["tool"], "credits": r["credits"],
                    "arguments": json.loads(txt)})
    return out


def add_request(run: ProductionRun, kind: str, scene: int, prompt: str = "") -> Optional[dict[str, Any]]:
    """A fix needs a new asset (re-record a voice line, or a hook clip). Replaces an older request for the same slot."""
    if not mcfg().get("allow_paid_revisions", False):
        return None                          # cost policy: a fix round may never buy assets; the owner decides that
    c = _creative(run)
    if kind == "voice":
        sc = run.plan["scenes"][scene]
        r = _voice_request(c, scene, sc["voiceover"], sc.get("delivery", ""))
    elif kind in ("image", "persona_image") and run.plan["scenes"][scene].get("persona"):
        sc = run.plan["scenes"][scene]      # keep the recurring face: re-shoot with the character reference
        r = _persona_image_request(c, scene, sc["persona"], prompt or sc.get("image_prompt_en", ""))
        # the current photo stays in place until the re-shoot arrives (a skipped re-shoot keeps it)
    elif kind == "talking" and run.plan["scenes"][scene].get("persona"):
        sc = run.plan["scenes"][scene]
        r = _talking_request(c, scene, sc["persona"], len(sc["voiceover"].split()))
        voice = next((x for x in run.asset_requests or [] if x["id"] == f"voice_s{scene}" and x["status"] == "done"), None)
        if r and voice is None:
            r = None                        # talking needs the recorded line
    elif kind == "image" and prompt and mcfg()["scene_image"]["enabled"]:
        r = _image_request(scene, prompt)
    elif kind == "video" and prompt:
        r = _clip_request(scene, prompt)
        run.plan["scenes"][scene]["clip_prompt_en"] = prompt   # the imported clip becomes the scene's asset
    else:
        r = None
    if r is None or r["credits"] > budget_left(run.app_id):
        return None
    keep = [x for x in run.asset_requests or [] if x["id"] != r["id"]]
    run.asset_requests = keep + [r]
    return r


def pending(run: ProductionRun) -> list[dict[str, Any]]:
    return [r for r in (run.asset_requests or []) if r["status"] == "pending"]


def write_job_sheet(run: ProductionRun) -> Path:
    """The exact Magnific MCP calls still to run for this video (read by the assistant)."""
    c = _creative(run)
    p = run_dir(run) / "magnific_jobs.json"
    p.write_text(json.dumps({
        "run_id": run.id, "creative_id": c.id, "app_id": c.app_id, "language": c.language,
        "voice_direction": (run.plan or {}).get("voice_direction", ""),
        "import_with": f"python cli.py crew import {run.id} <request_id> <file-or-url> [--credits N]",
        "credits_estimate": sum(r["credits"] for r in pending(run)), "budget_left": budget_left(run.app_id),
        "ready_now": ready(run),
        "waiting_on_inputs": [{"request_id": r["id"], "needs": r.get("depends_on")} for r in pending(run)
                              if r["id"] not in {j["request_id"] for j in ready(run)}],
        "jobs": [{"request_id": r["id"], **r["mcp"]} for r in pending(run)],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def import_file(run: ProductionRun, request_id: str, source: str, credits: Optional[int] = None,
                creation: Optional[str] = None) -> dict[str, Any]:
    """Bring a finished Magnific asset (local file or download URL) into the run and log its credits."""
    req = next((r for r in run.asset_requests or [] if r["id"] == request_id), None)
    if req is None:
        raise ValueError(f"no asset request {request_id} on run {run.id}")
    adir = run_dir(run) / "assets"
    adir.mkdir(exist_ok=True)
    ext = {"voice": ".mp3", "image": ".png"}.get(req["kind"], ".mp4")   # video / talking → .mp4
    src_ext = Path(source.split("?")[0]).suffix.lower()
    if src_ext in (".mp3", ".wav", ".m4a", ".mp4", ".mov", ".webm", ".jpg", ".png"):
        ext = src_ext
    dest = adir / f"{request_id}{ext}"
    if source.startswith(("http://", "https://")):
        r = requests.get(source, timeout=180)
        r.raise_for_status()
        dest.write_bytes(r.content)
    else:
        shutil.copy2(source, dest)
    used = int(credits if credits is not None else req["credits"])
    req.update({"status": "done", "file": str(dest), "creation": creation, "credits_used": used})
    scene = run.plan["scenes"][req["scene"]]
    if req["kind"] == "voice":
        scene["voice_file"] = str(dest)
    elif req["kind"] == "talking":
        scene["asset"], scene["talking"] = str(dest), True
    elif req.get("talk_face"):
        pass  # input for the lip-sync job only
    elif req["kind"] == "image" and scene.get("asset", "") and str(scene["asset"]).endswith((".mp4", ".mov")):
        pass  # the clip made from this photo is already in place
    else:
        scene["asset"] = str(dest)
    run.asset_requests = list(run.asset_requests)
    run.plan = dict(run.plan)
    persist(run)
    cost.log_step(f"magnific:{req['mcp']['tool']}", "magnific", 0.0, app_id=run.app_id,
                  cost_inr=round(used * float(mcfg()["inr_per_credit"]), 2), units=used)
    say(run, "assets", f"Imported {request_id} ({used} credits)")
    return req


def skip_pending(run: ProductionRun, why: str) -> int:
    """Carry on without the missing assets (edge-tts voice / stock footage)."""
    n = 0
    for r in run.asset_requests or []:
        if r["status"] == "pending":
            r["status"], n = "skipped", n + 1
            if r["kind"] in ("video", "image", "talking") and not run.plan["scenes"][r["scene"]].get("asset"):
                sc = run.plan["scenes"][r["scene"]]
                done_img = any(x["status"] == "done" and x["scene"] == r["scene"] and x["kind"] == "image"
                               for x in run.asset_requests)
                sc["shot"] = "persona_image" if done_img else ("stock_photo" if run.plan.get("mode") == "images_vo" else "stock")
                sc["talking"] = False
    run.asset_requests = list(run.asset_requests or [])
    run.plan = dict(run.plan)
    if n:
        say(run, "assets", f"Skipped {n} Magnific jobs ({why}); using the free fallback")
    persist(run)
    return n


def _creative(run: ProductionRun) -> Creative:
    from engine.production.common import creative_of

    return creative_of(run)


# ---------------------------------------------------------------- unattended fulfilment (Magnific REST API)

def fulfil_api(run: ProductionRun, credit_limit: Optional[int] = None) -> dict[str, int]:
    """Run every pending job through the Magnific API in dependency order (voices/photos → clips/talking).
    Stops before any job that would take spend past `credit_limit` (the owner-approved amount)."""
    import base64

    from engine import magnific

    spent = done = 0
    while True:
        jobs = [j for j in ready(run) if j["tool"] != "audio_tts"]   # voices come from the MCP (cast voices)
        if not jobs:
            break
        progressed = False
        for j in jobs:
            req = next(r for r in run.asset_requests if r["id"] == j["request_id"])
            if credit_limit is not None and spent + req["credits"] > credit_limit:
                say(run, "assets", f"Stopped: {req['id']} would pass the approved {credit_limit} credits")
                return {"done": done, "spent": spent, "stopped": 1}
            job = {**req, "mcp": {"tool": j["tool"], "arguments": j["arguments"]}}
            if req.get("ref_files"):
                job["ref_urls"] = [base64.b64encode(Path(f).read_bytes()).decode() for f in req["ref_files"] if f and Path(f).exists()]
            kind, body = magnific.body_for(job)
            url, _tid = magnific.run(kind, body)
            import_file(run, req["id"], url, credits=req["credits"], creation=url)   # the URL feeds dependent jobs
            spent, done, progressed = spent + req["credits"], done + 1, True
        if not progressed:
            break
    return {"done": done, "spent": spent, "stopped": 0}
