"""Orchestrates one research run: keywords → discover → scoring → analysis → patterns → playbook."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pandas as pd
from sqlmodel import delete, select

from engine import cost
from engine import progress as prg
from engine.brief import brief_of
from engine.db import (Keyword, MarketAnalysis, MarketPattern, MarketPlaybook, MarketVideo, ResearchRun, get_app,
                       save, session)
from engine.research import analyze, cfg, discover, instagram, keywords, patterns, playbook, scoring, youtube_mode
from engine.settings import CACHE_DIR, log

Progress = Optional[Callable[[str], None]]


def _say(progress: Progress, msg: str) -> None:
    log.info(msg)
    if progress:
        progress(msg)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- queries used by other modules

def latest_playbook(app_id: str) -> Optional[MarketPlaybook]:
    with session() as s:
        return s.exec(select(MarketPlaybook).where(MarketPlaybook.app_id == app_id)
                      .order_by(MarketPlaybook.id.desc())).first()


def is_fresh(app_id: str) -> bool:
    pb = latest_playbook(app_id)
    if pb is None:
        return False
    age = datetime.now(timezone.utc) - _aware(pb.created_at)
    return age < timedelta(days=float(cfg()["refresh_days"]))


def market_patterns(app_id: str) -> pd.DataFrame:
    """Pattern rows of the latest playbook's run (empty frame if no research)."""
    pb = latest_playbook(app_id)
    if pb is None:
        return pd.DataFrame()
    with session() as s:
        rows = s.exec(select(MarketPattern).where(MarketPattern.run_id == pb.run_id)).all()
    return pd.DataFrame([r.model_dump() for r in rows])


def market_hooks(app_id: Optional[str] = None) -> list[str]:
    """Stored `hook_first_3s` lines (for copy detection in compliance)."""
    with session() as s:
        q = select(MarketAnalysis.analysis)
        if app_id:
            runs = [r for r in s.exec(select(ResearchRun.id).where(ResearchRun.app_id == app_id)).all()]
            if not runs:
                return []
            vids = s.exec(select(MarketVideo.id).where(MarketVideo.run_id.in_(runs))).all()
            q = q.where(MarketAnalysis.market_video_id.in_(list(vids)))
        return [a.get("hook_first_3s", "") for a in s.exec(q).all() if a and a.get("hook_first_3s")]


