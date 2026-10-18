"""D8 Market research: what is working right now on YouTube Shorts in India."""
from __future__ import annotations

from typing import Any

from engine.settings import env, env_float, load_yaml


def cfg() -> dict[str, Any]:
    """config/research.yaml with RESEARCH_<KEY> env overrides."""
    out = dict(load_yaml("research"))
    for k, v in out.items():
        raw = env(f"RESEARCH_{k.upper()}")
        if raw:
            out[k] = type(v)(env_float(f"RESEARCH_{k.upper()}", float(v))) if isinstance(v, (int, float)) else raw
    return out


def youtube_mode() -> str:
    """'youtube' with a key, 'mock' for offline dry runs (LLM_PROVIDER=mock), else 'none'."""
    if env("YOUTUBE_API_KEY"):
        return "youtube"
    if env("LLM_PROVIDER").lower() == "mock" or env("RESEARCH_MOCK") == "1":
        return "mock"
    return "none"
