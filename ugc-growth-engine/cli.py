"""Command-line interface. Run `python cli.py --help`."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(add_completion=False, help="AI UGC & Social Growth Engine (India)")
console = Console()


def _register(brief: Path, enrich: bool = True) -> str:
    from engine.brief import load_brief, register_app

    return register_app(load_brief(brief), enrich=enrich).id


@app.command()
def understand(brief: Path = typer.Option(..., exists=True, help="Path to brief JSON"),
               enrich: bool = typer.Option(True, help="Enrich from Play Store if URL given")) -> None:
    """Brief (+ Play Store reviews) → personas, pain points, user phrases, key messages."""
    from engine import understand as und

    app_id = _register(brief, enrich)
    u = und.run(app_id)
    t = Table(title=f"Understanding · {app_id}")
    t.add_column("Section")
    t.add_column("Items")
    t.add_row("Personas", "\n".join(f"{p['name']} ({p['age_range']}, tier {p['city_tier']})" for p in u.personas))
    t.add_row("Pain points", "\n".join(p["pain"] for p in u.pain_points))
    t.add_row("User phrases", "\n".join(u.user_phrases))
    t.add_row("Key messages", "\n".join(f"{m['id']}: {m['message']}" for m in u.key_messages))
    t.add_row("India moments", "\n".join(m["moment"] for m in u.india_moments))
    console.print(t)


@app.command()
def research(brief: Optional[Path] = typer.Option(None, exists=True, help="brief JSON (registers the app)"),
             app_id: Optional[str] = typer.Option(None, help="existing app id"),
             force: bool = typer.Option(False, help="ignore the cached run and refresh"),
             purge_stale: bool = typer.Option(False, "--purge-stale", help="delete YouTube data older than 30 days")) -> None:
    """D8 market research: demand keywords → top Shorts → outlier scoring → AI analysis → Market Playbook."""
    from engine.research import run as rr

    if purge_stale:
        console.print(rr.purge_stale())
        if not (brief or app_id):
            return
    if app_id is None:
        if brief is None:
            raise typer.BadParameter("give --brief or --app-id")
        app_id = _register(brief)
    from engine import progress as prg

    with prg.tracked(app_id, "Market research", ["research"]):
        pb = rr.run_research(app_id, force=force, progress=console.print)
    s = rr.summary(pb.run_id)
    t = Table(title=f"Market research · {app_id} ({s['source']})")
    for k in ("keywords", "candidates", "relevant", "analysed", "quota_used", "gemini_calls"):
        t.add_column(k)
    t.add_row(*(str(s[k]) for k in ("keywords", "candidates", "relevant", "analysed", "quota_used", "gemini_calls")))
    console.print(t)
    console.print(pb.narrative)


@app.command()
def concepts(app_id: str, n: int = 10, generation: int = 1) -> None:
    """Generate UGC concepts for an app."""
    from engine import concepts as con

    for c in con.run(app_id, n=n, generation=generation):
        console.print(f"[bold]{c.id}[/] {c.title} · {c.format} · [dim]{c.india_scenario}[/]")


@app.command()
def generate(app_id: str,
             languages: Optional[str] = typer.Option(None, help="comma list, default = brief languages"),
             styles: Optional[str] = typer.Option(None, help="comma list of creator styles"),
             hooks_per_concept: int = 5,
             scripted: Optional[int] = None,
             render_count: Optional[int] = None,
             fresh: bool = typer.Option(False, help="clear previous generations first (market research is kept)")) -> None:
    """IDEAS ONLY (no videos): market research (cached) → understand → concepts → hooks → scripts → safety → picks."""
    from engine import db, loop, progress as prg
    from engine.locks import app_lock

    with app_lock(app_id, "generate ideas"), prg.tracked(app_id, "Generate video ideas (Gen 1)", prg.IDEA_STEPS):
        if fresh:
            db.reset_generations(app_id)
        langs = languages.split(",") if languages else None
        sty = styles.split(",") if styles else None
        loop.ensure_generation_one(app_id, render_count, progress=console.print, languages=langs, styles=sty,
                                   hooks_per_concept=hooks_per_concept, scripted=scripted)
    from engine.variations import matrix_counts

    console.print_json(json.dumps(matrix_counts(app_id, 1)))


@app.command()
def render(app_id: str, generation: Optional[int] = None, workers: Optional[int] = None,
           count: Optional[int] = typer.Option(None, help="re-pick this many of the best approved ideas first"),
           again: bool = typer.Option(False, help="re-render the currently selected videos (e.g. after fixes)")) -> None:
    """Render the creatives selected for render (voice, captions, music, end card) with live progress."""
    from engine import db, progress as prg, render as rnd, variations
    from engine.locks import app_lock

    gen = generation or db.current_generation(app_id)
    with app_lock(app_id, "rendering"), prg.tracked(app_id, f"Render videos · Gen {gen}", ["pick", f"render:{gen}"]):
        if again:
            from sqlmodel import update

            with db.session() as s:
                s.exec(update(db.Creative).where(db.Creative.app_id == app_id, db.Creative.generation == gen,
                                                 db.Creative.selected_for_render == True,  # noqa: E712
                                                 db.Creative.status == "rendered").values(status="approved"))
                s.commit()
        if count:
            prg.step(app_id, "pick", "running", f"Picking the best {count} (spread across languages and hook types)…")
            chosen = variations.select_for_render(app_id, gen, count)
            prg.step(app_id, "pick", "done", " · ".join(f"#{c.id} {c.language}/{c.hook_type}" for c in chosen))
        else:
            prg.step(app_id, "pick", "done", "Using the current selection")
        prg.step(app_id, f"render:{gen}", "running", "Voice → visuals → captions → music → export…")
        res = rnd.render_selected(app_id, gen, workers=workers,
                                  progress=lambda d, t, r: prg.detail(app_id, f"Rendered {d}/{t} videos"
                                                                      + (" (1 failed)" if "error" in r else "")))
        prg.step(app_id, f"render:{gen}", "done" if not res["failed"] else "failed",
                 f"{res['rendered']}/{res['requested']} videos in {res['wall_seconds']:.0f}s")
    console.print(res)


@app.command()
def simulate(app_id: str, generation: Optional[int] = None) -> None:
    """Fill SIMULATED metrics for rendered creatives of a generation."""
    from engine import db, simulator

    gen = generation or db.current_generation(app_id)
    n = simulator.simulate_generation(app_id, gen)
    console.print(f"SIMULATED metrics written for {n} creatives (gen {gen})")


@app.command("import-csv")
def import_csv(path: Path = typer.Argument(..., exists=True), platform: str = "instagram") -> None:
    """Import real metrics from CSV (columns = Metric table, `slug` identifies creative)."""
    from engine import metrics

    n, errors = metrics.import_csv(path.read_text(encoding="utf-8"), default_platform=platform)
    console.print(f"imported {n} rows; {len(errors)} errors")
    for e in errors[:20]:
        console.print(f"[red]{e}[/]")


@app.command()
def insights(app_id: str, generation: Optional[int] = None) -> None:
    """Statistical winners + LLM narrative."""
    from engine import insights_perf

    ins = insights_perf.run(app_id, generation)
    console.print(ins.narrative)


@app.command("next")
def next_batch(app_id: str, size: Optional[int] = None) -> None:
    """Generate the next generation from performance data (70% exploit / 30% explore)."""
    from engine import optimizer

    console.print_json(json.dumps(optimizer.generate_next_batch(app_id, size=size)))


@app.command()
def loop(app_id: Optional[str] = typer.Option(None),
         brief: Optional[Path] = typer.Option(None, help="brief JSON (used if app not registered)"),
         generations: int = 3,
         simulated: bool = typer.Option(True, "--simulated/--real"),
         render_count: Optional[int] = None,
         no_render: bool = typer.Option(False, help="skip video rendering (fast loop)")) -> None:
    """Run N full generations automatically (simulated market)."""
    from engine import loop as lp

    if app_id is None:
        brief = brief or Path("data/sample_briefs/fintech.json")
        app_id = _register(brief)
    from engine import progress as prg

    with prg.tracked(app_id, f"Growth loop · {generations} generations", prg.full_loop_steps(generations)):
        res = lp.run_loop(app_id, generations=generations, simulated=simulated, render_count=render_count,
                          render=not no_render)
    t = Table(title=f"Growth loop · {app_id} (SIMULATED)" if simulated else f"Growth loop · {app_id}")
    for col in ("gen", "creatives", "rendered", "installs/1k views", "CTR %", "exploit share"):
        t.add_column(col)
    for g in res["generations"]:
        t.add_row(str(g["generation"]), str(g["creatives"]), str(g["rendered"]),
                  f"{g['primary']:.2f}", f"{g['ctr'] * 100:.2f}", f"{g['exploit_share']:.0%}")
    console.print(t)
    if res.get("truth_check"):
        console.print("[bold]Hidden truth vs optimizer favourites[/]")
        console.print_json(json.dumps(res["truth_check"]))


@app.command()
def demo(brief: Path = typer.Option(Path("data/sample_briefs/fintech.json")), render_count: int = 6,
         fresh: bool = typer.Option(False, help="clear old generations first (keeps market research)")) -> None:
    """The judge demo: brief → Gen 1 → simulate → insights → Gen 2 → simulate → improvement."""
    import demo as dm

    if fresh:
        from engine import db
        from engine.brief import load_brief

        db.reset_generations(load_brief(brief).slug())
    console.print_json(json.dumps(dm.run_full_demo(brief, render_count=render_count), default=str))


@app.command()
def economics(app_id: str) -> None:
    """Cost + time ledger summary."""
    from engine import cost

    console.print_json(json.dumps(cost.summary(app_id), default=str))


# ---------------------------------------------------------------- production crew

crew = typer.Typer(help="Production crew: director → Magnific assets → editor → reviewers → QC → owner.")
app.add_typer(crew, name="crew")


def _print_run(run_id: int) -> None:
    from engine.production import pipeline
    from engine.production.common import fmt_tc, get_run, tasks

    run = get_run(run_id)
    console.print_json(json.dumps(pipeline.summary(run)))
    t = Table(title=f"Tasks · run {run.id}", show_lines=False)
    for col in ("v", "by", "→", "sev", "time", "scene", "issue", "fix", "status"):
        t.add_column(col)
    for x in tasks(run.id):
        t.add_row(str(x.version), x.raised_by, x.assigned_to, x.severity, fmt_tc(x.timecode),
                  "" if x.scene is None else str(x.scene), x.issue[:70], (x.fix or {}).get("type", ""), x.status)
    console.print(t)
    for e in (run.log or [])[-12:]:
        console.print(f"[dim]{e['at'][11:19]}[/dim] [bold]{e['role']}[/bold]: {e['message']}")


@crew.command("start")
def crew_start(creative_id: int, skip_assets: bool = typer.Option(False, help="don't wait for Magnific; use free fallbacks")) -> None:
    """Hand a scripted creative to the crew (stops at the Magnific job sheet if assets are needed)."""
    from engine.production import pipeline

    run = pipeline.start(creative_id, skip_assets=skip_assets)
    _print_run(run.id)


@crew.command("jobs")
def crew_jobs(run_id: int) -> None:
    """Print the Magnific job sheet (exact MCP calls) for a waiting run."""
    from engine.production import assets
    from engine.production.common import get_run

    console.print(assets.write_job_sheet(get_run(run_id)).read_text(encoding="utf-8"))


@crew.command("import")
def crew_import(run_id: int, request_id: str, source: str, credits: Optional[int] = None,
                creation: Optional[str] = None) -> None:
    """Import a finished Magnific asset (file path or download URL) and log its credits."""
    from engine.production import assets
    from engine.production.common import get_run

    r = assets.import_file(get_run(run_id), request_id, source, credits, creation)
    console.print(f"imported {request_id} → {r['file']} ({r['credits_used']} credits)")


@crew.command("resume")
def crew_resume(run_id: int, skip_assets: bool = False) -> None:
    """Continue after assets are imported: editor → reviewers → QC → (revisions) → owner."""
    from engine.production import pipeline

    pipeline.resume(run_id, skip_assets=skip_assets)
    _print_run(run_id)


@crew.command("status")
def crew_status(run_id: int) -> None:
    """Run summary, every review task and the crew log."""
    _print_run(run_id)


@crew.command("approve")
def crew_approve(run_id: int, note: str = "") -> None:
    """Owner approves → final export."""
    from engine.production import pipeline

    pipeline.approve(run_id, note)
    _print_run(run_id)


@crew.command("reject")
def crew_reject(run_id: int, note: str = typer.Option(..., help="what to change, in plain words")) -> None:
    """Owner sends the video back; the director turns the note into fixes and the crew cuts a new version."""
    from engine.production import pipeline

    pipeline.reject(run_id, note)
    _print_run(run_id)


# ---------------------------------------------------------------- onboarding (any app, from its brief alone)

@app.command()
def onboard(brief: Path = typer.Option(..., exists=True, help="the new app's brief JSON"),
            research: bool = typer.Option(True, help="run market research (YouTube + Apify + Gemini)"),
            n_protagonists: int = typer.Option(2, help="recurring characters who act (plus 1 presenter)")) -> None:
    """Set up a new app end to end: brief → market research → understanding → its own characters → its video-mode mix."""
    from engine import modes, progress as prg, understand as und
    from engine.production import personas
    from engine.research import run as rr

    app_id = _register(brief)
    steps = (["research"] if research else []) + ["understand"]
    with prg.tracked(app_id, f"Onboard {app_id}", steps):
        if research:
            try:
                rr.run_research(app_id, progress=console.print)
            except Exception as e:  # noqa: BLE001 — onboarding continues from the brief
                console.print(f"[yellow]research skipped: {str(e)[:200]}[/yellow]")
        prg.step(app_id, "understand", "running", "Understanding the app…")
        und.run(app_id)
        prg.step(app_id, "understand", "done", "Personas, pains, messages")
    cast = personas.propose(app_id, n_protagonists=n_protagonists)
    sheet = personas.write_sheet(app_id)
    shares, source = modes.planned_shares(app_id)
    t = Table(title=f"{app_id}: characters")
    for col in ("key", "name", "role", "who"):
        t.add_column(col)
    for p in cast:
        t.add_row(p.key, p.name, p.role, p.description[:80])
    console.print(t)
    console.print(f"Video-mode mix ({source}): " + ", ".join(f"{m} {v:.0%}" for m, v in shares.items()))
    console.print(f"Next: run the persona jobs in {sheet} (ask Claude), pick a face per character "
                  f"(`crew persona pick`), then `python cli.py generate {app_id}`.")


@app.command()
def go(app_id: str, size: int = typer.Option(10, help="scripts in this iteration"),
       force_research: bool = typer.Option(False, help="ignore this week's cached research")) -> None:
    """The dashboard's Go button: market research → understanding → the next scripts, then wait for approval."""
    from engine import batch, progress as prg, understand as und
    from engine.db import latest_understanding
    from engine.research import run as rr

    with prg.tracked(app_id, f"Engine run · {app_id}",
                     ["research", "understand", "concepts", "hooks", "scripts", "safety"]):
        try:
            rr.run_research(app_id, force=force_research, progress=console.print)
        except Exception as e:  # noqa: BLE001 — the brief alone still drives the rest
            prg.step(app_id, "research", "skipped", f"Research unavailable: {str(e)[:150]}")
        pb, u = rr.latest_playbook(app_id), latest_understanding(app_id)
        if u is None or (pb is not None and pb.created_at > u.created_at):
            prg.step(app_id, "understand", "running", "Reading the brief, reviews and research…")
            und.run(app_id)
            prg.step(app_id, "understand", "done", "Personas, pains, phrases and messages ready")
        else:
            prg.step(app_id, "understand", "done", "Up to date with the latest research")
        b = batch.build(app_id, size, track=False)
    console.print_json(json.dumps(batch.summary(b), default=str))


