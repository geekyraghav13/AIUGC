"""Orchestrates full generations: generate → render → (simulate) → insights → next batch → repeat."""
from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Optional

from engine import progress as prg
from engine import insights_perf, optimizer, render as rnd, simulator, understand, variations
from engine.db import creatives, current_generation, latest_understanding, save
from engine.metrics import metrics_frame, primary_metric_name, totals
from engine.settings import log

Progress = Optional[Callable[[str], None]]


def _say(progress: Progress, msg: str, app_id: Optional[str] = None) -> None:
    log.info(msg)
    if progress:
        progress(msg)
    if app_id:
        prg.detail(app_id, msg)


def publish_without_render(app_id: str, generation: int) -> int:
    """Fast mode: mark selected creatives as published (no video) so the simulator can test them."""
    rows = [c for c in creatives(app_id, generation, selected_for_render=True) if c.status in ("approved", "draft")]
    for c in rows:
        c.status = "published"
    save(*rows)
    return len(rows)


def generation_summary(app_id: str) -> list[dict[str, Any]]:
    """Primary metric + CTR per generation (ratio of sums), batch size and exploit share."""
    df = metrics_frame(app_id)
    out = []
    if df.empty:
        return out
    metric = primary_metric_name(df)
    for g, sub in df.groupby("generation"):
        t = totals(sub)
        rows = creatives(app_id, int(g))
        out.append({
            "generation": int(g), "creatives": len(sub), "rendered": sum(1 for c in rows if c.status == "rendered"),
            "primary": float(t.get(metric, float("nan"))), "metric": metric, "ctr": float(t["ctr"]),
            "hook_rate": float(t["hook_rate"]), "installs": float(t.get("installs") or 0), "views": float(t["views"]),
            "exploit_share": float((sub["origin"] == "exploit").mean()),
        })
    return out


def truth_check(app_id: str) -> dict[str, Any]:
    """Simulation only: does the optimizer's favourite value per dimension match the hidden best?

    'favoured' = highest posterior-mean effect in the optimizer's joint model (among tested values).
    """
    truth = simulator.reveal_truth(app_id)
    df = metrics_frame(app_id)
    if df.empty:
        return {}
    metric = primary_metric_name(df)
    latest = creatives(app_id, current_generation(app_id))
    from engine.research.market import context

    model = optimizer.EffectModel(df, metric, optimizer.arms(app_id, df), market=context(app_id))
    out = {}
    for d in optimizer.OPT_DIMS:
        tested = dict(truth.get(d, []))
        if len(tested) < 2:
            continue
        favoured = model.favourite(d, among=list(tested))
        ranked = sorted(tested, key=tested.get, reverse=True)
        share = Counter(getattr(c, d) for c in latest)
        out[d] = {"favoured": favoured, "true_best": ranked[0], "match": favoured == ranked[0],
                  "favoured_true_rank": ranked.index(favoured) + 1, "values_tested": len(ranked),
                  "latest_batch_share_of_true_best": round(share.get(ranked[0], 0) / max(1, len(latest)), 2)}
    return out


def ensure_research(app_id: str, progress: Progress = None) -> bool:
    """Run D8 market research if there is no playbook or it is older than refresh_days. Never blocks the loop."""
    from engine.research import youtube_mode
    from engine.research import run as research

    if research.is_fresh(app_id):
        prg.step(app_id, "research", "done", "Using this week's research (cached, no cost)")
        return True
    if youtube_mode() == "none":
        _say(progress, "Market research skipped: add YOUTUBE_API_KEY to .env to ground Gen 1 in what works now")
        prg.step(app_id, "research", "skipped", "No YOUTUBE_API_KEY — ideas come from the brief only")
        return False
    try:
        research.run_research(app_id, progress=progress)
        return True
    except Exception as e:  # noqa: BLE001 — research is an enrichment; generation continues without it
        from engine.locks import AppBusy

        if isinstance(e, AppBusy):
            raise
        _say(progress, f"Market research failed ({str(e)[:160]}); continuing from the brief only")
        prg.step(app_id, "research", "failed", f"{str(e)[:160]} — continuing from the brief only")
        return False


def ensure_generation_one(app_id: str, render_count: Optional[int], progress: Progress = None,
                          **gen1_kwargs: Any) -> None:
    from engine.locks import app_lock

    with app_lock(app_id, "generation 1"):
        _ensure_generation_one(app_id, render_count, progress, **gen1_kwargs)


