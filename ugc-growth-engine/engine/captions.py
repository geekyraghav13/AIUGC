"""Caption timing for the Remotion composition. Chromium shapes Indic scripts correctly, so fonts are plain CSS."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Optional

from engine.settings import FONTS_DIR, creator_styles, languages, load_yaml


@dataclass
class Word:
    start: float
    end: float
    text: str


def group_lines(words: list[Word], per_line: int) -> list[list[Word]]:
    """Split words into caption lines of `per_line` (2–4) words, breaking on sentence punctuation."""
    per_line = max(2, min(4, per_line))
    lines: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        cur.append(w)
        if len(cur) >= per_line or w.text.rstrip().endswith((".", "?", "!", "।", ",")):
            lines.append(cur)
            cur = []
    if cur:
        if lines and len(cur) == 1 and len(lines[-1]) < 4:
            lines[-1].extend(cur)  # avoid orphan single word
        else:
            lines.append(cur)
    return lines


def caption_lines(words: list[Word], per_line: int,
                  scenes: list[tuple[float, float, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Timed caption lines. Each line stays up until the next starts and carries the text look (position and
    colours) of the scene it begins in. `scenes` = [(start_s, end_s, look_dict)]."""
    lines = group_lines(words, per_line)
    out = []
    for i, line in enumerate(lines):
        end = lines[i + 1][0].start if i + 1 < len(lines) else line[-1].end + 0.3
        end = max(end, line[-1].end)
        look = next((lk for a, b, lk in scenes if a <= line[0].start < b), scenes[-1][2])
        out.append({"start": line[0].start, "end": end, "look": look,
                    "words": [{"text": w.text, "start": w.start, "end": w.end} for w in line]})
    return out


TRANSITIONS = ("punch", "fade")


def _available(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in pool if (FONTS_DIR / f["file"]).exists()]


def design(lang: str, style: str, seed: int = 0, mode: str = "") -> dict[str, Any]:
    """The whole text look of ONE video: a Latin font + a script font (Hindi/Tamil/...), size, outline, animation,
    transition, case, caption layout, headline style and hook style. Drawn from config/typography.yaml with a
    per-video seed, so every video looks different from the others but a re-render of the same video looks the same.
    `mode` (visual mode) filters layouts to what suits it (e.g. talking videos keep captions low, off the face)."""
    rng = random.Random(f"typography:{seed}")
    cs = creator_styles()[style]
    cap = cs["caption"]
    pools = load_yaml("typography")
    script = languages()[lang].get("script", "latin")
    latin = rng.choice(_available(pools["latin"]) or [{"family": "Noto Sans", "file": "NotoSans-Bold.ttf", "weight": 700,
                                                       "scale": 1.0}])
    script_font = None
    if script != "latin":
        script_font = rng.choice(_available(pools.get(script, [])) or [None])
    caps = bool(latin.get("caps")) or (rng.random() < 0.2 and script == "latin")
    scale = float(latin.get("scale", 1.0)) if script == "latin" else float(script_font["scale"] if script_font else 1.0)
    size = int(round(int(cap["size"]) * scale * rng.uniform(0.9, 1.15)))
    per_line = rng.choice([2, 2, 3, 3, 4]) if script == "latin" else rng.choice([2, 3, 3])
    layouts = ((pools.get("mode_layouts") or {}).get(mode) or {}).get("caption") or list(pools.get("caption_layouts") or ["karaoke"])
    return {"family": latin["family"], "scriptFamily": script_font["family"] if script_font else latin["family"],
            "langFont": languages()[lang]["font"], "weight": int(latin["weight"]),
            "anim": rng.choice(cs.get("anims") or ["pop", "slide", "fade"]), "transition": rng.choice(TRANSITIONS),
            "caps": caps, "size": size, "outlinePx": rng.choice([4, 5, 6, 7, 8]), "wordsPerLine": per_line,
            "captionLayout": rng.choice(layouts),
            "headlineStyle": rng.choice(pools.get("headline_styles") or ["sticker"]),
            "hookStyle": rng.choice(pools.get("hook_styles") or ["card"]),
            "_fonts": [f for f in (latin, script_font) if f]}


def signature(d: dict[str, Any]) -> str:
    """What makes two videos look alike: fonts, caption layout, headline + hook style, size bucket, animation."""
    return "·".join([d["family"], d["scriptFamily"], d["captionLayout"], d["headlineStyle"], d["hookStyle"],
                     str(d["size"] // 12), d["anim"]])


def style_seed(c: Any) -> int:
    """The seed a creative's look is drawn from (assigned by assign_styles; defaults to its id)."""
    sig = getattr(c, "style_signature", "") or ""
    head = sig.split("|", 1)[0]
    return int(head) if head.isdigit() else int(c.id or 0)


def assign_styles(creatives: list[Any], taken: Optional[set[str]] = None, tries: int = 60) -> dict[int, str]:
    """Give every creative a text look no other video in the same set already has (changes `style_signature` in place).

    Keeps an existing look when it is still unique, so re-renders don't change a video's style."""
    used = set(taken or set())
    out: dict[int, str] = {}
    for c in sorted(creatives, key=lambda c: c.id or 0):
        seed = style_seed(c)
        for k in range(tries):
            s = seed + k * 7919 if k else seed
            sig = signature(design(c.language, c.creator_style, s, getattr(c, "visual_mode", "")))
            if sig not in used:
                break
        used.add(sig)
        c.style_signature = f"{s}|{sig}"
        out[c.id] = c.style_signature
    return out