persona_cmd = typer.Typer(help="The app's recurring AI characters.")


@persona_cmd.command("propose")
def persona_propose(app_id: str, force: bool = False, n_protagonists: int = 2) -> None:
    """Cast characters from the app's own audience + brand tone, and write their Magnific job sheet."""
    from engine.production import personas

    personas.propose(app_id, n_protagonists=n_protagonists, force=force)
    console.print(personas.write_sheet(app_id).read_text(encoding="utf-8"))


@persona_cmd.command("jobs")
def persona_jobs(app_id: str) -> None:
    """Persona work still to do in Magnific (candidate portraits, library entries)."""
    from engine.production import personas

    console.print(personas.write_sheet(app_id).read_text(encoding="utf-8"))


@persona_cmd.command("import")
def persona_import(app_id: str, key: str, sources: list[str],
                   creations: str = typer.Option("", help="comma-separated Magnific creation ids, same order"),
                   credits: Optional[int] = None) -> None:
    """Import a character's candidate portraits (files or URLs)."""
    from engine.production import personas

    p = personas.import_candidates(app_id, key, sources, [x for x in creations.split(",") if x] or None, credits)
    console.print(f"{p.name}: {len(p.candidates)} candidates → pick one with `crew persona pick {app_id} {key} <n>`")


