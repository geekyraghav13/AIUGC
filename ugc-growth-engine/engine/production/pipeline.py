"""The production flow for one creative:

    OWNER (brief + script) → DIRECTOR (scene plan) → MAGNIFIC ASSETS (voice, hook clip) → EDITOR (v1)
      → REVIEWERS (text · visual · audio · brand) → QC (verdict + revision tasks)
      → fail: tasks go to the role that defines the fix → EDITOR (v2) → REVIEWERS → QC … (≤ max_revision_rounds)
      → pass: OWNER approves (final export) or sends back with a note → DIRECTOR turns it into fixes → EDITOR …
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import delete

from engine import progress as prg
from engine.db import Creative, ProductionRun, ReviewTask, Video, save, session
from engine.locks import app_lock
from engine.production import assets, director, editor, qc, reviewers
from engine.production.common import cfg, creative_of, get_run, persist, say, tasks
from engine.settings import OUTPUTS_DIR

STEPS = ["director", "assets", "edit", "review", "qc", "owner"]


def _stage(run: ProductionRun, stage: str, status: str = "running") -> None:
    run.stage, run.status = stage, status
    persist(run)


def start(creative_id: int, skip_assets: bool = False, track: bool = True) -> ProductionRun:
    """Hand a scripted creative to the crew. Stops at 'waiting_assets' if Magnific jobs are needed.
    `track=False` when called inside another tracked run (e.g. a batch render), so its progress isn't replaced."""
    from contextlib import nullcontext

    with session() as s:
        c = s.get(Creative, creative_id)
    if c is None or not c.script:
        raise ValueError(f"creative {creative_id} has no script")
    tracker = prg.tracked(c.app_id, f"Production crew · creative #{c.id}", STEPS) if track else nullcontext()
    with app_lock(c.app_id, "production crew"), tracker:
        run = ProductionRun(app_id=c.app_id, creative_id=c.id)
        save(run)
        say(run, "owner", f"Brief: {c.language} · {c.creator_style} · “{c.hook[:80]}” — produce it")
        prg.step(c.app_id, "director", "running", "Scene-by-scene plan…")
        director.direct(run)
        run.plan["rounds_left"] = int(cfg()["max_revision_rounds"])
        persist(run)
        prg.step(c.app_id, "director", "done", run.plan.get("director_note", "")[:160] or "Plan ready")
        return _advance(run, skip_assets)


def resume(run_id: int, skip_assets: bool = False, track: bool = True) -> ProductionRun:
    """Continue after Magnific assets were imported (or skip them and use the free fallbacks)."""
    from contextlib import nullcontext

    run = get_run(run_id)
    tracker = prg.tracked(run.app_id, f"Production crew · creative #{run.creative_id}", STEPS[1:]) if track else nullcontext()
    with app_lock(run.app_id, "production crew"), tracker:
        return _advance(run, skip_assets)


def _advance(run: ProductionRun, skip_assets: bool) -> ProductionRun:
    while True:
        if assets.pending(run):
            if skip_assets:
                assets.skip_pending(run, "skipped by request")
            else:
                sheet = assets.write_job_sheet(run)
                _stage(run, "assets", "waiting_assets")
                n = len(assets.pending(run))
                say(run, "assets", f"Waiting for {n} Magnific jobs (~{sum(r['credits'] for r in assets.pending(run))} "
                                   f"credits) — job sheet: {sheet.name}")
                prg.step(run.app_id, "assets", "running", f"{n} Magnific jobs to run (voice / hook clip)")
                return run
        prg.step(run.app_id, "assets", "done", "All assets in place")

        _stage(run, "editor")
        prg.step(run.app_id, "edit", "running", f"Cutting v{run.version + 1}…")
        editor.render_version(run)
        prg.step(run.app_id, "review", "running", f"Reviewing v{run.version}…")
        _stage(run, "review")
        found = reviewers.review(run, run.version)
        _stage(run, "qc")
        prg.step(run.app_id, "qc", "running", f"QC on v{run.version}…")
        v = qc.verdict(run, run.version, found)
        if v["passed"]:
            break
        left = int(run.plan.get("rounds_left", 0)) - 1
        run.plan["rounds_left"] = left
        if left <= 0:
            say(run, "qc", "Revision limit reached — sending to the owner with the open tasks listed")
            break
        open_tasks = [t for t in tasks(run.id, version=run.version, status="open")]
        counts = editor.apply_tasks(run, open_tasks)
        if not counts["applied"]:
            say(run, "qc", "Nothing left the crew can fix on its own — the owner decides")
            break
        say(run, "qc", f"Revision round: {counts['applied']} fixes applied, {counts['needs_human']} need a human → v{run.version + 1}")

    prg.step(run.app_id, "qc", "done", f"v{run.version} · score {run.qc_score:.0f}")
    if cfg().get("owner_auto_approve") and run.versions and run.versions[-1].get("passed"):
        return approve(run.id, "auto-approved (QC pass, batch mode)")
    _stage(run, "owner", "awaiting_owner")
    prg.step(run.app_id, "owner", "running", f"v{run.version} is waiting for the owner's approval")
    say(run, "owner", f"v{run.version} ready for review (QC score {run.qc_score:.0f})")
    return run


