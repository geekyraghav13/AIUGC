"""LLM provider abstraction: Gemini (free tier) → Groq (free tier) → Ollama (local).

Every call is cached on disk, throttled, retried with exponential backoff and JSON-validated.
`LLM_PROVIDER=mock` gives deterministic offline output (used by tests / no-key dry runs).
"""
from __future__ import annotations

import json
import re
import string
import threading
import time
from typing import Any, Callable, Optional, Type, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from engine import cache, cost
from engine.settings import PROMPTS_DIR, env, env_float, log, market

T = TypeVar("T", bound=BaseModel)

_lock = threading.Lock()
_last_call = 0.0
_usage = threading.local()  # (prompt_tokens, output_tokens, ₹) of the last paid call in this thread


class LLMError(RuntimeError):
    pass


BILLING_MARKERS = ("402", "credits are depleted", "billing")


def billing_message(provider: str, err: Exception | str) -> str:
    return (f"{provider.title()} refused the call: prepaid credits are depleted (HTTP 402). Top up at "
            "https://ai.studio/projects → your project → Billing, then run again. Nothing was spent.")


def set_status(provider: str, ok: bool, message: str = "") -> None:
    """Remember the last provider health (shown in the dashboard sidebar)."""
    cache.put_json("provider_status", provider, {"ok": ok, "message": message, "at": time.time()})


def get_status(provider: str) -> Optional[dict]:
    return cache.get_json("provider_status", provider)


# ---------------------------------------------------------------- prompts

def render_prompt(name: str, **vars: Any) -> str:
    """Load engine/prompts/<name>.md and substitute $vars (JSON-safe: braces untouched)."""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    safe = {k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)) for k, v in vars.items()}
    out = string.Template(text).safe_substitute(safe)
    override = (market().get("override") or "").strip()
    if override:                                     # non-India market: the override has the last word
        out = out.rstrip() + "\n\n" + override
    return out


