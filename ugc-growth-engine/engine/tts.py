"""Voice: edge-tts (free Microsoft neural Indian voices) with word timings; gTTS fallback."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from engine import cache
from engine.settings import creator_styles, ffprobe_bin, languages, log

EDGE_TIMEOUT_S = 45

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️←-⇿⬀-⯿]+"
)


def clean_text(text: str) -> str:
    """Remove emoji/symbols the fonts and TTS can't handle; collapse whitespace."""
    t = EMOJI.sub("", text or "")
    t = t.replace("…", "...").replace("—", ", ")
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=1)
def available_voices() -> set[str]:
    """Voice names from edge-tts (same list as `edge-tts --list-voices`)."""
    try:
        import edge_tts

        return {v["ShortName"] for v in asyncio.run(edge_tts.list_voices())}
    except Exception as e:  # noqa: BLE001
        log.warning(f"could not list edge-tts voices: {e}")
        return set()


def verify_voices() -> list[str]:
    """Return configured voices missing from edge-tts (empty list = all good)."""
    have = available_voices()
    if not have:
        return ["(could not reach edge-tts voice list — offline?)"]
    missing = []
    for code, cfg in languages().items():
        for v in cfg["voices"].values():
            if v not in have:
                missing.append(f"{code}: {v}")
    return missing


def voice_for(lang: str, style: str) -> str:
    gender = creator_styles()[style].get("voice_gender", "female")
    voices = languages()[lang]["voices"]
    return voices.get(gender) or next(iter(voices.values()))


def duration(path: str | Path) -> float:
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=30).stdout
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0.0


async def _edge(text: str, voice: str, rate: str, dest: Path) -> list[dict[str, Any]]:
    import edge_tts

    words: list[dict[str, Any]] = []
    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary", receive_timeout=60)
    with open(dest, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                s = chunk["offset"] / 1e7
                words.append({"start": round(s, 3), "end": round(s + chunk["duration"] / 1e7, 3), "word": chunk["text"]})
    return words


def _gtts(text: str, lang: str, dest: Path) -> None:
    from gtts import gTTS

    gTTS(text=text, lang=languages()[lang].get("gtts_lang", "hi"), timeout=30).save(str(dest))


def estimate_words(text: str, total: float) -> list[dict[str, Any]]:
    """Approximate word timings proportional to word length (used when TTS gives none)."""
    words = text.split()
    if not words or total <= 0:
        return []
    weights = [max(1, len(w)) for w in words]
    scale = (total * 0.95) / sum(weights)
    t, out = 0.05, []
    for w, wt in zip(words, weights):
        d = wt * scale
        out.append({"start": round(t, 3), "end": round(t + d, 3), "word": w})
        t += d
    return out


@lru_cache(maxsize=1)
def _whisper_model():  # noqa: ANN202
    import os

    # The Hugging Face "xet" transfer can stall forever on first download; plain HTTP with a timeout is reliable.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    from faster_whisper import WhisperModel

    return WhisperModel("base", device="cpu", compute_type="int8")


def _whisper_words(path: Path, lang: str) -> Optional[list[dict[str, Any]]]:
    try:
        model = _whisper_model()
    except ImportError:
        return None
    code = {"hinglish": "hi"}.get(lang, lang)
    segs, _ = model.transcribe(str(path), language=code, word_timestamps=True)
    return [{"start": w.start, "end": w.end, "word": w.word.strip()} for s in segs for w in (s.words or [])]


def rate_plus(rate: str, extra_pct: int) -> str:
    """'+12%' + 10 → '+22%' (edge-tts rate strings)."""
    m = re.fullmatch(r"([+-]?\d+)%", (rate or "+0%").strip())
    v = (int(m.group(1)) if m else 0) + int(extra_pct)
    return f"{v:+d}%"


def synth(text: str, lang: str, style: str, extra_rate_pct: int = 0) -> dict[str, Any]:
    """TTS one scene → {'path', 'duration', 'words', 'voice', 'provider'}. Cached by (text, voice, rate)."""
    text = clean_text(text)
    voice = voice_for(lang, style)
    rate = rate_plus(creator_styles()[style].get("tts_rate", "+0%"), extra_rate_pct)
    k = cache.key("tts", text, voice, rate)
    meta = cache.get_json("tts", k)
    if meta and Path(meta["path"]).exists():
        return meta
    dest = cache.path("tts", k, ".mp3")
    provider, words = "edge-tts", []
    for attempt in range(3):
        try:
            # hard cap: edge-tts streams can occasionally stall without tripping their own timeouts
            words = asyncio.run(asyncio.wait_for(_edge(text, voice, rate, dest), timeout=EDGE_TIMEOUT_S))
            if dest.stat().st_size > 1000:
                break
        except Exception as e:  # noqa: BLE001 — includes asyncio.TimeoutError
            log.warning(f"edge-tts failed ({attempt + 1}/3): {type(e).__name__} {e}")
    else:
        provider = "gtts"
        _gtts(text, lang, dest)
        words = _whisper_words(dest, lang) or []
    dur = duration(dest)
    if not words:
        words = estimate_words(text, dur)
    meta = {"path": str(dest), "duration": dur, "words": words, "voice": voice if provider == "edge-tts" else "gtts",
            "provider": provider, "text": text}
    cache.put_json("tts", k, meta)
    return meta


def align_script_words(text: str, heard: list[dict[str, Any]], total: float) -> list[dict[str, Any]]:
    """Caption words = the script's own words (never a transcription typo), timed from what Whisper heard.

    Same word count → take Whisper's timings one-to-one. Otherwise spread the script words over the span Whisper
    heard speech in, weighted by word length.
    """
    words = text.split()
    if not words:
        return []
    if heard and len(heard) == len(words):
        return [{"start": h["start"], "end": h["end"], "word": w} for w, h in zip(words, heard)]
    if not heard:
        return estimate_words(text, total)
    a, b = float(heard[0]["start"]), float(heard[-1]["end"])
    weights = [max(1, len(w)) for w in words]
    scale = max(0.1, b - a) / sum(weights)
    t, out = a, []
    for w, wt in zip(words, weights):
        out.append({"start": round(t, 3), "end": round(t + wt * scale, 3), "word": w})
        t += wt * scale
    return out


def from_file(path: str | Path, text: str, lang: str, provider: str = "magnific") -> dict[str, Any]:
    """Use an externally produced voice line (e.g. Magnific / ElevenLabs) for one scene. Cached by file contents."""
    import hashlib

    path = Path(path)
    text = clean_text(text)
    k = cache.key("tts-ext", hashlib.sha256(path.read_bytes()).hexdigest(), text, lang)
    meta = cache.get_json("tts", k)
    if meta and Path(meta["path"]).exists():
        return meta
    dur = duration(path)
    heard = _whisper_words(path, lang) or []
    meta = {"path": str(path), "duration": dur, "words": align_script_words(text, heard, dur),
            "voice": path.stem, "provider": provider, "text": text}
    cache.put_json("tts", k, meta)
    return meta
