"""QC / QA REVIEW — technical checks on the file, then the verdict over every reviewer's findings. Failures become
specific revision tasks, each assigned to the role that must define the fix (text, audio, director, assets, …)."""
from __future__ import annotations

import json
import subprocess
from typing import Any

from engine.db import ProductionRun, ReviewTask, save
from engine.production.common import (FIX_OWNER, SEVERITY_WEIGHT, Issue, cfg, fmt_tc, persist, say, version_dir)
from engine.settings import ffprobe_bin


def technical(run: ProductionRun, version: int) -> list[Issue]:
    """File-level checks: format, frame rate, length, audio present, cover art embedded."""
    video = version_dir(run, version) / "video.mp4"
    issues: list[Issue] = []
    try:
        out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
                             capture_output=True, text=True, timeout=60).stdout
        info = json.loads(out)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as e:
        return [Issue(category="file", severity="blocker", issue=f"video can't be read: {e}", fix={"type": "none"})]
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v or (v.get("width"), v.get("height")) != (1080, 1920):
        issues.append(Issue(category="format", severity="blocker", issue="video is not 1080×1920", fix={"type": "none"}))
    elif v.get("r_frame_rate") not in ("30/1", "30000/1001"):
        issues.append(Issue(category="format", severity="minor", issue=f"frame rate {v.get('r_frame_rate')} (want 30)",
                            fix={"type": "none"}))
    if not a:
        issues.append(Issue(category="audio", severity="blocker", issue="no audio track", fix={"type": "none"}))
    if not any(s.get("disposition", {}).get("attached_pic") for s in streams):
        issues.append(Issue(category="cover", severity="minor", issue="no cover image embedded", fix={"type": "none"}))
    dur = float(info.get("format", {}).get("duration", 0))
    lo, hi = cfg()["review"]["duration_range_s"]
    if not lo <= dur <= hi:
        issues.append(Issue(category="length", severity="major", issue=f"video is {dur:.1f}s (want {lo}–{hi}s)",
                            fix={"type": "set_pace", "pace": "fast"} if dur > hi else {"type": "none"}))
    return issues


def _key(role: str, i: Issue) -> tuple:
    # without a scene, fall back to the timecode so issues about different moments never collapse into one
    where = i.scene if i.scene is not None else (round(i.timecode, 1) if i.timecode is not None else i.issue[:40])
    return (where, i.category, (i.fix or {}).get("type"), (i.fix or {}).get("field"))


def dedupe(found: dict[str, list[Issue]]) -> list[tuple[str, Issue]]:
    """One issue per (scene, category, fix); keep the most severe one."""
    order = {"blocker": 0, "major": 1, "minor": 2}
    best: dict[tuple, tuple[str, Issue]] = {}
    for role, issues in found.items():
        for i in issues:
            k = _key(role, i)
            if k not in best or order[i.severity] < order[best[k][1].severity]:
                best[k] = (role, i)
    return sorted(best.values(), key=lambda ri: (order[ri[1].severity], ri[1].timecode or 0))


def score(issues: list[tuple[str, Issue]]) -> float:
    return float(max(0, 100 - sum(SEVERITY_WEIGHT[i.severity] for _, i in issues)))


def verdict(run: ProductionRun, version: int, found: dict[str, list[Issue]]) -> dict[str, Any]:
    """Score the version, file revision tasks, and say whether it passes to the owner."""
    found = {**found, "qc": technical(run, version)}
    issues = dedupe(found)
    sc = score(issues)
    blocking = [i for _, i in issues if i.severity in ("blocker", "major")]
    passed = not blocking and sc >= float(cfg()["qc_pass_score"])
    rows = []
    for role, i in issues:
        fix_type = (i.fix or {}).get("type") or "none"
        rows.append(ReviewTask(run_id=run.id, version=version, raised_by=role,
                               assigned_to=FIX_OWNER.get(fix_type, "owner"), severity=i.severity, category=i.category,
                               timecode=i.timecode, scene=i.scene, issue=i.issue, fix=i.fix or {"type": "none"},
                               status="open"))
    if rows:
        save(*rows)
    run.qc_score = sc
    run.versions = [dict(v, qc_score=sc, passed=passed, issues=len(issues)) if v["version"] == version else v
                    for v in run.versions or []]
    persist(run)
    top = "; ".join(f"{fmt_tc(i.timecode)} {i.issue[:60]} → {FIX_OWNER.get((i.fix or {}).get('type'), 'owner')}"
                    for _, i in issues if i.severity != "minor")[:400]
    say(run, "qc", f"v{version}: {'PASS' if passed else 'FAIL'} · score {sc:.0f} · {len(issues)} tasks"
                   + (f" · {top}" if top else ""))
    return {"passed": passed, "score": sc, "tasks": rows}