def _ensure_generation_one(app_id: str, render_count: Optional[int], progress: Progress = None,
                           **gen1_kwargs: Any) -> None:
    ensure_research(app_id, progress)
    if latest_understanding(app_id) is None:
        _say(progress, "Understanding the app (brief + reviews)…")
        prg.step(app_id, "understand", "running", "Reading the brief, reviews and market playbook…")
        u = understand.run(app_id)
        prg.step(app_id, "understand", "done", f"{len(u.personas)} personas · {len(u.pain_points)} pain points · "
                                              f"{len(u.key_messages)} key messages")
    else:
        prg.step(app_id, "understand", "done", "Already done")
    if current_generation(app_id) == 0:
        _say(progress, "Generating Gen 1: concepts → hook matrix → scripts → compliance…")
        variations.run_generation_one(app_id, render_count=render_count, **gen1_kwargs)
    elif variations.resume_generation_one(app_id, render_count):
        _say(progress, "Resumed an interrupted Gen 1 (safety check + picks)")
    else:
        for k in ("concepts", "hooks", "scripts", "safety", "pick"):
            prg.step(app_id, k, "done", "Already done")


def run_generation(app_id: str, generation: int, simulated: bool, render: bool, progress: Progress = None,
                   render_progress: Optional[Callable] = None) -> None:
    if render:
        _say(progress, f"Rendering Gen {generation} videos…")
        prg.step(app_id, f"render:{generation}", "running", "Voice → visuals → captions → music → export…")

        def rp(done: int, total: int, r: dict) -> None:
            prg.detail(app_id, f"Rendered {done}/{total} videos" + (" (1 failed)" if "error" in r else ""))
            if render_progress:
                render_progress(done, total, r)

        res = rnd.render_selected(app_id, generation, progress=rp)
        _say(progress, f"Gen {generation}: rendered {res['rendered']}/{res['requested']} in {res['wall_seconds']}s")
        prg.step(app_id, f"render:{generation}", "done",
                 f"{res['rendered']}/{res['requested']} videos in {res['wall_seconds']:.0f}s"
                 + (f" · {res['failed']} failed" if res["failed"] else "")
                 + (f" · {res['sent_to_crew']} character videos with the production crew" if res.get("sent_to_crew") else ""))
        crew_waiting = bool(res.get("sent_to_crew"))
    else:
        crew_waiting = False
        publish_without_render(app_id, generation)
        prg.step(app_id, f"render:{generation}", "skipped", "Fast mode: no videos rendered")
    if simulated:
        prg.step(app_id, f"test:{generation}", "running", "Simulated market is 'watching' the videos…")
        # in simulation, videos still with the crew are tested too, so the loop learns every visual mode
        n = simulator.simulate_generation(app_id, generation, include_unrendered=not render or crew_waiting)
        _say(progress, f"Simulated market: SIMULATED metrics for {n} Gen {generation} creatives")
        prg.step(app_id, f"test:{generation}", "done", f"SIMULATED results for {n} videos")
    _say(progress, f"Computing insights after Gen {generation}…")
    prg.step(app_id, f"insights:{generation}", "running", "Lift + probability best per tag → AI summary…")
    ins = insights_perf.run(app_id, generation)
    prg.step(app_id, f"insights:{generation}", "done",
             (ins.findings or {}).get("findings", ["No clear winner yet"])[0][:160] if ins.findings else "No data")


def run_loop(app_id: str, generations: int = 3, simulated: bool = True, render_count: Optional[int] = None,
             render: bool = True, next_size: Optional[int] = None, progress: Progress = None,
             render_progress: Optional[Callable] = None) -> dict[str, Any]:
    """Run until `generations` generations exist and have been tested (resumes where it left off)."""
    from engine.locks import app_lock

    with app_lock(app_id, "growth loop"):
        return _run_loop(app_id, generations, simulated, render_count, render, next_size, progress, render_progress)


def _run_loop(app_id: str, generations: int, simulated: bool, render_count: Optional[int], render: bool,
              next_size: Optional[int], progress: Progress, render_progress: Optional[Callable]) -> dict[str, Any]:
    ensure_generation_one(app_id, render_count, progress)
    df = metrics_frame(app_id)
    tested = set(df["generation"].unique()) if not df.empty else set()
    g = 1
    while True:
        if g not in tested:
            run_generation(app_id, g, simulated, render, progress, render_progress)
        if g >= generations:
            break
        if current_generation(app_id) <= g:
            _say(progress, f"Growth loop: generating Gen {g + 1} from Gen 1–{g} learnings (70% exploit / 30% explore)…")
            prg.step(app_id, f"next:{g + 1}", "running", "Thompson sampling → new hooks → scripts → safety…")
            alloc = optimizer.generate_next_batch(app_id, size=next_size or render_count)
            prg.step(app_id, f"next:{g + 1}", "done",
                     f"{alloc['created']} new creatives ({alloc['origin_counts'].get('exploit', 0)} exploit / "
                     f"{alloc['origin_counts'].get('explore', 0)} explore)")
        g += 1
    return {"app_id": app_id, "generations": generation_summary(app_id),
            "truth_check": truth_check(app_id) if simulated else None}
