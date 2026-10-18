"""Paths, environment settings and YAML config loading."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from rich.logging import RichHandler

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
ASSETS_DIR = ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
MUSIC_DIR = ASSETS_DIR / "music"
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
OUTPUTS_DIR = ROOT / "outputs"
PROMPTS_DIR = ROOT / "engine" / "prompts"

load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
)
log = logging.getLogger("ugc")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default) or default


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(float(env(name, str(default))))
    except ValueError:
        return default


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def languages() -> dict[str, Any]:
    return load_yaml("languages")


def formats() -> dict[str, Any]:
    return load_yaml("formats")


def creator_styles() -> dict[str, Any]:
    return load_yaml("creator_styles")


def taxonomy() -> dict[str, Any]:
    return load_yaml("taxonomy")


def market() -> dict[str, Any]:
    """Active market profile (config/markets.yaml). Default TIER1 (US/UK/CA/AU English); ENGINE_MARKET=IN switches to India."""
    markets = load_yaml("markets")
    name = env("ENGINE_MARKET", str(markets.get("default", "TIER1"))).upper()
    profile = markets.get(name) if isinstance(markets.get(name), dict) else markets["TIER1"]
    return {"id": name, **profile}


def india_context() -> dict[str, Any]:
    """Seasonal moments + everyday scenarios for the active market (India unless ENGINE_MARKET says otherwise)."""
    return load_yaml(market().get("context_file", "india_context"))


def _has_filter(binary: str, name: str) -> bool:
    try:
        out = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return f" {name} " in out


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    """ffmpeg for small utilities: audio duration probe, gradient/placeholder images, music synthesis.

    Video composition is done by Remotion (see engine/render.py). Placeholder images use the `drawtext` filter,
    so prefer a build that has it (macOS: `brew install ffmpeg-full`); any ffmpeg works for the rest.
    """
    found = [c for c in (env("FFMPEG_BIN"), "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
                         "/usr/local/opt/ffmpeg-full/bin/ffmpeg", shutil.which("ffmpeg") or "",
                         "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg") if c and Path(c).exists()]
    for c in found:
        if _has_filter(c, "drawtext"):
            return c
    if found:
        return found[0]
    raise RuntimeError("ffmpeg not found (used for audio probing and placeholder images). "
                       "Install it (macOS: `brew install ffmpeg-full`) or set FFMPEG_BIN in .env.")


@lru_cache(maxsize=1)
def ffprobe_bin() -> str:
    probe = Path(ffmpeg_bin()).with_name("ffprobe")
    return str(probe) if probe.exists() else (shutil.which("ffprobe") or "ffprobe")
