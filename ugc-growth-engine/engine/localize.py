"""India-native localization rules per language (localize, don't translate) + script validation."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from engine.settings import languages

# Unicode ranges for Indic scripts we support.
SCRIPT_RANGES: dict[str, tuple[int, int]] = {
    "devanagari": (0x0900, 0x097F),
    "bengali": (0x0980, 0x09FF),
    "tamil": (0x0B80, 0x0BFF),
    "telugu": (0x0C00, 0x0C7F),
}
_LETTER = re.compile(r"\w", re.U)


def script_share(text: str, script: str) -> float:
    """Share of letters in `text` that belong to `script` ('latin' = ASCII/Latin letters)."""
    letters = [ch for ch in text if _LETTER.match(ch) and not ch.isdigit() and ch != "_"]
    if not letters:
        return 1.0
    if script == "latin":
        hits = sum(1 for ch in letters if "LATIN" in unicodedata.name(ch, ""))
    else:
        lo, hi = SCRIPT_RANGES[script]
        hits = sum(1 for ch in letters if lo <= ord(ch) <= hi)
    return hits / len(letters)


def check_language(text: str, lang: str) -> tuple[bool, str]:
    """Is `text` written in the right script for `lang`? English tech/brand words are allowed."""
    cfg = languages().get(lang)
    if cfg is None:
        return False, f"unknown language {lang}"
    script = cfg["script"]
    share = script_share(text, script)
    if script == "latin":
        ok = share >= 0.95
        return ok, "" if ok else f"Hinglish must be Roman script only ({share:.0%} Latin letters)"
    ok = share >= 0.55  # English tech/brand words are allowed to stay English
    return ok, "" if ok else f"{cfg['name']} must be mostly {script} script ({share:.0%})"


def language_block(lang: str) -> str:
    """Prompt guidance for writing natively in one language."""
    cfg = languages()[lang]
    return (
        f"LANGUAGE: {cfg['name']} (code `{lang}`), script: {cfg['script']}.\n"
        f"Style: {cfg['style_notes'].strip()}\n"
        f"Set scenes and references in: {', '.join(cfg.get('regions', []))}.\n"
        "Write it natively as a creator from that region would speak — do NOT translate from English. "
        "Use local food, places, festivals, slang and everyday situations of that audience."
    )


def languages_block(langs: list[str]) -> str:
    return "\n\n".join(language_block(l) for l in langs)


def words(text: str) -> list[str]:
    """Lower-cased words, safe for Indic scripts: split on whitespace, strip punctuation/symbols/emoji at the
    edges. (Regex \\w splits Tamil/Devanagari words at vowel signs, which are combining marks, not \\w.)"""
    out = []
    for w in (text or "").lower().split():
        i, j = 0, len(w)
        while i < j and unicodedata.category(w[i])[0] in "PSZ":
            i += 1
        while j > i and unicodedata.category(w[j - 1])[0] in "PSZ":
            j -= 1
        if i < j:
            out.append(w[i:j])
    return out


def script_texts(script: dict[str, Any]) -> str:
    """All viewer-facing text of a script JSON (for language + compliance checks)."""
    parts = [script.get("hook", ""), script.get("cta", "")]
    for sc in script.get("scenes", []):
        parts += [sc.get("voiceover", ""), sc.get("on_screen_text", "")]
    return " ".join(p for p in parts if p)
