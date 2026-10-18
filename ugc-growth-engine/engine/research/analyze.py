"""Step 4 — AI video analysis: Gemini watches each selected Short and fills a taxonomy-aligned schema.

Tag values come from config/taxonomy.yaml, formats.yaml, languages.yaml and creator_styles.yaml, so market
patterns map 1:1 to our generation tags. Cached by (app, video id): re-runs cost 0 Gemini calls.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
from functools import lru_cache
from typing import Any, Optional

from engine import cache, cost, llm
from engine.research import cfg
from engine.scripts import length_bucket
from engine.settings import creator_styles, env, formats, languages, log, taxonomy

PROMPT_VERSION = "v1"
PRODUCTION_STYLES = ["talking_head", "in_car", "skit", "animation", "green_screen", "screen_demo", "text_broll",
                     "image_slideshow", "other"]
TRIGGERS = ["fear_of_loss", "save_money", "confusion_to_clarity", "anger", "curiosity", "social_proof", "other"]
EXTRA_CTA = ["follow", "comment_keyword", "none"]
BOOL_FIELDS = ["is_relevant", "uses_numbers", "shows_app_or_screen"]
TEXT_FIELDS = ["topic_angle", "persona_guess", "hook_first_3s", "message", "why_it_worked"]

_lock = threading.Lock()
_last_call = 0.0
_video_usage = threading.local()


def allowed() -> dict[str, list[str]]:
    """Allowed value per tag field ("other" is always accepted as the last-resort value)."""
    return {
        "hook_type": list(taxonomy()["hook_types"]) + ["other"],
        "format": list(formats()) + ["other"],
        "language": list(languages()) + ["english", "other"],
        "production_style": PRODUCTION_STYLES,
        "closest_creator_style": list(creator_styles()) + ["other"],
        "emotional_trigger": TRIGGERS,
        "cta_type": list(taxonomy()["cta_types"]) + EXTRA_CTA + ["other"],
        "length_bucket": list(taxonomy()["length_buckets"]) + ["other"],
    }


def normalise(v: Any) -> str:
    return re.sub(r"[\s\-/]+", "_", str(v or "").strip().lower())


def validate(a: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalise values; return (analysis, invalid tag fields)."""
    out = dict(a)
    bad = []
    for f, vals in allowed().items():
        v = normalise(out.get(f))
        if v in vals:
            out[f] = v
        else:
            bad.append(f)
    for f in BOOL_FIELDS:
        v = out.get(f)
        out[f] = v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1")
    for f in TEXT_FIELDS:
        out[f] = str(out.get(f) or "").strip()
    if not isinstance(out.get("structure"), list):
        out["structure"] = [s.strip() for s in str(out.get("structure") or "").split("→") if s.strip()]
    return out, bad


def force_other(a: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    out = dict(a)
    for f in fields:
        out[f] = "other"
    return out


# ---------------------------------------------------------------- Gemini

@lru_cache(maxsize=1)
def pick_model() -> str:
    """Newest stable Flash model on this key (falls back to preview, then the gemini-flash-latest alias)."""
    if env("RESEARCH_GEMINI_MODEL"):
        return env("RESEARCH_GEMINI_MODEL")
    skip = ("lite", "image", "tts", "live", "audio", "embedding", "thinking", "exp", "robotics", "computer", "native")
    cands = []
    try:
        from google import genai

        client = genai.Client(api_key=env("GEMINI_API_KEY"))  # keep a reference: the pager needs a live client
        for m in client.models.list():
            name = m.name.replace("models/", "")
            actions = getattr(m, "supported_actions", None) or []
            if "flash" not in name or any(s in name for s in skip):
                continue
            if actions and "generateContent" not in actions:
                continue
            ver = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
            if ver:
                cands.append(("preview" not in name, float(ver.group(1)), -len(name), name))
    except Exception as e:  # noqa: BLE001
        log.warning(f"could not list Gemini models: {e}")
    return sorted(cands, reverse=True)[0][3] if cands else "gemini-flash-latest"


def _throttle() -> None:
    global _last_call
    with _lock:
        wait = _last_call + float(cfg()["gemini_interval_s"]) - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _gemini_video(url: str, prompt: str, model: str, retries: int = 3) -> dict[str, Any]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=env("GEMINI_API_KEY"))
    for attempt in range(retries):
        _throttle()
        cost.check_budget()
        try:
            resp = client.models.generate_content(
                model=model,
                contents=types.Content(parts=[types.Part(file_data=types.FileData(file_uri=url)),
                                              types.Part(text=prompt)]),
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2,
                                                   http_options=types.HttpOptions(timeout=180_000)),
            )
            _video_usage.last = cost.usage_cost(resp)
            data = llm.extract_json(resp.text or "")
            return data[0] if isinstance(data, list) else data
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if isinstance(e, cost.BudgetExceeded) or any(
                    s in msg for s in ("API key not valid", "PERMISSION_DENIED", "402", "credits are depleted", "billing")):
                raise
            wait = 40 * (attempt + 1) if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) else 5
            log.warning(f"gemini video retry {attempt + 1} ({msg[:120]}) — waiting {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Gemini could not analyse {url}")