# ---------------------------------------------------------------- owner

def approve(run_id: int, note: str = "") -> ProductionRun:
    """Owner approves → final export: the approved version becomes the creative's video; the fixed script is saved."""
    from engine.render import scripted_with_plan

    run = get_run(run_id)
    c = creative_of(run)
    src = OUTPUTS_DIR / c.app_id / f"gen_{c.generation}" / str(c.id) / "production" / f"v{run.version}"
    dest = src.parent.parent
    for name in ("video.mp4", "thumbnail.jpg", "caption.txt", "script.json"):
        if (src / name).exists():
            shutil.copy2(src / name, dest / name)
    ver = run.versions[-1] if run.versions else {}
    with session() as s:
        row = s.get(Creative, c.id)
        row.script = scripted_with_plan(row.script, run.plan)
        row.hook = row.script["scenes"][0]["voiceover"]
        row.status = "rendered"
        s.add(row)
        s.exec(delete(Video).where(Video.creative_id == c.id))
        s.add(Video(creative_id=c.id, path=str(dest / "video.mp4"), thumbnail_path=str(dest / "thumbnail.jpg"),
                    duration_s=ver.get("duration", 0.0), render_seconds=ver.get("render_seconds", 0.0)))
        s.commit()
    run.owner_note, run.stage, run.status = note, "done", "approved"
    persist(run)
    say(run, "owner", f"APPROVED v{run.version}" + (f" — {note}" if note else "") + " · exported as the final video")
    prg.step(run.app_id, "owner", "done", f"Approved v{run.version}")
    return run


def reject(run_id: int, note: str, rerun: bool = True) -> ProductionRun:
    """Owner sends it back: the director turns the note into concrete fixes, then the crew cuts a new version."""
    run = get_run(run_id)
    say(run, "owner", f"SENT BACK v{run.version}: {note}")
    out = director.interpret_owner_note(run, note)
    rows = [ReviewTask(run_id=run.id, version=run.version, raised_by="owner", assigned_to="director",
                       severity="major", scene=f.scene, issue=note[:300], fix=f.fix, status="open")
            for f in out.fixes] or [ReviewTask(run_id=run.id, version=run.version, raised_by="owner",
                                               assigned_to="owner", severity="major", issue=note[:300],
                                               fix={"type": "none"}, status="needs_human")]
    save(*rows)
    say(run, "director", out.reply_to_owner or f"{len(out.fixes)} changes planned from the owner's note")
    counts = editor.apply_tasks(run, [r for r in rows if r.status == "open"])
    run.owner_note = note
    run.plan["rounds_left"] = int(cfg()["max_revision_rounds"])
    _stage(run, "editor", "running")
    if not rerun or not counts["applied"]:
        _stage(run, "owner", "awaiting_owner")
        return run
    return resume(run.id)


def summary(run: ProductionRun) -> dict:
    ts = tasks(run.id)
    return {"run_id": run.id, "creative_id": run.creative_id, "status": run.status, "stage": run.stage,
            "version": run.version, "qc_score": run.qc_score,
            "open_tasks": sum(1 for t in ts if t.status == "open"),
            "fixed_tasks": sum(1 for t in ts if t.status == "fixed"),
            "needs_human": sum(1 for t in ts if t.status == "needs_human"),
            "pending_assets": len(assets.pending(run)), "credits_estimate": run.credits_estimate,
            "updated": (run.updated_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")}
