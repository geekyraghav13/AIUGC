"""Magnific REST API client (MAGNIFIC_API_KEY in .env) — lets an approved batch run unattended.

Every call follows the same shape: POST /v1/ai/<tool> → {"data": {"task_id"}}, then poll GET /v1/ai/<tool>/<task_id>
until status is COMPLETED and `generated` holds the result URLs. Auth: header `x-magnific-api-key`.
Credits are taken from the prices measured with Magnific's own cost simulator (the API doesn't return them) and
logged per job; a batch never spends beyond what the owner approved.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

from engine.settings import env, log

BASE = "https://api.magnific.com"
TOOLS = {
    # job kind → (API path, credits per job as measured with simulate_cost on 2026-09-29)
    "voice": ("/v1/ai/voiceover/elevenlabs-turbo-v2-5", 16),   # not used: voices go through the MCP
    "image": ("/v1/ai/text-to-image/seedream-v4", 50),
    "image_ref": ("/v1/ai/text-to-image/seedream-v4-edit", 50),       # same face: reference image(s)
    "clip": ("/v1/ai/image-to-video/kling-v2-5-720p", 140),
    "talking": ("/v1/ai/video/omni-human-1-5", 180),                 # per second of speech
}


class MagnificError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(env("MAGNIFIC_API_KEY"))


def _headers() -> dict[str, str]:
    return {"x-magnific-api-key": env("MAGNIFIC_API_KEY"), "Content-Type": "application/json"}


def _req(method: str, path: str, body: Optional[dict[str, Any]] = None, timeout: int = 60) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(4):
        try:
            r = requests.request(method, BASE + path, headers=_headers(), json=body, timeout=timeout)
            if r.status_code in (401, 403):
                raise MagnificError(f"Magnific API key rejected ({r.status_code}): {r.text[:200]}")
            if r.status_code == 402:
                raise MagnificError("Magnific: not enough credits (402) — nothing more will be spent")
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} {r.text[:200]}")
            if r.status_code >= 400:
                raise MagnificError(f"Magnific {method} {path} failed ({r.status_code}): {r.text[:300]}")
            return r.json()
        except MagnificError:
            raise
        except requests.RequestException as e:
            last = e
            time.sleep(min(30, 3 * 2 ** attempt))
    raise MagnificError(f"Magnific {method} {path} failed after retries: {last}")


def check() -> dict[str, Any]:
    """Free check: list the image tool's tasks (no generation, no credits). Raises if the key is missing/invalid."""
    if not enabled():
        raise MagnificError("MAGNIFIC_API_KEY is not set in .env")
    # look up a task that can't exist: an accepted key answers 404 "not found", a bad key 401/403
    r = requests.get(f"{BASE}{TOOLS['image'][0]}/00000000-0000-0000-0000-000000000000", headers=_headers(), timeout=30)
    if r.status_code in (401, 403):
        raise MagnificError(f"Magnific API key rejected ({r.status_code})")
    return {"ok": True, "key_accepted": True, "status": r.status_code}


def submit(kind: str, body: dict[str, Any]) -> str:
    data = _req("POST", TOOLS[kind][0], body).get("data") or {}
    tid = data.get("task_id") or data.get("id")
    if not tid:
        raise MagnificError(f"Magnific {kind}: no task id in response")
    return str(tid)


def wait(kind: str, task_id: str, timeout_s: int = 900, every_s: int = 5) -> list[str]:
    """Poll until COMPLETED; returns the generated URLs."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        data = _req("GET", f"{TOOLS[kind][0]}/{task_id}").get("data") or {}
        status = str(data.get("status", "")).upper()
        if status == "COMPLETED":
            gen = data.get("generated") or []
            urls = [g if isinstance(g, str) else (g.get("url") or "") for g in gen]
            if not any(urls):
                raise MagnificError(f"Magnific {kind} {task_id}: completed without output")
            return [u for u in urls if u]
        if status == "FAILED":
            raise MagnificError(f"Magnific {kind} {task_id} failed: {str(data)[:300]}")
        time.sleep(every_s)
    raise MagnificError(f"Magnific {kind} {task_id}: timed out after {timeout_s}s")


def run(kind: str, body: dict[str, Any]) -> tuple[str, str]:
    """Submit + wait. Returns (first result URL, task id)."""
    tid = submit(kind, body)
    log.info(f"magnific {kind} task {tid} submitted")
    return wait(kind, tid)[0], tid


# ---------------------------------------------------------------- request builders (MCP job → REST body)

def body_for(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate one of our asset jobs (written in Magnific-MCP terms) into a REST call (kind, body)."""
    tool, a = job["mcp"]["tool"], job["mcp"]["arguments"]
    if tool == "audio_tts":
        # the REST voiceover only takes raw ElevenLabs voice ids, not Magnific's cast Indian voices → voices run via MCP
        raise MagnificError("voice lines run through the Magnific MCP (cast Indian voices)")
    if tool == "images_generate":
        refs = job.get("ref_urls") or []
        body = {"prompt": a["prompt"], "aspect_ratio": "social_story_9_16"}
        if refs:
            return "image_ref", {**body, "reference_images": refs[:5]}
        return "image", body
    if tool == "video_generate":
        return "clip", {"image": a["keyframes"]["start"]["url"], "prompt": a["prompt"], "duration": "5",
                        "aspect_ratio": "social_story_9_16"}
    if tool == "video_speak":
        return "talking", {"image_url": a["imageUrl"], "audio_url": a["audioUrl"], "resolution": "720p"}
    raise MagnificError(f"no REST mapping for {tool}")
