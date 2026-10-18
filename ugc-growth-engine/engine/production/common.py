"""Shared pieces of the production crew: the issue format every reviewer uses, run bookkeeping, media helpers."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from engine.db import Creative, ProductionRun, ReviewTask, save, session
from engine.settings import OUTPUTS_DIR, ffmpeg_bin, load_yaml, log

ROLES = ("owner", "director", "assets", "editor", "text", "visual", "audio", "brand", "qc")
SEVERITY_WEIGHT = {"blocker": 25, "major": 10, "minor": 3}

# Which role defines the fix for each machine action (the editor then applies it).
FIX_OWNER = {"set_text": "text", "set_visual": "director", "move_text": "editor", "set_pace": "director",
             "set_mix": "audio", "set_design": "editor", "request_asset": "assets", "replan": "director"}


def cfg() -> dict[str, Any]:
    return load_yaml("production")


class Issue(BaseModel):
    """One concrete, timestamped problem and the change that fixes it."""
    category: str = ""
    severity: str = "major"
    timecode: Optional[float] = None
    scene: Optional[int] = None
    issue: str
    fix: dict[str, Any] = Field(default_factory=dict)   # {"type": set_text|set_visual|..., ...params}

    @model_validator(mode="after")
    def _scene_from_fix(self) -> "Issue":
        """Models often put the scene only inside `fix`; the task (and QC's dedupe) needs it on the issue."""
        if self.scene is None and isinstance((self.fix or {}).get("scene"), int) and self.fix["scene"] >= 0:
            self.scene = int(self.fix["scene"])
        return self

    @field_validator("severity")
    @classmethod
    def _sev(cls, v: str) -> str:
        v = (v or "").lower()
        return v if v in SEVERITY_WEIGHT else "major"

    @field_validator("timecode", mode="before")
    @classmethod
    def _tc(cls, v: Any) -> Optional[float]:
        if v in (None, ""):
            return None
        if isinstance(v, str) and ":" in v:
            m, _, s = v.partition(":")
            try:
                return float(m) * 60 + float(s)
            except ValueError:
                return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


class Issues(BaseModel):
    issues: list[Issue] = Field(default_factory=list)
    summary: str = ""


def fmt_tc(t: Optional[float]) -> str:
    if t is None:
        return "—"
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


# ---------------------------------------------------------------- run bookkeeping

RUN_JSON = ("plan", "asset_requests", "versions", "log")


def persist(run: ProductionRun) -> None:
    """Save a run. Its JSON fields are edited in place, which SQLAlchemy can't see (an in-place-changed dict
    compares equal to itself), so flag them as modified explicitly."""
    from sqlalchemy.orm.attributes import flag_modified

    with session() as s:
        r = s.merge(run)
        for f in RUN_JSON:
            flag_modified(r, f)
        s.commit()
        s.refresh(r)
        for f in ("id", "version", "stage", "status", "qc_score", "updated_at", *RUN_JSON):
            setattr(run, f, getattr(r, f))

def get_run(run_id: int) -> ProductionRun:
    with session() as s:
        r = s.get(ProductionRun, run_id)
    if r is None:
        raise ValueError(f"no production run {run_id}")
    return r


def latest_run(creative_id: int) -> Optional[ProductionRun]:
    from sqlmodel import select

    with session() as s:
        return s.exec(select(ProductionRun).where(ProductionRun.creative_id == creative_id)
                      .order_by(ProductionRun.id.desc())).first()


def say(run: ProductionRun, role: str, message: str) -> None:
    """Append to the run's crew log (shown as a timeline) and to the live progress loader."""
    from engine import progress as prg

    run.log = list(run.log or []) + [{"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "role": role, "message": message}]
    run.updated_at = datetime.now(timezone.utc)
    persist(run)
    prg.detail(run.app_id, f"{role.title()}: {message}")
    log.info(f"[crew #{run.creative_id}] {role}: {message}")


def tasks(run_id: int, version: Optional[int] = None, status: Optional[str] = None) -> list[ReviewTask]:
    from sqlmodel import select

    with session() as s:
        q = select(ReviewTask).where(ReviewTask.run_id == run_id)
        if version is not None:
            q = q.where(ReviewTask.version == version)
        if status:
            q = q.where(ReviewTask.status == status)
        return list(s.exec(q.order_by(ReviewTask.id)).all())


def creative_of(run: ProductionRun) -> Creative:
    with session() as s:
        c = s.get(Creative, run.creative_id)
    if c is None:
        raise ValueError(f"creative {run.creative_id} is gone")
    return c


def run_dir(run: ProductionRun) -> Path:
    c = creative_of(run)
    d = OUTPUTS_DIR / c.app_id / f"gen_{c.generation}" / str(c.id) / "production"
    d.mkdir(parents=True, exist_ok=True)
    return d


def version_dir(run: ProductionRun, version: int) -> Path:
    d = run_dir(run) / f"v{version}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def props_of(run: ProductionRun, version: int) -> dict[str, Any]:
    p = version_dir(run, version) / "props.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ---------------------------------------------------------------- media helpers

def extract_frame(video: Path, t: float, dest: Path, width: int = 540) -> Optional[Path]:
    try:
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(video),
                        "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(dest)],
                       check=True, capture_output=True, timeout=60)
        return dest if dest.exists() else None
    except (subprocess.SubprocessError, OSError) as e:
        log.warning(f"frame grab at {t:.1f}s failed: {e}")
        return None


def scene_bounds(props: dict[str, Any]) -> list[tuple[float, float]]:
    out, t = [], 0.0
    for d in props.get("sceneDurations") or []:
        out.append((t, t + d))
        t += d
    return out


def review_frames(run: ProductionRun, version: int, per_scene: int = 2) -> list[tuple[float, Path]]:
    """Frames the reviewers look at: inside every scene, plus the hook and the end card. Named by timecode."""
    vd = version_dir(run, version)
    video = vd / "video.mp4"
    props = props_of(run, version)
    fps = props.get("fps", 30)
    times = [0.9]  # hook sticker is on screen
    for a, b in scene_bounds(props):
        for k in range(per_scene):
            times.append(round(a + (b - a) * (k + 1) / (per_scene + 1), 2))
    total = props.get("totalFrames", 0) / fps
    if total:
        times.append(round(total - 1.0, 2))  # end card
    fdir = vd / "frames"
    fdir.mkdir(exist_ok=True)
    out = []
    for t in sorted(set(times)):
        f = extract_frame(video, t, fdir / f"t{t:06.2f}s.jpg")
        if f:
            out.append((t, f))
    return out


def extract_audio(run: ProductionRun, version: int) -> Optional[Path]:
    vd = version_dir(run, version)
    dest = vd / "audio.mp3"
    try:
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(vd / "video.mp4"), "-vn", "-ac", "1",
                        "-b:a", "64k", str(dest)], check=True, capture_output=True, timeout=120)
        return dest
    except (subprocess.SubprocessError, OSError) as e:
        log.warning(f"audio extract failed: {e}")
        return None


def plan_for_prompt(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Compact scene list for reviewer prompts (what each scene is meant to show and say)."""
    return [{"scene": i, "shot": s.get("shot"), "persona": s.get("persona") or None,
             "shows": s.get("image_prompt_en") or s.get("query_en") or s.get("clip_prompt_en") or "",
             "voiceover": s.get("voiceover", ""), "on_screen_text": s.get("on_screen_text", "")}
            for i, s in enumerate(plan.get("scenes") or [])]