def run_frames(run_id: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(keywords, all candidate videos, analysed relevant videos A) for a run."""
    with session() as s:
        kw = pd.DataFrame([k.model_dump() for k in s.exec(select(Keyword).where(Keyword.run_id == run_id)).all()])
        vids = list(s.exec(select(MarketVideo).where(MarketVideo.run_id == run_id)).all())
        ids = [v.id for v in vids]
        an = list(s.exec(select(MarketAnalysis).where(MarketAnalysis.market_video_id.in_(ids))).all()) if ids else []
    V = pd.DataFrame([v.model_dump() for v in vids])
    rows = []
    by_id = {v.id: v for v in vids}
    for a in an:
        v = by_id.get(a.market_video_id)
        if v is None or not a.analysis.get("is_relevant", True):
            continue
        rows.append({**v.model_dump(), **a.analysis, "market_video_id": v.id, "model": a.model})
    return kw, V, pd.DataFrame(rows)


# ---------------------------------------------------------------- main run

def run_research(app_id: str, force: bool = False, progress: Progress = None) -> Optional[MarketPlaybook]:
    """Run D8 for an app. Reuses the latest playbook if younger than refresh_days (unless force)."""
    from engine.locks import app_lock

    with app_lock(app_id, "market research"):
        _close_orphan_runs(app_id)
        return _run_research(app_id, force, progress)


def _close_orphan_runs(app_id: str) -> None:
    """Runs left 'running' by a crashed/killed process can't still be running while we hold the lock."""
    with session() as s:
        for r in s.exec(select(ResearchRun).where(ResearchRun.app_id == app_id, ResearchRun.status == "running")).all():
            r.status, r.notes = "failed", (r.notes + " · interrupted").strip(" ·")
            r.finished_at = r.finished_at or datetime.now(timezone.utc)
            s.add(r)
        s.commit()


def _run_research(app_id: str, force: bool, progress: Progress) -> Optional[MarketPlaybook]:
    def say(msg: str) -> None:
        _say(progress, msg)
        prg.detail(app_id, msg.strip())

    prg.step(app_id, "research", "running", "Checking for a recent research run…")
    if not force and is_fresh(app_id):
        say("Market research: using cached run (< refresh_days old)")
        prg.step(app_id, "research", "done", "Using this week's research (cached, no cost)")
        return latest_playbook(app_id)
    app = get_app(app_id)
    if app is None:
        raise ValueError(f"unknown app {app_id}")
    mode = youtube_mode()
    if mode == "none":
        discover.client(app_id)  # raises with setup instructions
    c = cfg()
    b = brief_of(app)
    langs = b.languages
    run = ResearchRun(app_id=app_id, source=mode)
    save(run)
    t0 = time.time()
    try:
        # 1. demand keywords
        say("Market research 1/6: demand keywords (LLM seeds → YouTube autocomplete)…")
        seeds = keywords.seed_keywords(b, langs, app_id)
        cand = keywords.expand(seeds, offline=(mode == "mock"), app_id=app_id)
        trends = keywords.trends_interest(list(cand[cand["relevant"]].sort_values("autocomplete_hits",
                                                                                   ascending=False)["text"].head(25)))
        cand["trends_interest"] = cand["text"].map(trends) if trends else None
        cand["prelim"] = keywords.preliminary_score(cand, trends)
        to_search = keywords.select_for_search(cand, int(c["top_keywords"]))
        say(f"  {len(cand)} candidate keywords ({int(cand['relevant'].sum())} relevant); "
                       f"searching the top {len(to_search)}")

        # 2. discover
        say("Market research 2/6: searching YouTube Shorts (India, last "
                       f"{c['days_back']} days)…")
        yt = discover.client(app_id, seeds.category_terms)
        rows, per_kw, stopped = discover.discover(yt, list(to_search["text"]), seeds.category_terms,
                                                  seeds.object_terms, progress=None)
        df = pd.DataFrame(rows)
        say(f"  {len(df)} candidate Shorts, {int(df['relevant'].sum()) if not df.empty else 0} relevant"
                       + (" (stopped at quota budget)" if stopped else ""))
        widened = False
        if (df.empty or int(df["relevant"].sum()) < int(c.get("min_relevant", 10))) and not stopped:
            # niche app: few strict matches → widen (any topic term, lower view floor, longer window)
            say(f"  Few relevant Shorts — widening: any topic term, ≥ {int(c['widen_min_views']):,} views, "
                f"last {int(c['widen_days_back'])} days")
            rows2, per_kw2, stopped = discover.discover(yt, list(to_search["text"]), seeds.category_terms,
                                                         seeds.object_terms, min_views=int(c["widen_min_views"]),
                                                         days_back=int(c["widen_days_back"]))
            df = pd.DataFrame(rows2)
            if not df.empty:
                df["relevant"] = df["relevant"] | df["relevant_loose"]
            per_kw, widened = (per_kw2 or per_kw), True
            say(f"  after widening: {len(df)} candidate Shorts, {int(df['relevant'].sum()) if not df.empty else 0} relevant")

        # 3. baselines + outlier scoring
        say("Market research 3/6: creator baselines → outlier scores…")
        if not df.empty:
            info = scoring.channel_baselines(yt, df[df["relevant"]]["channel_id"].unique())
            base = pd.DataFrame.from_dict(info, orient="index")
            for col in ("channel_avg_views", "median_recent_views", "baseline_sample"):
                df[col] = df["channel_id"].map(base[col]) if col in base else None
            df = scoring.score_videos(df)
        if not df.empty:
            df["platform"] = "youtube" if mode == "youtube" else "youtube_mock"

        # 3b. Instagram Reels via Apify (paid, capped) — real runs only
        ig, ig_top = pd.DataFrame(), pd.DataFrame()
        if instagram.enabled() and mode == "youtube":
            say("Market research 3b: Instagram Reels via Apify (hashtags → Reels → creator baselines)…")
            try:
                tags = seeds.hashtags or [instagram.to_hashtag(t) for t in to_search["text"]]
                ig = pd.DataFrame(instagram.discover(tags, seeds.category_terms, seeds.object_terms, app_id))
                if not ig.empty and int(ig["relevant"].sum()) < 5 and "relevant_loose" in ig:
                    ig["relevant"] = ig["relevant"] | ig["relevant_loose"]   # niche topic: any topic term counts
                if not ig.empty:
                    owners = list(ig[ig["relevant"]].sort_values("views", ascending=False)["channel_id"].unique())
                    info = instagram.baselines(owners, app_id)
                    for col in ("channel_avg_views", "median_recent_views", "baseline_sample"):
                        ig[col] = ig["channel_id"].map({o: d[col] for o, d in info.items()})
                    ig = scoring.score_videos(ig)
                    ig_top = scoring.select_top(ig, int(c.get("instagram_analyze_n", 15)), int(c["max_per_channel"]))
                    tr = instagram.transcripts(list(ig_top["url"]), app_id)
                    ig_top = ig_top.assign(transcript=ig_top["video_id"].map(tr))
                say(f"  {len(ig)} Instagram Reels, {int(ig['relevant'].sum()) if not ig.empty else 0} relevant")
            except Exception as e:  # noqa: BLE001 — Instagram is an extra source; YouTube results stand alone
                say(f"  Instagram skipped: {str(e)[:160]}")
                ig, ig_top = pd.DataFrame(), pd.DataFrame()

        views_by_kw = {kw: int(df[df["video_id"].isin(ids[:10])]["views"].sum()) if not df.empty else 0
                       for kw, ids in per_kw.items()}
        cand["searched"] = cand["text"].isin(per_kw)
        cand["top10_views"] = cand["text"].map(views_by_kw)
        cand["demand_score"] = keywords.demand_scores(cand)
        kw_rows = [Keyword(app_id=app_id, run_id=run.id, text=r.text, language=r.language, source=r.source,
                           autocomplete_hits=int(r.autocomplete_hits),
                           trends_interest=None if pd.isna(r.trends_interest) else float(r.trends_interest),
                           top10_views=None if pd.isna(r.top10_views) else int(r.top10_views),
                           searched=bool(r.searched), demand_score=float(r.demand_score))
                   for r in cand[cand["relevant"]].sort_values("demand_score", ascending=False)
                   .head(max(50, int(c["top_keywords"]))).itertuples()]
        save(*kw_rows)
        top = scoring.select_top(df, int(c["analyze_n"]), int(c["max_per_channel"])) if not df.empty else df
        top = pd.concat([t for t in (top, ig_top) if not t.empty], ignore_index=True) if not (top.empty and ig_top.empty) else top
        selected = set(top["video_id"]) if not top.empty else set()
        all_rows = pd.concat([t for t in (df, ig) if not t.empty], ignore_index=True) if not (df.empty and ig.empty) else df
        mv = {}
        for r in all_rows.to_dict("records") if not all_rows.empty else []:
            m = MarketVideo(run_id=run.id, platform=r.get("platform", "youtube"), thumbnail_url=r.get("thumbnail_url")
                            if isinstance(r.get("thumbnail_url"), str) else None,
                            video_id=r["video_id"], url=r["url"], title=r["title"], channel_id=r["channel_id"],
                            channel_title=r["channel_title"], published_at=r["published_at"],
                            duration_s=int(r["duration_s"]), views=int(r["views"]), likes=int(r["likes"]),
                            comments=int(r["comments"]), baseline=float(r.get("baseline") or 0),
                            outlier_score=float(r.get("outlier_score") or 0),
                            market_score=float(r.get("market_score") or 0), keywords=r["keywords"],
                            relevant=bool(r["relevant"]), selected=r["video_id"] in selected)
            mv[r["video_id"]] = m
        save(*mv.values())

        # 4. analysis
        say(f"Market research 4/6: AI analyses the top {len(selected)} videos "
                       f"({len(ig_top)} Instagram Reels from caption/hashtags)…")
        app_desc = f"{b.app_name} — {b.one_liner}"
        calls = ok = 0
        analyses = []
        for i, r in enumerate(top.to_dict("records") if not top.empty else []):
            a, model, called = analyze.analyze_video(r, app_desc, app_id, mode)
            calls += int(called)
            if a is None:
                continue
            ok += 1
            analyses.append(MarketAnalysis(market_video_id=mv[r["video_id"]].id, analysis=a, model=model))
            if (i + 1) % 5 == 0:
                say(f"  AI analysed {i + 1}/{len(top)} videos")
        save(*analyses)
        say(f"  {ok} videos analysed ({calls} new Gemini calls, {ok - calls} from cache)")

        # 5. patterns
        say("Market research 5/6: pattern statistics…")
        kw_df, V, A = run_frames(run.id)
        tables = patterns.compute(A)
        prow = []
        for t in tables.values():
            for r in t.to_dict("records"):
                prow.append(MarketPattern(run_id=run.id, dimension=r["dimension"], value=str(r["value"]),
                                          videos=int(r["videos"]), share=float(r["share"]),
                                          weighted_share=float(r["weighted_share"]),
                                          avg_outlier=float(r["avg_outlier"]), lift=float(r["lift"]),
                                          signal=r["signal"], examples=r["examples"]))
        save(*prow)

        # 6. playbook
        say("Market research 6/6: Market Playbook…")
        if A.empty:
            raise RuntimeError("no analysed relevant videos — widen keywords or check API keys")
        pb_json, narrative = playbook.build(b.app_name, b.one_liner, tables, A, kw_df, V, app_id)
        pid = {(p.dimension, p.value): p.id for p in prow}
        for rec in pb_json["recipes"]:
            rec["pattern_ids"] = [pid[k] for k in (("format", rec["format"]), ("hook_type", rec["hook_type"]),
                                                   ("language", rec["language"]),
                                                   ("closest_creator_style", rec["creator_style"])) if k in pid]
        pb = MarketPlaybook(app_id=app_id, run_id=run.id, playbook=pb_json, narrative=narrative)
        save(pb)
        run.status, run.quota_used, run.gemini_calls = "done", yt.used, calls
        run.notes = (f"{len(kw_rows)} keywords, {len(df)} YouTube + {len(ig)} Instagram candidates, {ok} analysed"
                     + (" · widened for a niche topic" if widened else "") + (" · quota budget hit" if stopped else ""))
        prg.step(app_id, "research", "done", f"{len(kw_rows)} keywords · {len(df)} YouTube + {len(ig)} Instagram "
                                            f"videos · {ok} analysed · playbook ready")
        return pb
    except Exception as e:
        run.status, run.notes = "failed", str(e)[:500]
        run.quota_used = locals().get("yt").used if locals().get("yt") is not None else run.quota_used
        raise
    finally:
        run.finished_at = datetime.now(timezone.utc)
        save(run)
        cost.log_step("research:run", mode, time.time() - t0, app_id=app_id)
        say(f"Market research {run.status}: quota used {run.quota_used} units, "
                       f"{run.gemini_calls} Gemini video calls, {time.time() - t0:.0f}s")


def purge_stale(days: Optional[int] = None) -> dict[str, int]:
    """Delete stored YouTube API data (and its cache) older than `stale_days` (YouTube API Services policy)."""
    days = int(days if days is not None else cfg()["stale_days"])
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with session() as s:
        old = [v for v in s.exec(select(MarketVideo)).all() if _aware(v.fetched_at) < cutoff]
        ids = [v.id for v in old]
        n_an = 0
        if ids:
            n_an = len(s.exec(select(MarketAnalysis.id).where(MarketAnalysis.market_video_id.in_(ids))).all())
            s.exec(delete(MarketAnalysis).where(MarketAnalysis.market_video_id.in_(ids)))
            s.exec(delete(MarketVideo).where(MarketVideo.id.in_(ids)))
        s.commit()
    n_files = 0
    for ns in ("youtube", "market_analysis"):
        d = CACHE_DIR / ns
        if not d.exists():
            continue
        for f in d.rglob("*.json"):
            if datetime.fromtimestamp(f.stat().st_mtime, timezone.utc) < cutoff:
                f.unlink()
                n_files += 1
    return {"videos": len(ids), "analyses": n_an, "cache_files": n_files}


def _apify_usd(run: ResearchRun) -> float:
    from engine.db import Ledger
    from engine.settings import env_float

    end = _aware(run.finished_at) if run.finished_at else datetime.now(timezone.utc)
    with session() as s:
        rows = s.exec(select(Ledger).where(Ledger.app_id == run.app_id, Ledger.step.startswith("apify:"))).all()
    return sum(r.cost_inr for r in rows if _aware(run.started_at) <= _aware(r.created_at) <= end) / env_float("USD_INR", 88.0)


def summary(run_id: int) -> dict[str, Any]:
    with session() as s:
        run = s.get(ResearchRun, run_id)
    kw, V, A = run_frames(run_id)
    return {"run_id": run_id, "status": run.status if run else "?", "source": run.source if run else "?",
            "keywords": len(kw), "candidates": len(V),
            "instagram": int((V["platform"] == "instagram").sum()) if not V.empty else 0,
            "apify_usd": round(_apify_usd(run), 2) if run else 0.0, "relevant": int(V["relevant"].sum()) if not V.empty else 0,
            "analysed": len(A), "quota_used": run.quota_used if run else 0,
            "gemini_calls": run.gemini_calls if run else 0}
