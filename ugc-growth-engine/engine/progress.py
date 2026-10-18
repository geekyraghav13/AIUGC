"""Live, cross-process run progress (one JSON file per app) for the dashboard's step-by-step loader.

A run declares its steps up front; pipeline code marks them running/done/failed with a short detail. Anything not
inside a tracked run is a no-op, so modules can report progress unconditionally.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from engine.settings import CACHE_DIR

# Canonical steps, in order. A run uses a subset.
LABELS: dict[str, str] = {
    "research": "Market research (search demand → top Shorts/Reels → AI analysis → playbook)",
    "understand": "Understand the app (personas, pains, messages)",
    "concepts": "Video ideas (concepts)",
    "hooks": "Hooks in every language",
    "scripts": "Full scripts",
    "safety": "Safety & compliance check",
    "pick": "Pick the best ideas to film",
    "render": "Render videos",
    "test": "Test (simulated market)",
    "insights": "Insights — what won",
    "next": "Next batch (70% exploit / 30% explore)",
    # production crew (one creative)
    "director": "Director — scene-by-scene plan",
    "assets": "Magnific assets (voice, hook clip)",
    "edit": "Editor — cut",
    "review": "Reviewers — text, visual, audio, brand",
    "qc": "QC — verdict + revision tasks",
    "owner": "Owner — approve or send back",
}
IDEA_STEPS = ["research", "understand", "concepts", "hooks", "scripts", "safety", "pick"]


def full_loop_steps(generations: int = 2) -> list[str]:
    steps = IDEA_STEPS + ["render:1", "test:1", "insights:1"]
    for g in range(2, generations + 1):
        steps += [f"next:{g}", f"render:{g}", f"test:{g}", f"insights:{g}"]
    return steps


def label(key: str) -> str:
    base, _, gen = key.partition(":")
    text = LABELS.get(base, base)
    if gen.startswith("v"):
        return f"{text} {gen}"
    return f"{text} — Gen {gen}" if gen else text


def _path(app_id: str):  # noqa: ANN202
    d = CACHE_DIR / "progress"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{app_id}.json"


def read(app_id: str) -> Optional[dict[str, Any]]:
    p = _path(app_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write(app_id: str, state: dict[str, Any]) -> None:
    p = _path(app_id)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


_active: dict[str, bool] = {}


def start(app_id: str, title: str, steps: list[str]) -> None:
    now = time.time()
    _write(app_id, {"title": title, "pid": os.getpid(), "started": now, "updated": now, "finished": None,
                    "ok": None, "message": "", "log": [],
                    "steps": [{"key": k, "label": label(k), "status": "pending", "detail": "", "started": None,
                               "ended": None} for k in steps]})
    _active[app_id] = True


def _update(app_id: str, fn) -> None:  # noqa: ANN001
    if not _active.get(app_id):
        return
    st = read(app_id)
    if not st or st.get("pid") != os.getpid():
        return
    fn(st)
    st["updated"] = time.time()
    _write(app_id, st)


def _find(st: dict[str, Any], key: str) -> Optional[dict[str, Any]]:
    """Exact key, else the first not-finished step with that base (e.g. 'render' → 'render:2')."""
    for s in st["steps"]:
        if s["key"] == key:
            return s
    base = key.partition(":")[0]
    for s in st["steps"]:
        if s["key"].partition(":")[0] == base and s["status"] in ("pending", "running"):
            return s
    return None


def step(app_id: str, key: str, status: str = "running", detail: str = "") -> None:
    """Mark a step running/done/failed/skipped (unknown keys are ignored)."""
    def fn(st: dict[str, Any]) -> None:
        s = _find(st, key)
        if s is None:
            return
        now = time.time()
        if status == "running":
            # anything still running before this step is implicitly done
            for other in st["steps"]:
                if other is s:
                    break
                if other["status"] == "running":
                    other["status"], other["ended"] = "done", now
            s["started"] = s["started"] or now
        else:
            s["started"] = s["started"] or now
            s["ended"] = now
        s["status"] = status
        if detail:
            s["detail"] = detail
            st["log"] = (st.get("log") or [])[-40:] + [f"{time.strftime('%H:%M:%S')}  {detail}"]
    _update(app_id, fn)


def detail(app_id: str, text: str) -> None:
    """Update the detail line of the currently running step (and the activity log)."""
    def fn(st: dict[str, Any]) -> None:
        for s in st["steps"]:
            if s["status"] == "running":
                s["detail"] = text
        st["log"] = (st.get("log") or [])[-40:] + [f"{time.strftime('%H:%M:%S')}  {text}"]
    _update(app_id, fn)


def finish(app_id: str, ok: bool, message: str = "") -> None:
    def fn(st: dict[str, Any]) -> None:
        now = time.time()
        for s in st["steps"]:
            if s["status"] == "running":
                s["status"], s["ended"] = ("done" if ok else "failed"), now
                if not ok and message:
                    s["detail"] = message
        st["finished"], st["ok"], st["message"] = now, ok, message
    _update(app_id, fn)
    _active.pop(app_id, None)


@contextmanager
def tracked(app_id: str, title: str, steps: list[str]) -> Iterator[None]:
    start(app_id, title, steps)
    try:
        yield
    except BaseException as e:  # noqa: BLE001 — record, then re-raise
        finish(app_id, False, str(e)[:300])
        raise
    finish(app_id, True, "Done")


def is_running(app_id: str) -> bool:
    """A run is live if it hasn't finished and its process still exists."""
    st = read(app_id)
    if not st or st.get("finished"):
        return False
    try:
        os.kill(int(st["pid"]), 0)
        return True
    except (OSError, ValueError, KeyError):
        return False