@persona_cmd.command("pick")
def persona_pick(app_id: str, key: str, index: int) -> None:
    """Choose candidate #n as the character's face."""
    from engine.production import personas

    p = personas.pick(app_id, key, index)
    console.print(f"{p.name}: candidate {index} chosen · next: library_create (see `crew persona jobs {app_id}`)")


@persona_cmd.command("library")
def persona_library(app_id: str, key: str, library_id: str) -> None:
    """Record the Magnific character-library id (same face in every later image/clip)."""
    from engine.production import personas

    p = personas.set_library(app_id, key, library_id)
    console.print(f"{p.name} is ready ({p.role})")


crew.add_typer(persona_cmd, name="persona")


@crew.command("resume-ready")
def crew_resume_ready(app_id: Optional[str] = typer.Option(None, help="one app (default: every app)"),
                      wait_minutes: int = typer.Option(30, help="how long to wait if the app is busy")) -> None:
    """Continue every run whose Magnific assets are all in — one at a time per app (runs never overlap)."""
    import time as _t

    from sqlmodel import select

    from engine.db import ProductionRun, session
    from engine.locks import is_busy
    from engine.production import assets, pipeline

    with session() as s:
        q = select(ProductionRun).where(ProductionRun.status == "waiting_assets")
        if app_id:
            q = q.where(ProductionRun.app_id == app_id)
        runs = [r for r in s.exec(q.order_by(ProductionRun.id)).all() if not assets.pending(r)]
    for r in runs:
        deadline = _t.time() + wait_minutes * 60
        while is_busy(r.app_id) and _t.time() < deadline:
            _t.sleep(10)
        console.print(f"▶ run {r.id} (creative #{r.creative_id}, {r.app_id})")
        try:
            pipeline.resume(r.id)
        except Exception as e:  # noqa: BLE001 — one run failing never stops the queue
            console.print(f"[red]run {r.id} failed: {str(e)[:200]}[/red]")
    console.print(f"done: {len(runs)} runs")