def _mock_analysis(v: dict[str, Any]) -> dict[str, Any]:
    """Offline stand-in: deterministic, biased toward a few values so patterns emerge."""
    rng = random.Random(int(hashlib.md5(v["video_id"].encode()).hexdigest()[:8], 16))
    al = allowed()
    pick = lambda f, bias: bias if rng.random() < 0.45 else rng.choice(al[f][:-1])  # noqa: E731
    fmt = pick("format", "problem_solution")
    return {
        "is_relevant": rng.random() < 0.9,
        "topic_angle": rng.choice(["month-end money runs out", "where did my salary go", "splitting rent with flatmates",
                                   "UPI spends add up silently", "budget before festive sale"]),
        "persona_guess": rng.choice(["college student", "first-job professional", "young couple"]),
        "hook_first_3s": rng.choice(["Salary aate hi khatam?", "Ye galti mat karna", "3 UPI habits jo paisa khaate hain",
                                     "POV: month end aur wallet khaali"]) + f" ({v['video_id'][-3:]})",
        "hook_type": pick("hook_type", "relatable_pain"), "format": fmt,
        "language": pick("language", "hinglish"), "production_style": rng.choice(PRODUCTION_STYLES[:-1]),
        "closest_creator_style": pick("closest_creator_style", "bestie_narrator"),
        "emotional_trigger": rng.choice(TRIGGERS[:-1]), "message": rng.choice(
            ["track small UPI spends", "set a monthly budget", "plan on salary day", "split bills fairly"]),
        "structure": ["hook", "problem", "tip", "cta"], "uses_numbers": rng.random() < 0.5,
        "shows_app_or_screen": rng.random() < 0.4, "cta_type": pick("cta_type", "follow"),
        "length_bucket": length_bucket(v.get("duration_s", 30)),
        "why_it_worked": "mock analysis (offline dry run)",
    }


def analyze_video(v: dict[str, Any], app_desc: str, app_id: str, mode: str) -> tuple[Optional[dict[str, Any]], str, bool]:
    """Returns (analysis | None, model, gemini_called). Never raises for a single video."""
    k = cache.key("market_analysis", PROMPT_VERSION, app_id, v["video_id"])
    hit = cache.get_json("market_analysis", k)
    if hit:
        return hit["analysis"], hit["model"], False
    al = allowed()
    prompt = llm.render_prompt(
        "research_analyze",
        hook_types=al["hook_type"], formats=al["format"], languages=al["language"],
        production_styles=al["production_style"], creator_styles=al["closest_creator_style"],
        triggers=al["emotional_trigger"], cta_types=al["cta_type"], length_buckets=al["length_bucket"],
        format_help={k2: " → ".join(v2["structure"][:3]) for k2, v2 in formats().items()},
        style_help={k2: v2["description"] for k2, v2 in creator_styles().items()},
        app=app_desc, title=v["title"], channel=v["channel_title"], views=f"{v['views']:,}",
        outlier=f"{v.get('outlier_score') or 0:.1f}", duration=str(v["duration_s"]),
    )
    called = False
    t0 = time.time()
    try:
        if mode == "mock":
            raw, model = _mock_analysis(v), "mock"
        elif v.get("platform") == "instagram":
            # Gemini can't open Instagram URLs and we never download videos (D8.5): analyse caption/hashtags/transcript
            model = f"text:{llm.active_provider()}"
            extra = (f"\n\nYou cannot watch this Reel. Infer from its text.\nCaption: {v.get('caption', '')[:1500]}"
                     f"\nHashtags: {v.get('hashtags', [])}\nTranscript: {v.get('transcript') or '(not available)'}")
            raw = llm.complete_json("research_analyze_reel", prompt + extra, temperature=0.2, app_id=app_id)
        elif env("GEMINI_API_KEY"):
            model = pick_model()
            raw = _gemini_video(v["url"], prompt, model)
            called = True
        else:
            # no Gemini key: text-only fallback from title/description (clearly labelled)
            model = f"text-only:{llm.active_provider()}"
            raw = llm.complete_json("research_analyze_text", prompt + "\n(You cannot watch the video: infer from the title.)",
                                    temperature=0.2, app_id=app_id)
    except Exception as e:  # noqa: BLE001 — log and skip this video
        log.warning(f"analysis skipped for {v['url']}: {str(e)[:160]}")
        return None, "", called
    if not isinstance(raw, dict):
        return None, "", called
    a, bad = validate(raw)
    if bad and mode != "mock":
        try:
            fixed = llm.complete_json(
                "research_fix_tags",
                llm.render_prompt("research_fix_tags", invalid=bad, allowed={f: al[f] for f in bad},
                                  analysis=json.dumps(a, ensure_ascii=False)),
                temperature=0.0, app_id=app_id)
            a, bad = validate({**a, **{f: fixed.get(f) for f in bad}})
        except llm.LLMError as e:
            log.debug(f"tag fix failed: {e}")
    a = force_other(a, bad)
    a["length_bucket"] = length_bucket(v["duration_s"])  # measured, not guessed
    if called:
        pt, ot, inr = getattr(_video_usage, "last", (0, 0, 0.0))
        cost.log_step("gemini:video_analysis", model, time.time() - t0, app_id=app_id, cost_inr=inr,
                      paid_equivalent_inr=cost.PAID_EQUIV["llm_call"] * 3, units=max(1, pt + ot))
    cache.put_json("market_analysis", k, {"analysis": a, "model": model})
    return a, model, called
