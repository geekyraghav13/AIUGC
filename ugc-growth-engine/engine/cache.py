"""Tiny on-disk cache for every external call (LLM, TTS, stock search)."""
from __future__ import annotations

import hashlib
import os
import threading
import json
from pathlib import Path
from typing import Any

from engine.settings import CACHE_DIR


def key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def path(namespace: str, k: str, suffix: str = ".json") -> Path:
    d = CACHE_DIR / namespace / k[:2]
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{k}{suffix}"


def get_json(namespace: str, k: str) -> Any | None:
    p = path(namespace, k)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def put_json(namespace: str, k: str, value: Any) -> None:
    p = path(namespace, k)
    tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")  # unique: concurrent writers can't collide
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