@crew.command("batch-jobs")
def crew_batch_jobs(app_id: Optional[str] = typer.Option(None, help="one app (default: every app)")) -> None:
    """One combined Magnific sheet: every waiting crew run's ready jobs + pending persona work, grouped by app."""
    from sqlmodel import select

    from engine.db import ProductionRun, list_apps, session
    from engine.production import assets, personas
    from engine.settings import OUTPUTS_DIR

    with session() as s:
        q = select(ProductionRun).where(ProductionRun.status == "waiting_assets")
        if app_id:
            q = q.where(ProductionRun.app_id == app_id)
        runs = list(s.exec(q).all())
    apps = [app_id] if app_id else [a.id for a in list_apps()]
    out = {"budget_left": assets.budget_left(), "apps": {}}
    for a in apps:
        entry = {"persona_jobs": personas.pending_jobs(a),
                 "runs": [{"run_id": r.id, "creative_id": r.creative_id, "ready_now": assets.ready(r),
                           "waiting": len(assets.pending(r)) - len(assets.ready(r))} for r in runs if r.app_id == a]}
        if entry["persona_jobs"] or entry["runs"]:
            out["apps"][a] = entry
    path = OUTPUTS_DIR / "magnific_batch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    n = sum(len(v["persona_jobs"]) + sum(len(r["ready_now"]) for r in v["runs"]) for v in out["apps"].values())
    console.print(f"{n} Magnific jobs ready across {len(out['apps'])} apps → {path}")