# ---------------------------------------------------------------- JSON parsing

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Parse JSON from an LLM reply: tolerate code fences, leading prose and trailing commas."""
    if text is None:
        raise ValueError("empty reply")
    t = text.strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON object found in reply")
    start = min(starts)
    closer = "}" if t[start] == "{" else "]"
    end = t.rfind(closer)
    if end <= start:
        raise ValueError("unterminated JSON in reply")
    body = t[start : end + 1]
    body = re.sub(r",\s*([}\]])", r"\1", body)  # trailing commas
    return json.loads(body)


# ---------------------------------------------------------------- providers

def _throttle() -> None:
    global _last_call
    gap = env_float("LLM_MIN_INTERVAL_S", 4.0)
    with _lock:
        wait = _last_call + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _call_gemini(prompt: str, temperature: float) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=env("GEMINI_API_KEY"))
    cost.check_budget()
    resp = client.models.generate_content(
        model=gemini_model(),
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
            http_options=types.HttpOptions(timeout=120_000),
        ),
    )
    _usage.last = cost.usage_cost(resp)
    return resp.text or ""


def gemini_model() -> str:
    """GEMINI_MODEL from .env, or the newest stable Flash on the key when unset/'auto' (models get retired)."""
    m = env("GEMINI_MODEL", "auto")
    if m.lower() in ("", "auto"):
        from engine.research.analyze import pick_model

        return pick_model()
    return m


def _call_groq(prompt: str, temperature: float) -> str:
    from groq import Groq

    client = Groq(api_key=env("GROQ_API_KEY"), timeout=120)
    resp = client.chat.completions.create(
        model=env("GROQ_MODEL", "llama-3.3-70b-versatile"),
        messages=[
            {"role": "system", "content": "You reply with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


def _call_ollama(prompt: str, temperature: float) -> str:
    r = requests.post(
        f"{env('OLLAMA_HOST', 'http://localhost:11434')}/api/generate",
        json={"model": env("OLLAMA_MODEL", "llama3.1"), "prompt": prompt, "format": "json",
              "stream": False, "options": {"temperature": temperature}},
        timeout=300,
    )
    r.raise_for_status()
    return r.json().get("response", "")


PROVIDERS: dict[str, Callable[[str, float], str]] = {
    "gemini": _call_gemini,
    "groq": _call_groq,
    "ollama": _call_ollama,
}


def _available(name: str) -> bool:
    if name == "gemini":
        return bool(env("GEMINI_API_KEY"))
    if name == "groq":
        return bool(env("GROQ_API_KEY"))
    if name == "ollama":
        try:
            return requests.get(f"{env('OLLAMA_HOST', 'http://localhost:11434')}/api/tags", timeout=2).ok
        except requests.RequestException:
            return False
    return name == "mock"


def provider_chain() -> list[str]:
    primary = env("LLM_PROVIDER", "gemini").lower()
    if primary == "mock":
        return ["mock"]
    order = [primary] + [p for p in ("gemini", "groq", "ollama") if p != primary]
    return [p for p in order if _available(p)]


def active_provider() -> str:
    chain = provider_chain()
    return chain[0] if chain else "none"


# ---------------------------------------------------------------- public API

def complete_json(
    task: str,
    prompt: str,
    model: Optional[Type[T]] = None,
    temperature: float = 0.8,
    app_id: Optional[str] = None,
    mock_payload: Optional[Callable[[], Any]] = None,
    max_attempts: int = 4,
) -> Any:
    """Return parsed (and optionally pydantic-validated) JSON for a prompt.

    `task` names the call for logging, caching and the mock provider.
    `mock_payload` builds deterministic offline output when LLM_PROVIDER=mock.
    """
    chain = provider_chain()
    if not chain:
        raise LLMError(
            "No LLM provider available. Set GEMINI_API_KEY (free at aistudio.google.com) or GROQ_API_KEY "
            "in .env, run Ollama locally, or use LLM_PROVIDER=mock for an offline dry run."
        )
    last_err: Exception | None = None
    for provider in chain:
        model_name = gemini_model() if provider == "gemini" else env(f"{provider.upper()}_MODEL")
        k = cache.key(provider, model_name, task, prompt, temperature)
        cached = cache.get_json("llm", k)
        if cached is not None:
            try:
                return _validate(cached, model)
            except (ValidationError, ValueError):
                pass
        started = time.time()
        spent_inr, tokens = 0.0, 0
        for attempt in range(max_attempts):
            try:
                if provider == "mock":
                    if mock_payload is None:
                        raise LLMError(f"mock provider has no payload for task {task!r}")
                    data = mock_payload()
                else:
                    _throttle()
                    _usage.last = (0, 0, 0.0)
                    raw = PROVIDERS[provider](prompt, temperature)
                    spent_inr += getattr(_usage, "last", (0, 0, 0.0))[2]
                    tokens += sum(getattr(_usage, "last", (0, 0, 0.0))[:2])
                    data = extract_json(raw)
                out = _validate(data, model)
                if provider != "mock":
                    set_status(provider, True)
                cache.put_json("llm", k, data)
                cost.log_step(f"llm:{task}", provider, time.time() - started, app_id=app_id, cost_inr=spent_inr,
                              paid_equivalent_inr=cost.PAID_EQUIV["llm_call"], units=max(1, tokens))
                return out
            except cost.BudgetExceeded:
                raise
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                last_err = e
                log.warning(f"[llm] {provider}/{task}: invalid JSON (attempt {attempt + 1}): {str(e)[:200]}")
                prompt = prompt + "\n\nIMPORTANT: your previous reply was not valid for the schema. Reply with ONLY valid JSON matching the schema exactly."
            except Exception as e:  # noqa: BLE001 — network/quota errors from any SDK
                last_err = e
                wait = min(60, 2 ** attempt * 3)
                msg = str(e)
                log.warning(f"[llm] {provider}/{task}: {msg[:200]} — retry in {wait}s")
                if any(s in msg for s in BILLING_MARKERS):
                    set_status(provider, False, billing_message(provider, e))
                    last_err = LLMError(billing_message(provider, e))
                    break  # billing problems won't fix themselves; never retry into paid usage
                if any(s in msg for s in ("API key not valid", "PERMISSION_DENIED", "401", "invalid_api_key")):
                    set_status(provider, False, f"{provider}: API key rejected")
                    break
                time.sleep(wait)
        log.warning(f"[llm] provider {provider} failed for {task}; trying next provider")
    if isinstance(last_err, LLMError):
        raise last_err
    raise LLMError(f"All LLM providers failed for {task}: {last_err}")


def _validate(data: Any, model: Optional[Type[T]]) -> Any:
    if model is None:
        return data
    return model.model_validate(data)


# ---------------------------------------------------------------- multimodal (reviewers)

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".m4a": "audio/mp4", ".mp4": "video/mp4"}


def complete_json_media(task: str, prompt: str, files: list[Any], model: Optional[Type[T]] = None,
                        temperature: float = 0.2, app_id: Optional[str] = None,
                        mock_payload: Optional[Callable[[], Any]] = None, max_attempts: int = 3) -> Any:
    """JSON answer from Gemini about images/audio (frames of a rendered video, its soundtrack).

    Gemini only: other providers can't see or hear. Cached by (prompt, file contents). Cost logged like text calls.
    """
    import hashlib
    from pathlib import Path

    paths = [Path(f) for f in files]
    if env("LLM_PROVIDER", "gemini").lower() == "mock":
        if mock_payload is None:
            raise LLMError(f"mock provider has no payload for task {task!r}")
        return _validate(mock_payload(), model)
    if not env("GEMINI_API_KEY"):
        raise LLMError("Media review needs GEMINI_API_KEY (only Gemini can watch frames / listen to audio).")
    digest = hashlib.sha256()
    for p in paths:
        digest.update(p.read_bytes())
    k = cache.key("gemini-media", gemini_model(), task, prompt, digest.hexdigest(), temperature)
    cached = cache.get_json("llm", k)
    if cached is not None:
        try:
            return _validate(cached, model)
        except (ValidationError, ValueError):
            pass
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=env("GEMINI_API_KEY"))
    parts = []
    for p in paths:
        parts.append(types.Part(text=f"[file: {p.name}]"))
        parts.append(types.Part.from_bytes(data=p.read_bytes(), mime_type=MIME.get(p.suffix.lower(), "image/jpeg")))
    parts.append(types.Part(text=prompt))
    started, spent, tokens, last = time.time(), 0.0, 0, None
    for attempt in range(max_attempts):
        try:
            _throttle()
            cost.check_budget()
            resp = client.models.generate_content(
                model=gemini_model(), contents=types.Content(parts=parts),
                config=types.GenerateContentConfig(temperature=temperature, response_mime_type="application/json",
                                                   http_options=types.HttpOptions(timeout=180_000)))
            pt, ot, inr = cost.usage_cost(resp)
            spent, tokens = spent + inr, tokens + pt + ot
            data = extract_json(resp.text or "")
            out = _validate(data, model)
            cache.put_json("llm", k, data)
            cost.log_step(f"llm:{task}", "gemini", time.time() - started, app_id=app_id, cost_inr=spent,
                          paid_equivalent_inr=cost.PAID_EQUIV["llm_call"], units=max(1, tokens))
            return out
        except cost.BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e)
            if any(s in msg for s in BILLING_MARKERS):
                raise LLMError(billing_message("gemini", e)) from e
            log.warning(f"[llm] gemini-media/{task}: {msg[:200]} (attempt {attempt + 1})")
            time.sleep(min(30, 3 * 2 ** attempt))
    raise LLMError(f"media review failed for {task}: {last}")
