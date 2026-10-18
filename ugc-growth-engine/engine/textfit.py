"""Adaptive text look: where overlay text goes, and the colours/placeholder it uses.

Position: for every scene we sample frames, score horizontal bands by how busy they are and pick the calmest allowed
band, keeping the previous scene's band unless another is clearly calmer.

Colours: one palette per VIDEO (so colours never flip between scenes) that is DIFFERENT for every video: the accent
comes from that video's own footage (complement of its dominant hue, nudged per creative), and the text placeholder
style (glass, tinted, light card, or outline-only) rotates per creative.
"""
from __future__ import annotations

import colorsys
import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

from engine.settings import ffmpeg_bin, log

W, H = 1080, 1920
# Candidate text-block centres (px). Kept inside platform UI safe zones (top ~200px, bottom ~380px).
BANDS = {"top": 330, "upper": 640, "middle": 960, "lower": 1290}
BAND_H = 300
SAMPLE = (108, 192)  # analysis resolution (1/10 of the frame)
SWITCH_GAIN = 0.12   # a new band must be this much calmer (0–1 scale) than the previous one to move the text
PLATE_BUSY = 0.18    # band busier than this gets a translucent plate behind the text
SHOT_Y = 1700        # text centre under the phone in app-screenshot scenes
DARK, LIGHT = "111111", "FFFFFF"


@dataclass
class TextLook:
    y: int                       # centre of the text block, px
    band: str
    primary: str                 # hex RRGGBB
    outline: str
    highlight: str
    plate: Optional[str]         # rgba() css string or None
    busy: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"y": self.y, "band": self.band, "primary": self.primary, "outline": self.outline,
                "highlight": self.highlight, "plate": self.plate, "busy": round(self.busy, 3)}


# ---------------------------------------------------------------- frames

def _cover(img: Image.Image) -> Image.Image:
    """Crop like CSS object-fit: cover into the analysis size."""
    w, h = img.size
    tw, th = SAMPLE
    scale = max(tw / w, th / h)
    img = img.resize((max(tw, round(w * scale)), max(th, round(h * scale))), Image.BILINEAR)
    l, t = (img.width - tw) // 2, (img.height - th) // 2
    return img.crop((l, t, l + tw, t + th))


def load_image(path: str | Path) -> Optional[Image.Image]:
    try:
        return _cover(Image.open(path).convert("RGB"))
    except Exception as e:  # noqa: BLE001 — analysis is best effort
        log.debug(f"textfit: cannot read {path}: {e}")
        return None