# ---------------------------------------------------------------- iterations: 10 scripts → owner approval → videos

batch_cmd = typer.Typer(help="Iterations of 10 scripts: build → owner approves on the dashboard → videos one by one.")
app.add_typer(batch_cmd, name="batch")


@batch_cmd.command("create")
def batch_create(app_id: str, size: int = 10) -> None:
    """Write the next 10 scripts (languages + video modes from this app's research). Nothing is filmed yet."""
    from engine import batch

    b = batch.build(app_id, size)
    console.print_json(json.dumps(batch.summary(b), default=str))


@batch_cmd.command("approve")
def batch_approve(batch_id: int) -> None:
    """Approve the batch (undecided scripts count as approved) and fix its Magnific credit ceiling."""
    from engine import batch

    console.print_json(json.dumps(batch.summary(batch.approve(batch_id)), default=str))


@batch_cmd.command("run")
def batch_run(batch_id: int) -> None:
    """Produce the approved scripts one video at a time (director → assets → editor → review → QC → owner)."""
    from engine import batch

    console.print_json(json.dumps(batch.summary(batch.process(batch_id)), default=str))


@batch_cmd.command("voice-jobs")
def batch_voice_jobs(batch_id: int) -> None:
    """Every voice line the batch still needs (exact Magnific MCP calls), for Claude to record."""
    from engine import batch
    from engine.production import assets
    from engine.production.common import latest_run

    out = []
    for c in batch.creatives_of(batch.get(batch_id)):
        run = latest_run(c.id)
        if run is None or c.status != "approved":
            continue
        for r in assets.pending(run):
            if r["kind"] == "voice":
                out.append({"run_id": run.id, "creative_id": c.id, "request_id": r["id"], **r["mcp"]["arguments"]})
    console.print_json(json.dumps(out, ensure_ascii=False))


@batch_cmd.command("status")
def batch_status(batch_id: int) -> None:
    from engine import batch

    b = batch.get(batch_id)
    console.print_json(json.dumps(batch.summary(b), default=str))
    for c in batch.creatives_of(b):
        console.print(f"#{c.id} [{c.status}] {c.language} · {c.visual_mode} · {c.creator_style} — {c.hook}")


@app.command("magnific-check")
def magnific_check() -> None:
    """Free check that MAGNIFIC_API_KEY works (lists tasks; generates nothing, spends no credits)."""
    from engine import magnific

    console.print(magnific.check())


if __name__ == "__main__":
    app()