def video_frame(path: str | Path, at: float) -> Optional[Image.Image]:
    """One frame of a clip via ffmpeg (scaled small)."""
    try:
        p = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{max(0.0, at):.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=-2:384", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True, timeout=30)
        if p.returncode != 0 or not p.stdout:
            return None
        import io

        return _cover(Image.open(io.BytesIO(p.stdout)).convert("RGB"))
    except Exception as e:  # noqa: BLE001
        log.debug(f"textfit: cannot grab frame from {path}: {e}")
        return None


# ---------------------------------------------------------------- scoring

def _lum(rgb: np.ndarray) -> np.ndarray:
    lin = np.where(rgb <= 0.03928, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def contrast(l1: float, l2: float) -> float:
    a, b = max(l1, l2), min(l1, l2)
    return (a + 0.05) / (b + 0.05)


def _band_slice(name: str) -> slice:
    sy = SAMPLE[1] / H
    c = BANDS[name]
    return slice(max(0, int((c - BAND_H / 2) * sy)), min(SAMPLE[1], int((c + BAND_H / 2) * sy)))


def band_stats(img: Image.Image) -> dict[str, dict[str, float]]:
    rgb = np.asarray(img, dtype=np.float32) / 255.0
    lum = _lum(rgb)
    gy, gx = np.gradient(lum)
    grad = np.hypot(gx, gy)
    out = {}
    for name in BANDS:
        s = _band_slice(name)
        g, l = grad[s], lum[s]
        # busyness 0–1: edge density plus brightness spread (a calm sky or wall scores low)
        busy = float(min(1.0, 4.0 * g.mean() + 1.2 * l.std()))
        out[name] = {"busy": busy, "lum": float(l.mean())}
    return out


def dominant_hue(img: Image.Image) -> tuple[float, float]:
    """(hue 0–1, saturation strength 0–1) of the frame, weighting vivid pixels."""
    hsv = np.asarray(img.convert("HSV"), dtype=np.float32) / 255.0
    w = hsv[..., 1] * hsv[..., 2]
    if w.sum() < 1e-6:
        return 0.0, 0.0
    ang = hsv[..., 0] * 2 * np.pi
    x, y = (w * np.cos(ang)).sum(), (w * np.sin(ang)).sum()
    hue = (np.arctan2(y, x) / (2 * np.pi)) % 1.0
    return float(hue), float(min(1.0, np.hypot(x, y) / w.sum()) * min(1.0, w.mean() * 4))


def _hex(r: float, g: float, b: float) -> str:
    return "".join(f"{int(round(max(0, min(1, v)) * 255)):02X}" for v in (r, g, b))


def _rel_lum_hex(h: str) -> float:
    v = np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)
    return float(_lum(v))


@dataclass
class Palette:
    style: str              # glass | tint | light | outline
    accent: str             # spoken-word / hook colour (hex, no #)
    primary: str            # caption text colour
    outline: str
    plate: Optional[str]    # css background of the text placeholder (None = outline-only)
    hue: float

    def as_dict(self) -> dict[str, Any]:
        return {"style": self.style, "accent": self.accent, "primary": self.primary, "outline": self.outline,
                "plate": self.plate, "hue": round(self.hue, 3)}


PLATE_STYLES = ("glass", "tint", "light", "outline")


def _rgba(rgb: tuple[float, float, float], a: float) -> str:
    r, g, b = (int(round(max(0, min(1, v)) * 255)) for v in rgb)
    return f"rgba({r},{g},{b},{a})"


def video_palette(all_frames: list[Image.Image], fallback_hex: str, seed: int) -> Palette:
    """One palette for a whole video, derived from its footage and varied by `seed` (the creative id)."""
    hues = [dominant_hue(f) for f in all_frames] or [(0.0, 0.0)]
    hue, strength = max(hues, key=lambda t: t[1])
    if strength < 0.2:  # grey / muted footage: take the hue from the seed so videos still differ
        hue = ((seed * 0.6180339887) % 1.0)
    h = int(hashlib.sha256(f"palette:{seed}".encode()).hexdigest()[:8], 16)
    style = PLATE_STYLES[h % len(PLATE_STYLES)]
    offset = (0.5, 0.42, 0.58)[(h // 4) % 3] + ((h // 12) % 9 - 4) * 0.01   # complement-ish, jittered per video
    comp = (hue + offset) % 1.0
    bright = colorsys.hsv_to_rgb(comp, 0.85, 1.0)
    if style == "light":                            # deep accent that reads on a white card
        v = 0.7
        while v > 0.3 and contrast(float(_lum(np.array(colorsys.hsv_to_rgb(comp, 0.9, v), dtype=np.float32))), 1.0) < 4.0:
            v -= 0.05
        accent = _hex(*colorsys.hsv_to_rgb(comp, 0.9, v))
        return Palette(style, accent, DARK, LIGHT, "rgba(255,255,255,0.92)", hue)
    # Blues/violets are dim even at full brightness: lighten (desaturate) until the accent pops on dark surfaces.
    sat = 0.85
    while sat > 0.15 and contrast(float(_lum(np.array(colorsys.hsv_to_rgb(comp, sat, 1.0), dtype=np.float32))), 0.03) < 7.0:
        sat -= 0.05
    bright = colorsys.hsv_to_rgb(comp, sat, 1.0)
    accent = _hex(*bright)
    if style == "tint":                             # deep card tinted with the footage's hue
        return Palette(style, accent, LIGHT, DARK, _rgba(colorsys.hsv_to_rgb(hue, 0.7, 0.16), 0.84), hue)
    if style == "glass":
        return Palette(style, accent, LIGHT, DARK, "rgba(0,0,0,0.5)", hue)
    return Palette(style, accent, LIGHT, DARK, None, hue)   # outline-only; a plate is added where footage is busy


def choose(frames_per_scene: list[list[Image.Image]], palette: Palette, prefer: str,
           forced: Optional[list[Optional[str]]] = None, allowed: Optional[tuple[str, ...]] = None,
           avoid: Optional[dict[int, str]] = None) -> list[TextLook]:
    """One TextLook per scene.

    `palette` the video's colours and placeholder style (same for every scene).
    `prefer`  the creator style's default band (a small bias, not a rule).
    `forced`  per-scene band override; "shot" = below the phone in an app-screenshot scene.
    `allowed` restrict candidate bands (e.g. the hook only uses the top two).
    `avoid`   {scene index: band} another overlay already occupies.
    """
    looks: list[TextLook] = []
    prev: Optional[str] = None
    names = [n for n in BANDS if not allowed or n in allowed]
    for i, frames in enumerate(frames_per_scene):
        force = forced[i] if forced else None
        if force == "shot":
            looks.append(TextLook(y=SHOT_Y, band="shot", primary=palette.primary, outline=palette.outline,
                                  highlight=palette.accent, plate=palette.plate or "rgba(0,0,0,0.5)"))
            prev = None
            continue
        cands = [n for n in names if n != (avoid or {}).get(i)] or names
        if not frames:
            name = force or (prev if prev in cands else None) or (prefer if prefer in cands else cands[0])
            stats = {name: {"busy": 0.3, "lum": 0.2}}
            imgs: list[Image.Image] = []
        else:
            per = [band_stats(f) for f in frames]
            stats = {n: {"busy": max(p[n]["busy"] for p in per), "lum": float(np.mean([p[n]["lum"] for p in per]))}
                     for n in BANDS}
            imgs = frames
            if force:
                name = force
            else:
                score = {n: stats[n]["busy"] + (0.0 if n == prefer else 0.08) for n in cands}
                best = min(score, key=score.get)
                name = prev if prev in score and score[prev] - score[best] < SWITCH_GAIN else best
        st = stats[name]
        lum, busy = st["lum"], st["busy"]
        # Colours and placeholder come from the video's palette; outline-only videos still get a dark plate
        # where the footage would hurt legibility (busy, bright or mid-tone).
        plate = palette.plate
        if plate is None and (busy > PLATE_BUSY or lum > 0.4 or (0.18 < lum <= 0.4 and busy > 0.1)):
            plate = "rgba(0,0,0,0.5)"
        primary, outline, hl = palette.primary, palette.outline, palette.accent
        looks.append(TextLook(y=BANDS[name], band=name, primary=primary, outline=outline, highlight=hl, plate=plate,
                              busy=busy))
        prev = name
    return looks
