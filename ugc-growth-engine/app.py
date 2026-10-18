"""Streamlit dashboard. Run: streamlit run app.py"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlmodel import select

from engine import (compliance, cost, db, insights_perf, llm, loop, metrics, optimizer, progress, render,
                    simulator, tracking, understand, variations)
from engine.brief import Brief, brief_of, load_brief, register_app, sample_briefs
from engine.locks import is_busy
from engine.settings import DATA_DIR, creator_styles, env, env_float, env_int, languages

st.set_page_config(page_title="AI UGC Growth Engine · India", page_icon="🎬", layout="wide")

SIM_BADGE = "🧪 SIMULATED"


# ---------------------------------------------------------------- helpers

def pct(x: Any, digits: int = 1) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{digits}f}%"


def num(x: Any, digits: int = 0) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:,.{digits}f}"


def app_id() -> Optional[str]:
    return st.session_state.get("app_id")


def require_app() -> Optional[db.App]:
    aid = app_id()
    a = db.get_app(aid) if aid else None
    if a is None:
        st.info("Pick an app in the sidebar, or create one on the **Brief** page.")
    return a


def sim_banner(sources: list[str] | set[str]) -> None:
    if "simulated" in sources:
        st.warning(f"{SIM_BADGE} — numbers below come from the simulated test market, not real audiences.")


def title_with_source(title: str, sources: Any) -> str:
    return f"{title} · {SIM_BADGE}" if "simulated" in set(sources) else title


def deliverables(aid: str) -> dict[str, tuple[bool, str]]:
    rows = db.creatives(aid)
    vids = [c for c in rows if c.status == "rendered"]
    langs = {c.language for c in rows if c.script}
    df = metrics.metrics_frame(aid)
    ins = insights_perf.latest(aid)
    gens = {c.generation for c in rows}
    children = [c for c in rows if c.parent_creative_id]
    ledger = cost.summary(aid)
    d = {
        "D1 · AI UGC Generator": (len(vids) > 0, f"{len(vids)} videos rendered"),
        "D2 · Indian Localization": (len(langs) >= 2, f"scripts in {', '.join(sorted(langs)) or '—'}"),
        "D3 · Variations from one brief": (len(rows) >= 50, f"{len(rows)} variations, "
                                           f"{sum(1 for c in rows if c.script)} scripted"),
        "D4 · Performance Dashboard": (not df.empty, f"{len(df)} creatives with metrics"),
        "D5 · AI Insights": (bool(ins and ins.findings), "insights computed" if ins and ins.findings else "—"),
        "D6 · Growth Loop": (len(gens) >= 2 and len(children) > 0, f"{len(gens)} generations, {len(children)} child creatives"),
    }
    d["D7 · Working Prototype"] = (all(v[0] for v in d.values()) and ledger["videos_rendered"] > 0,
                                   f"end-to-end in {ledger['total_seconds'] / 60:.1f} min of compute")
    from engine.research import run as research

    pb = research.latest_playbook(aid)
    if pb:
        sm = research.summary(pb.run_id)
        label = f"{sm['keywords']} keywords · {sm['candidates']} Shorts · {sm['analysed']} analysed"
        d["D8 · Market Research"] = (True, label + (" (MOCK data)" if sm["source"] == "mock" else ""))
    else:
        d["D8 · Market Research"] = (False, "no research yet")
    return d


# ---------------------------------------------------------------- background runs + live stepper

ICONS = {"done": "✅", "running": "🔄", "pending": "⏳", "failed": "❌", "skipped": "⏭️"}
RUN_LOGS = Path(__file__).resolve().parent / "cache" / "progress"


def launch(app_id_: str, args: list[str]) -> bool:
    """Start a pipeline command in the background (survives page changes). False if one is already running."""
    import subprocess
    import sys

    if progress.is_running(app_id_) or is_busy(app_id_):
        st.warning("A run is already in progress for this app — see its live progress below.")
        return False
    RUN_LOGS.mkdir(parents=True, exist_ok=True)
    log_f = open(RUN_LOGS / f"{app_id_}.log", "w")  # noqa: SIM115 — handed to the child process
    subprocess.Popen([sys.executable, "cli.py", *args], cwd=str(Path(__file__).resolve().parent),
                     stdout=log_f, stderr=subprocess.STDOUT, start_new_session=True)
    import time as _t

    for _ in range(20):  # wait until the child has registered its run (≤ 5 s)
        if progress.is_running(app_id_):
            break
        _t.sleep(0.25)
    return True


def stop_run(app_id_: str) -> None:
    import os
    import signal

    stt = progress.read(app_id_) or {}
    try:
        os.killpg(os.getpgid(int(stt["pid"])), signal.SIGTERM)
    except (OSError, KeyError, ValueError):
        pass


def _fmt_secs(x: float) -> str:
    x = max(0, int(x))
    return f"{x // 60}m {x % 60:02d}s" if x >= 60 else f"{x}s"


def _stepper(app_id_: str) -> None:
    import time as _t

    stt = progress.read(app_id_)
    if not stt:
        return
    live = progress.is_running(app_id_)
    steps = stt["steps"]
    done = sum(1 for x in steps if x["status"] in ("done", "skipped"))
    running = next((x for x in steps if x["status"] == "running"), None)
    now = _t.time()
    if live:
        head = f"🔄 **{stt['title']}** — step {min(done + 1, len(steps))} of {len(steps)}"
    elif stt.get("finished") and stt.get("ok"):
        head = f"✅ **{stt['title']}** — finished in {_fmt_secs(stt['finished'] - stt['started'])}"
    elif stt.get("finished"):
        head = f"❌ **{stt['title']}** — failed: {stt.get('message', '')[:200]}"
    else:
        head = f"⏹️ **{stt['title']}** — stopped (the process ended before finishing)"
    st.markdown(head)
    frac = done / len(steps) if steps else 0
    if running and running.get("started"):
        frac = min(1.0, frac + 0.5 / len(steps))
    st.progress(frac, text=f"{done}/{len(steps)} steps · {_fmt_secs((stt.get('finished') or now) - stt['started'])} elapsed")
    rows = []
    for x in steps:
        dur = ""
        if x.get("started"):
            dur = _fmt_secs((x.get("ended") or now) - x["started"])
        icon = ICONS.get(x["status"], "•")
        if x["status"] == "running" and not live:
            icon = "⏹️"
        style = "font-weight:600" if x["status"] == "running" else ("opacity:0.55" if x["status"] == "pending" else "")
        rows.append(f"<div style='display:flex;gap:10px;padding:5px 0;border-bottom:1px solid rgba(128,128,128,0.15);{style}'>"
                    f"<div style='width:22px'>{icon}</div><div style='flex:1'>{x['label']}"
                    + (f"<div style='font-size:0.85em;opacity:0.75;font-weight:400'>{x['detail']}</div>" if x.get("detail") else "")
                    + f"</div><div style='width:70px;text-align:right;opacity:0.6;font-size:0.85em'>{dur}</div></div>")
    st.markdown("".join(rows), unsafe_allow_html=True)
    if stt.get("log"):
        with st.expander("Activity log"):
            st.code("\n".join(stt["log"][-25:]), language=None)
    if live and st.button("⏹ Stop this run", key=f"stop_{app_id_}"):
        stop_run(app_id_)
        st.rerun()


@st.fragment(run_every="2s")
def _live_stepper(app_id_: str) -> None:
    _stepper(app_id_)
    if not progress.is_running(app_id_):
        st.rerun()  # run ended: refresh the whole page once so results appear


def progress_panel(app_id_: Optional[str]) -> None:
    """Live step-by-step loader while a run is going; last run's summary otherwise."""
    if not app_id_ or not progress.read(app_id_):
        return
    with st.container(border=True):
        if progress.is_running(app_id_):
            _live_stepper(app_id_)
        else:
            _stepper(app_id_)


# ---------------------------------------------------------------- sidebar

def sidebar() -> None:
    st.sidebar.markdown("<div class='brand'>● <span>UGC Growth Engine</span></div>", unsafe_allow_html=True)
    apps = db.list_apps()
    NEW = "__new__"
    ids = [a.id for a in apps]
    names = {a.id: a.name for a in apps}
    qp = st.query_params.get("app")
    if qp in ids and "app_id" not in st.session_state:
        st.session_state["app_id"] = qp  # a refreshed page or shared link keeps its app
    cur = app_id() if app_id() in ids else (ids[0] if ids else NEW)
    pick = st.sidebar.selectbox(
        "Working on app", ids + [NEW], index=(ids + [NEW]).index(cur),
        format_func=lambda i: "➕ Add a new app…" if i == NEW else names[i],
        help="Pick one of your registered apps. To add an app, choose “➕ Add a new app…” (Brief page).")
    if pick == NEW:
        if st.session_state.get("_nav_new") != True:
            st.session_state["_nav_new"] = True
            st.session_state["flow_new"] = True
            st.switch_page(st.session_state["_pages"]["engine"])
    else:
        st.session_state["_nav_new"] = False
        if "app_id" in st.session_state and st.session_state["app_id"] != pick:
            st.session_state["flow_open"] = True   # picked another app in the sidebar: open it
        st.session_state["app_id"] = pick
        if st.session_state.get("flow_open") and st.query_params.get("app") != pick:
            st.query_params["app"] = pick
    prov = llm.active_provider()
    if prov == "mock":
        st.sidebar.warning("LLM: **mock** (offline templates). Add a free GEMINI_API_KEY in `.env` for real copy.")
    elif prov == "none":
        st.sidebar.error("No LLM provider. Add GEMINI_API_KEY or GROQ_API_KEY to `.env`.")
    elif (llm.get_status(prov) or {}).get("ok") is False:
        st.sidebar.error(f"LLM: **{prov}** — {llm.get_status(prov)['message']}")
    stock = "Pexels" if env("PEXELS_API_KEY") else ("Pixabay" if env("PIXABAY_API_KEY") else "gradients")
    from engine import cost as _cost

    spent, cap = _cost.spent_today_inr(), env_float("GEMINI_DAILY_BUDGET_INR", 500)
    over = " <b style='color:#F87171'>· cap reached</b>" if spent >= cap else ""
    model = llm.gemini_model() if prov == "gemini" else prov
    st.sidebar.markdown(f"<div class='sbstat'>AI <b>{model}</b><br>Stock <b>{stock}</b><br>"
                        f"Gemini today <b>₹{spent:.0f}</b> / ₹{cap:.0f}{over}</div>", unsafe_allow_html=True)
    if app_id() and progress.is_running(app_id()):
        stt = progress.read(app_id()) or {}
        steps = stt.get("steps", [])
        cur = next((x for x in steps if x["status"] == "running"), None)
        n = sum(1 for x in steps if x["status"] in ("done", "skipped"))
        st.sidebar.info(f"🔄 **Running:** {stt.get('title', '')}  \nStep {min(n + 1, len(steps))}/{len(steps)}: "
                        f"{cur['label'].split(' (')[0] if cur else '…'}  \n_Live progress on Home._")
    elif app_id() and is_busy(app_id()):
        st.sidebar.info("⏳ A run is in progress for this app (started elsewhere).")
    if app_id():
        with st.sidebar.expander("Reset this app's data"):
            if st.checkbox("Yes, delete all generated data for this app"):
                if st.button("Reset", type="primary"):
                    db.reset_app(app_id())
                    st.rerun()


# ---------------------------------------------------------------- pages

def page_home() -> None:
    st.title("AI UGC & Social Growth Engine 🇮🇳")
    st.markdown("> One app brief → a continuous pipeline of India-specific UGC experiments, measured and improved "
                "every generation — **at near-zero cost per creative.**")
    aid = app_id()
    col1, col2 = st.columns([3, 2])
    with col1:
        st.subheader("Deliverables")
        if aid:
            for name, (ok, detail) in deliverables(aid).items():
                st.markdown(f"{'✅' if ok else '⬜️'} **{name}** — {detail}")
        else:
            st.caption("No app yet.")
    with col2:
        st.subheader("Run Full Loop (Demo)")
        samples = sample_briefs()
        pick = st.selectbox("Sample brief", list(samples), format_func=lambda k: f"{k} ({samples[k].name})")
        n = st.slider("Videos per generation", 2, 12, 6)
        fresh = st.checkbox("Start fresh (clear old videos/generations; keeps market research)", value=False)
        running = bool(aid) and progress.is_running(aid)
        if st.button("▶ Run Full Loop (Demo)", type="primary", use_container_width=True, disabled=running):
            b = load_brief(samples[pick])
            if launch(b.slug(), ["demo", "--brief", str(samples[pick]), "--render-count", str(n)]
                      + (["--fresh"] if fresh else [])):
                st.session_state["app_id"] = b.slug()
                st.rerun()
        st.caption("Runs in the background — you can switch pages; progress stays live here and in the sidebar.")
    progress_panel(app_id())
    if aid:
        st.divider()
        s = cost.summary(aid)
        c = st.columns(5)
        c[0].metric("Generations", db.current_generation(aid))
        c[1].metric("Variations", s["creatives_total"])
        c[2].metric("Videos", s["videos_rendered"])
        c[3].metric("Cost per creative", f"₹{s['cost_per_creative_inr']:.2f}", help="Actual API spend ÷ videos")
        c[4].metric("Time per video", f"{s['seconds_per_video']:.0f}s" if s["seconds_per_video"] else "—")


def page_brief() -> None:
    st.title("App brief")
    st.caption("Add a new app here: load a sample brief or fill the form, then **Save & register app**. "
               "It then appears in the sidebar's app list.")
    samples = sample_briefs()
    src = st.radio("Start from", ["Sample brief", "Blank"], horizontal=True)
    base: dict[str, Any] = {}
    if src == "Sample brief":
        k = st.selectbox("Sample", list(samples))
        base = json.loads(samples[k].read_text(encoding="utf-8"))
    elif app_id() and db.get_app(app_id()):
        base = db.get_app(app_id()).brief
    brief_editor(base, "brief")
    a = db.get_app(app_id()) if app_id() else None
    if a and a.screenshots:
        st.caption("Screenshots used in videos")
        st.image([s for s in a.screenshots if Path(s).exists()][:6], width=140)


def brief_editor(base: dict[str, Any], key: str) -> Optional[db.App]:
    """The app brief form. Saving registers (or updates) the app and makes it the current app."""
    with st.form(key):
        c1, c2 = st.columns(2)
        name = c1.text_input("App name", base.get("app_name", ""))
        category = c2.text_input("Category", base.get("category", ""))
        one = st.text_input("One-liner", base.get("one_liner", ""))
        desc = st.text_area("Description", base.get("description", ""), height=100)
        feats = st.text_area("Key features (one per line)", "\n".join(base.get("key_features", [])), height=110)
        usp = st.text_input("USP", base.get("usp", ""))
        aud = st.text_input("Target audience", base.get("target_audience", ""))
        c3, c4, c5 = st.columns(3)
        tiers = c3.multiselect("City tiers", ["1", "2", "3"], base.get("city_tiers", ["1", "2", "3"]))
        langs = c4.multiselect("Languages", list(languages()), base.get("languages", ["hinglish", "hi", "ta"]),
                               format_func=lambda l: languages()[l]["name"])
        tones = ["fun", "trustworthy", "premium", "desi-casual"]
        tone = c5.selectbox("Brand tone", tones, index=tones.index(base.get("brand_tone", "desi-casual")))
        cta = st.text_input("CTA", base.get("cta", "Download free on Play Store"))
        c6, c7 = st.columns(2)
        pkg = c6.text_input("Play Store package id", base.get("play_store_id", ""))
        url = c7.text_input("Play Store URL (optional — enriches with reviews + screenshots)", base.get("play_store_url", ""))
        avoid = st.text_area("Claims to avoid (one per line)", "\n".join(base.get("claims_to_avoid", [])))
        shots = st.file_uploader("App screenshots (optional)", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
        submitted = st.form_submit_button("Save & register app", type="primary")
    if submitted:
        b = Brief(app_id=base.get("app_id", ""), app_name=name, category=category, one_liner=one, description=desc,
                  key_features=[f.strip() for f in feats.splitlines() if f.strip()], usp=usp, target_audience=aud,
                  city_tiers=tiers, languages=langs, brand_tone=tone, cta=cta, play_store_id=pkg, play_store_url=url,
                  claims_to_avoid=[a.strip() for a in avoid.splitlines() if a.strip()])
        if shots:
            d = DATA_DIR / "apps" / b.slug()
            d.mkdir(parents=True, exist_ok=True)
            for old in d.glob("screenshot_*"):
                old.unlink()
            for i, f in enumerate(shots):
                (d / f"screenshot_{i}{Path(f.name).suffix.lower()}").write_bytes(f.getvalue())
        with st.spinner("Registering (Play Store enrichment if URL given)…"):
            a = register_app(b)
        st.session_state["app_id"] = a.id
        st.success(f"Saved {a.name} ({len(a.brief.get('reviews', []))} reviews, {len(a.screenshots)} screenshots)")
        return a
    return None


def page_research() -> None:
    from engine.research import run as research, youtube_mode
    from engine.research.analyze import allowed

    st.title("Market research — what's working now")
    a = require_app()
    if not a:
        return
    mode = youtube_mode()
    if mode == "none":
        st.warning("Add a free **YOUTUBE_API_KEY** to `.env` (Google Cloud → enable YouTube Data API v3 → API key) "
                   "and a **GEMINI_API_KEY** so the engine can watch the top videos.")
    c1, c2, c3 = st.columns([2, 1, 1])
    force = c2.checkbox("Ignore cache (< 7 days)", value=False)
    if c1.button("🔎 Refresh research", type="primary", disabled=mode == "none" or progress.is_running(a.id)):
        if launch(a.id, ["research", "--app-id", a.id] + (["--force"] if force else [])):
            st.rerun()
    progress_panel(a.id)
    if c3.button("Purge data > 30 days"):
        st.info(research.purge_stale())
    pb = research.latest_playbook(a.id)
    if pb is None:
        st.caption("No research yet.")
        return
    sm = research.summary(pb.run_id)
    if sm["source"] == "mock":
        st.warning("🧪 MOCK research data (offline dry run) — add YOUTUBE_API_KEY + GEMINI_API_KEY for real market data.")
    k = st.columns(4) + st.columns(4)
    k[0].metric("Demand keywords", sm["keywords"])
    k[1].metric("Candidate videos", sm["candidates"], help=f"incl. {sm['instagram']} Instagram Reels")
    k[2].metric("Relevant", sm["relevant"])
    k[3].metric("Analysed by AI", sm["analysed"])
    k[4].metric("YouTube quota used", f"{sm['quota_used']:,}", help="Free budget: 10,000 units/day")
    k[5].metric("Gemini video calls", sm["gemini_calls"], help="Cached videos cost 0 calls")
    k[6].metric("Instagram Reels", sm["instagram"])
    k[7].metric("Apify spend (run)", f"${sm['apify_usd']:.2f}")
    st.caption(f"Run #{pb.run_id} · {pb.created_at:%d %b %Y %H:%M} UTC")

    tabs = st.tabs(["📘 Playbook", "🔑 Demand keywords", "🎥 Top videos", "📊 Patterns", "🧩 Combinations & angles"])
    kw, V, A = research.run_frames(pb.run_id)
    with tabs[0]:
        st.markdown(pb.narrative)
    with tabs[1]:
        if not kw.empty:
            st.dataframe(kw.sort_values("demand_score", ascending=False)[
                ["text", "language", "source", "autocomplete_hits", "trends_interest", "top10_views", "searched",
                 "demand_score"]], hide_index=True, use_container_width=True,
                column_config={"demand_score": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")})
    with tabs[2]:
        if not A.empty:
            A2 = A.sort_values("market_score", ascending=False)
            for row in range(0, len(A2), 4):
                cols = st.columns(4)
                for col, r in zip(cols, A2.iloc[row:row + 4].itertuples()):
                    with col:
                        if r.platform == "youtube":
                            st.image(f"https://i.ytimg.com/vi/{r.video_id}/hqdefault.jpg")
                        elif r.platform == "instagram" and isinstance(r.thumbnail_url, str):
                            try:
                                st.image(r.thumbnail_url)
                            except Exception:  # noqa: BLE001 — Instagram CDN links expire
                                pass
                        st.caption(("📸 Instagram Reel · analysed from caption" if r.platform == "instagram"
                                    else "▶️ YouTube Short · watched by Gemini"))
                        st.markdown(f"**[{r.title[:70]}]({r.url})**  \n{r.channel_title}  \n"
                                    f"{r.views:,} views · **{r.outlier_score:.1f}×** baseline · score {r.market_score:.2f}")
                        st.caption(f"`{r.format}` · `{r.hook_type}` · `{r.language}` · `{r.closest_creator_style}`  \n"
                                   f"{r.topic_angle}")
    with tabs[3]:
        pat = research.market_patterns(a.id)
        dims = [d for d in ["format", "hook_type", "language", "closest_creator_style", "production_style",
                            "emotional_trigger", "cta_type", "length_bucket"] if d in set(pat["dimension"])]
        for i in range(0, len(dims), 2):
            cols = st.columns(2)
            for col, d in zip(cols, dims[i:i + 2]):
                t = pat[pat["dimension"] == d].sort_values("weighted_share", ascending=False)
                fig = px.bar(t, x="value", y="weighted_share", color="signal", text=t["avg_outlier"].map(lambda x: f"{x:.1f}×"),
                             color_discrete_map={"pattern": "#3b82f6", "weak signal": "#9ca3af"},
                             hover_data={"videos": True, "share": ":.0%", "lift": ":+.0%", "avg_outlier": ":.1f"},
                             title=f"{d}: weighted share (label = avg outlier ×)")
                fig.update_layout(height=320, yaxis_tickformat=".0%", margin=dict(t=50, b=10), legend_title=None)
                col.plotly_chart(fig, use_container_width=True)
        st.dataframe(pat[pat["dimension"].isin(dims)][["dimension", "value", "videos", "share", "weighted_share",
                                                        "avg_outlier", "lift", "signal"]],
                     hide_index=True, use_container_width=True,
                     column_config={"share": st.column_config.NumberColumn(format="percent"),
                                    "weighted_share": st.column_config.NumberColumn(format="percent"),
                                    "lift": st.column_config.NumberColumn(format="percent"),
                                    "avg_outlier": st.column_config.NumberColumn(format="%.1f×")})
        st.caption(f"Tags use our own taxonomy ({len(allowed()['format']) - 1} formats, "
                   f"{len(allowed()['hook_type']) - 1} hook types), so market patterns map 1:1 to generation tags. "
                   "“weak signal” = fewer than 3 videos.")
    with tabs[4]:
        pat = research.market_patterns(a.id)
        for d, label in (("format × hook_type × language", "Winning combinations"), ("topic_angle", "Topic angles")):
            st.markdown(f"**{label}**")
            t = pat[pat["dimension"] == d]
            st.dataframe(t[["value", "videos", "weighted_share", "avg_outlier", "lift", "signal", "examples"]],
                         hide_index=True, use_container_width=True,
                         column_config={"weighted_share": st.column_config.NumberColumn(format="percent"),
                                        "lift": st.column_config.NumberColumn(format="percent")})
        with db.session() as s:
            cons = list(s.exec(select(db.Concept).where(db.Concept.app_id == a.id, db.Concept.generation == 1)).all())
        if cons:
            n = sum(1 for c in cons if c.market_evidence)
            st.markdown(f"**Gen 1 concepts linked to market evidence: {n}/{len(cons)}**")
            st.dataframe(pd.DataFrame([{"concept": c.title, "format": c.format, "market_evidence": c.market_evidence}
                                       for c in cons]), hide_index=True, use_container_width=True)


def page_understand() -> None:
    st.title("Understand the app")
    a = require_app()
    if not a:
        return
    if st.button("Run understanding", type="primary"):
        with st.spinner("Reading brief + reviews…"):
            try:
                understand.run(a.id)
            except llm.LLMError as e:
                st.error(str(e))
    u = db.latest_understanding(a.id)
    if not u:
        st.caption("Not run yet.")
        return
    st.subheader("Personas")
    cols = st.columns(len(u.personas) or 1)
    for c, p in zip(cols, u.personas):
        c.markdown(f"**{p['name']}**  \n{p.get('age_range', '')} · tier {p.get('city_tier', '')}  \n"
                   f"🎯 {p.get('goal', '')}  \n😤 {p.get('frustration', '')}")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Pain points")
        for p in u.pain_points:
            st.markdown(f"- **{p['pain']}**" + (f"  \n  _{' · '.join(p.get('evidence_phrases', [])[:3])}_"
                                                 if p.get("evidence_phrases") else ""))
        st.subheader("Real user phrases")
        for ph in u.user_phrases:
            st.markdown(f"> {ph}")
    with c2:
        st.subheader("Key messages")
        for m in u.key_messages:
            st.markdown(f"- `{m['id']}` {m['message']}")
        st.subheader("India moments (this + next month)")
        for m in u.india_moments:
            st.markdown(f"- **{m['moment']}** — {m.get('how_app_fits', '')}")
        st.subheader("Loved features")
        st.write(", ".join(u.loved_features))


def creatives_df(aid: str, gen: Optional[int]) -> pd.DataFrame:
    rows = db.creatives(aid, gen)
    return pd.DataFrame([{
        "id": c.id, "gen": c.generation, "origin": c.origin, "language": c.language, "hook_type": c.hook_type,
        "format": c.format, "style": c.creator_style, "message": c.message, "persona": c.persona,
        "length": c.length_bucket, "cta": c.cta_type, "score": c.predicted_score, "compliance": c.compliance_status,
        "status": c.status, "render": c.selected_for_render, "hook": c.hook,
        "issues": "; ".join(c.compliance_issues or []),
    } for c in rows])


def page_generate() -> None:
    st.title("Generate variations")
    a = require_app()
    if not a:
        return
    b = brief_of(a)
    if db.latest_understanding(a.id) is None:
        st.caption("The ideas run does market research (cached weekly) and understanding automatically first.")
    with st.expander("💡 Generate video ideas — settings", expanded=db.current_generation(a.id) == 0):
        c1, c2 = st.columns(2)
        langs = c1.multiselect("Languages", list(languages()), [l for l in b.languages if l in languages()],
                               format_func=lambda l: languages()[l]["name"])
        styles = c2.multiselect("Creator styles", list(creator_styles()), variations.default_styles(b.brand_tone),
                                format_func=lambda s: creator_styles()[s]["label"])
        c3, c4, c5, c6 = st.columns(4)
        n_con = c3.number_input("Concepts", 3, 20, 10)
        hpc = c4.number_input("Hooks per concept", 2, 8, 5)
        scripted = c5.number_input("Scripts to write", 6, 120, env_int("SCRIPTED_COUNT", 60))
        rc = c6.number_input("Videos to render", 2, 30, env_int("GEN1_RENDER_COUNT", 12))
        st.caption(f"Matrix: {n_con} concepts × {hpc} hooks × {len(langs)} languages × {len(styles)} styles = "
                   f"**{n_con * hpc * len(langs) * len(styles)} possible creatives**")
        has = db.current_generation(a.id) > 0
        fresh_ideas = st.checkbox("Replace existing ideas (clears generations; keeps market research)", value=False,
                                  disabled=not has)
        if st.button("💡 Generate video ideas (no videos yet)", type="primary",
                     disabled=(has and not fresh_ideas) or progress.is_running(a.id)):
            args = ["generate", a.id, "--hooks-per-concept", str(hpc), "--scripted", str(scripted),
                    "--render-count", str(rc), "--languages", ",".join(langs), "--styles", ",".join(styles)]
            if launch(a.id, args + (["--fresh"] if fresh_ideas else [])):
                st.rerun()
    progress_panel(a.id)
    gens = sorted({c.generation for c in db.creatives(a.id)})
    if not gens:
        return
    gen = st.selectbox("Generation", gens, index=len(gens) - 1)
    mc = variations.matrix_counts(a.id, gen)
    c = st.columns(5)
    c[0].metric("Variation matrix", mc["matrix"])
    c[1].metric("Scripted", mc["scripted"])
    c[2].metric("Selected to render", mc["selected"])
    c[3].metric("Rendered", mc["rendered"])
    c[4].metric("Compliance flags", mc["flagged"])
    from engine import modes as vmodes

    rows_g = db.creatives(a.id, gen)
    ms = vmodes.summary(a.id, [x for x in rows_g if x.selected_for_render] or rows_g)
    st.markdown(f"**Video modes** · target from *{ms['source']}*: " + " · ".join(
        f"{m.replace('_', ' ')} {ms['target'][m]:.0%} (picked {ms['counts'].get(m, 0)})" for m in vmodes.MODES)
        + f" · est. {ms['credits_estimate']:,} Magnific credits")
    df = creatives_df(a.id, gen)
    show_all = st.toggle("Show hook-only rows (not scripted)", value=False)
    view = df if show_all else df[df["status"] != "hook_only"]
    st.dataframe(view, use_container_width=True, hide_index=True, height=360,
                 column_config={"score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%.0f")})

    st.subheader("Review a creative")
    ids = view["id"].tolist()
    if not ids:
        return
    cid = st.selectbox("Creative", ids, format_func=lambda i: f"#{i} · {df.set_index('id').loc[i, 'hook'][:70]}")
    with db.session() as s:
        cr = s.get(db.Creative, cid)
    if cr.compliance_issues:
        st.error("Compliance issues: " + "; ".join(cr.compliance_issues))
    if cr.script:
        scenes = pd.DataFrame([{"voiceover": s["voiceover"], "on_screen_text": s.get("on_screen_text", ""),
                                "visual": s["visual"].get("type"), "query_en": s["visual"].get("query_en", ""),
                                "est_seconds": s.get("est_seconds", 4)} for s in cr.script["scenes"]])
        edited = st.data_editor(scenes, use_container_width=True, hide_index=True, key=f"ed{cid}")
        st.caption(f"Post caption: {cr.post_caption}  \n{' '.join(cr.hashtags or [])}  \n"
                   f"Tracking link: {tracking.link_for(cr) if cr.tracking_slug else '—'}")
        b1, b2, b3, b4 = st.columns(4)
        if b1.button("💾 Save edits"):
            sc = cr.script
            for i, row in edited.iterrows():
                if i < len(sc["scenes"]):
                    sc["scenes"][i].update({"voiceover": row["voiceover"], "on_screen_text": row["on_screen_text"],
                                            "est_seconds": float(row["est_seconds"])})
                    sc["scenes"][i]["visual"].update({"type": row["visual"], "query_en": row["query_en"]})
            cr.script = dict(sc)
            cr.hook = sc["scenes"][0]["voiceover"]
            db.save(cr)
            compliance.check_creatives([cr], b, use_llm=False)
            st.rerun()
        if b2.button("✅ Approve"):
            cr.compliance_status, cr.status = "approved", "approved"
            db.save(cr)
            st.rerun()
        if b3.button("🚫 Reject"):
            cr.compliance_status, cr.status, cr.selected_for_render = "rejected", "rejected", False
            db.save(cr)
            st.rerun()
        if b4.button("🎬 Toggle render selection"):
            cr.selected_for_render = not cr.selected_for_render
            if cr.selected_for_render and cr.status == "draft":
                cr.status = "approved"
            db.save(cr)
            st.rerun()

    st.subheader("Same concept, different languages")
    concept_ids = sorted({c.concept_id for c in db.creatives(a.id, gen) if c.script and c.concept_id})
    if concept_ids:
        with db.session() as s:
            cons = {c.id: c for c in s.exec(select(db.Concept).where(db.Concept.id.in_(concept_ids))).all()}
        k = st.selectbox("Concept", concept_ids, format_func=lambda i: cons[i].title if i in cons else str(i))
        if k in cons:
            st.caption(f"Scenario: {cons[k].india_scenario} · persona: {cons[k].persona} · format: {cons[k].format}")
        same = [c for c in db.creatives(a.id, gen) if c.concept_id == k and c.script]
        cols = st.columns(max(1, len({c.language for c in same})))
        for col, lang in zip(cols, sorted({c.language for c in same})):
            ex = next(c for c in same if c.language == lang)
            col.markdown(f"**{languages()[lang]['name']}** · {ex.creator_style}")
            col.markdown("\n".join(f"- {s['voiceover']}" for s in ex.script["scenes"]))


def _zip_videos(aid: str, gen: int) -> tuple[bytes, int]:
    import io
    import zipfile

    buf = io.BytesIO()
    n = 0
    with db.session() as ss, zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for c in db.creatives(aid, gen):
            v = ss.exec(select(db.Video).where(db.Video.creative_id == c.id)).first()
            if v and Path(v.path).exists():
                base = f"{aid}_gen{gen}_{c.id}_{c.language}"
                z.write(v.path, f"{base}.mp4")
                cap = Path(v.path).with_name("caption.txt")
                if cap.exists():
                    z.write(cap, f"{base}_caption.txt")
                n += 1
    return buf.getvalue(), n


STATUS_BADGE = {"rendered": "🎬 video ready", "published": "📤 published", "approved": "✅ approved",
                "draft": "📝 script", "failed": "❌ failed", "rejected": "🚫 rejected"}


def page_ideas() -> None:
    st.title("💡 Video ideas")
    a = require_app()
    if not a:
        return
    gens = sorted({c.generation for c in db.creatives(a.id)})
    if not gens:
        st.info("No ideas yet — go to **Generate** and press “💡 Generate video ideas”.")
        progress_panel(a.id)
        return
    c1, c2, c3 = st.columns([1, 2, 2])
    gen = c1.selectbox("Generation", gens, index=len(gens) - 1)
    show = c2.radio("Show", ["With videos", "Picked to film", "All scripted ideas"], horizontal=True)
    rows = [c for c in db.creatives(a.id, gen) if c.script]
    langs = sorted({c.language for c in rows})
    lang_pick = c3.multiselect("Languages", langs, langs, format_func=lambda l: languages()[l]["name"])
    with db.session() as ss:
        vids = {v.creative_id: v for v in ss.exec(select(db.Video)).all()}
        concepts = {c.id: c for c in ss.exec(select(db.Concept).where(db.Concept.app_id == a.id)).all()}
    from engine.research.run import market_patterns

    pat = market_patterns(a.id)
    pat_label = {int(r.id): f"{r.dimension}: {r.value}" for r in pat.itertuples()} if not pat.empty else {}
    u = db.latest_understanding(a.id)
    msg_text = {m["id"]: m["message"] for m in (u.key_messages if u else [])}

    def keep(c) -> bool:  # noqa: ANN001
        if c.language not in lang_pick:
            return False
        if show == "With videos":
            return c.id in vids
        if show == "Picked to film":
            return c.selected_for_render or c.id in vids
        return True

    rows = [c for c in rows if keep(c)]
    k = st.columns(4)
    k[0].metric("Ideas (concepts)", len({c.concept_id for c in rows}))
    k[1].metric("Scripted variations", len(rows))
    k[2].metric("Videos", sum(1 for c in rows if c.id in vids))
    data, n = _zip_videos(a.id, gen)
    k[3].download_button(f"⬇️ Download all {n} videos (.zip)", data, file_name=f"{a.id}_gen{gen}_videos.zip",
                         mime="application/zip", disabled=n == 0, use_container_width=True)
    if not rows:
        st.info("Nothing to show with these filters.")
        return
    by_concept: dict = {}
    for c in rows:
        by_concept.setdefault(c.concept_id, []).append(c)
    for cid, items in sorted(by_concept.items(), key=lambda kv: -max(x.predicted_score for x in kv[1])):
        con = concepts.get(cid)
        with st.container(border=True):
            if con:
                st.markdown(f"### {con.title}")
                st.markdown(f"🇮🇳 **Scenario:** {con.india_scenario}  \n👤 **For:** {con.persona} · "
                            f"🎞️ **Format:** `{con.format}` · 💬 **Message:** {msg_text.get(con.message, con.message)}")
                if con.pain_point:
                    st.caption(f"Pain point: {con.pain_point}")
                if con.market_evidence:
                    st.markdown("📈 **Built on market proof:** " + " ".join(
                        f"`{pat_label.get(i, i)}`" for i in con.market_evidence))
            for c in sorted(items, key=lambda x: -x.predicted_score):
                st.markdown(f"**{languages()[c.language]['name']}** · “{c.hook}”  \n"
                            f"<span style='opacity:0.75'>{STATUS_BADGE.get(c.status, c.status)} · hook: `{c.hook_type}` · "
                            f"style: `{c.creator_style}` · score {c.predicted_score:.0f} · #{c.id}"
                            + (" · ⚠️ " + "; ".join(c.compliance_issues) if c.compliance_status == "flagged" else "")
                            + "</span>", unsafe_allow_html=True)
                cols = st.columns([1, 2]) if c.id in vids else [None, st.container()]
                if c.id in vids and Path(vids[c.id].path).exists():
                    with cols[0]:
                        st.video(vids[c.id].path)
                        st.download_button("⬇️ Download video", Path(vids[c.id].path).read_bytes(),
                                           file_name=f"{a.id}_{c.id}_{c.language}.mp4", mime="video/mp4",
                                           key=f"dlidea{c.id}", use_container_width=True)
                with cols[1]:
                    with st.expander("Script, caption & link", expanded=False):
                        for i, sc in enumerate(c.script["scenes"], 1):
                            vis = sc["visual"]
                            where = "📱 app screen" if vis.get("type") == "screenshot" else f"🎥 {vis.get('query_en', '')}"
                            st.markdown(f"**{i}.** {sc['voiceover']}  \n<span style='opacity:0.65'>on screen: "
                                        f"“{sc.get('on_screen_text', '')}” · {where}</span>", unsafe_allow_html=True)
                        st.markdown(f"**CTA:** {c.script.get('cta', '')}")
                        st.code(f"{c.post_caption}\n{' '.join(c.hashtags or [])}\n{tracking.link_for(c) if c.tracking_slug else ''}",
                                language=None)
                st.divider()


def page_studio() -> None:
    st.title("Studio")
    a = require_app()
    if not a:
        return
    gens = sorted({c.generation for c in db.creatives(a.id)})
    if not gens:
        st.info("Generate creatives first.")
        return
    gen = st.selectbox("Generation", gens, index=len(gens) - 1)
    queue = [c for c in db.creatives(a.id, gen, selected_for_render=True) if c.status in ("approved", "draft", "failed")]
    c1, c2 = st.columns([1, 3])
    c1.metric("In render queue", len(queue))
    if c2.button(f"🎬 Render {len(queue)} selected videos", type="primary", disabled=not queue):
        bar = st.progress(0.0)

        def prog(done: int, total: int, r: dict) -> None:
            bar.progress(done / total, text=f"{done}/{total} · " + ("failed" if "error" in r else f"{r['render_seconds']:.0f}s"))

        res = render.render_selected(a.id, gen, progress=prog)
        st.success(f"Rendered {res['rendered']}/{res['requested']} in {res['wall_seconds']}s")
        for e in res["errors"]:
            st.error(f"#{e['creative_id']}: {e['error'][:300]}")
    rendered = [c for c in db.creatives(a.id, gen) if c.status in ("rendered", "published")]
    with db.session() as s:
        vids = {v.creative_id: v for v in s.exec(select(db.Video)).all()}
    langs = sorted({c.language for c in rendered})
    fc1, fc2 = st.columns(2)
    pick = fc1.multiselect("Filter languages", langs, langs)
    mode_opts = sorted({c.visual_mode or "faceless" for c in rendered})
    mpick = fc2.multiselect("Filter video modes", mode_opts, mode_opts)
    items = [c for c in rendered if c.language in pick and (c.visual_mode or "faceless") in mpick and c.id in vids]
    for row in range(0, len(items), 4):
        cols = st.columns(4)
        for col, c in zip(cols, items[row:row + 4]):
            v = vids[c.id]
            with col:
                if v.thumbnail_path and Path(v.thumbnail_path).exists():
                    st.image(v.thumbnail_path, caption="Thumbnail / cover")
                if Path(v.path).exists():
                    st.video(v.path)
                st.markdown(f"**#{c.id}** · {languages()[c.language]['name']} · {c.creator_style} · "
                            f"`{(c.visual_mode or 'faceless').replace('_', ' ')}`  \n"
                            f"`{c.hook_type}` · `{c.format}` · {v.duration_s:.0f}s · render {v.render_seconds:.0f}s")
                st.caption(c.hook)
                if Path(v.path).exists():
                    st.download_button("Download", Path(v.path).read_bytes(), file_name=f"{a.id}_{c.id}.mp4",
                                       key=f"dl{c.id}")
                st.code(f"{c.post_caption}\n{' '.join(c.hashtags or [])}\n{tracking.link_for(c)}", language=None)


def page_performance() -> None:
    st.title("Performance")
    a = require_app()
    if not a:
        return
    source = st.radio("Data source", ["all", "real", "simulated"], horizontal=True)
    df = metrics.metrics_frame(a.id, source=source)
    t1, t2 = st.tabs(["Dashboard", "Add data"])
    with t1:
        if df.empty:
            st.info("No metrics yet — simulate a test (Growth Loop) or add real numbers in **Add data**.")
        else:
            sources = set("/".join(df["source"]).split("/"))
            sim_banner(sources)
            gens = sorted(df["generation"].unique())
            g = st.multiselect("Generations", gens, gens)
            sub = df[df["generation"].isin(g)]
            t = metrics.totals(sub)
            k = st.columns(4) + st.columns(4)
            k[0].metric("Views", num(t["views"]))
            k[1].metric("Hook rate", pct(t["hook_rate"]))
            k[2].metric("Avg watch", f"{t['avg_watch_pct']:.1f}%")
            k[3].metric("Engagement", pct(t["engagement_rate"], 2))
            k[4].metric("CTR", pct(t["ctr"], 2))
            k[5].metric("Installs", num(t["installs"]))
            k[6].metric("Installs / 1k views", num(t["installs_per_1k_views"], 2))
            k[7].metric("CPI", "₹0 (organic)" if not t["spend_inr"] else f"₹{t['cpi']:.0f}")
            metric = st.selectbox("Metric for breakdowns", ["installs_per_1k_views", "ctr", "hook_rate",
                                                            "engagement_rate", "avg_watch_pct", "cvr"],
                                  format_func=lambda m: metrics.METRIC_LABELS.get(m, m))
            dims = ["language", "hook_type", "format", "creator_style", "message", "length_bucket"]
            for i in range(0, len(dims), 2):
                cols = st.columns(2)
                for col, d in zip(cols, dims[i:i + 2]):
                    agg = pd.DataFrame([{d: v, **metrics.totals(grp)} for v, grp in sub.groupby(d)])
                    agg = agg.sort_values(metric, ascending=False)
                    fig = px.bar(agg, x=d, y=metric, text_auto=".3s", color_discrete_sequence=["#3b82f6"],
                                 title=title_with_source(f"{metrics.METRIC_LABELS.get(metric, metric)} by {d}", sources),
                                 hover_data={"creatives": True, "views": True})
                    fig.update_layout(height=320, margin=dict(t=50, b=10))
                    col.plotly_chart(fig, use_container_width=True)
            st.subheader("Creative leaderboard")
            cols = ["creative_id", "generation", "origin", "language", "hook_type", "format", "creator_style", "hook",
                    "views", "hook_rate", "avg_watch_pct", "engagement_rate", "ctr", "installs", "installs_per_1k_views",
                    "source"]
            st.dataframe(sub.sort_values("installs_per_1k_views", ascending=False)[cols], hide_index=True,
                         use_container_width=True,
                         column_config={c: st.column_config.NumberColumn(format="%.3f") for c in
                                        ["hook_rate", "engagement_rate", "ctr", "installs_per_1k_views"]})
    with t2:
        st.subheader("Manual entry (real data)")
        rows = [c for c in db.creatives(a.id) if c.tracking_slug and c.status in ("rendered", "published", "approved")]
        if rows:
            with st.form("manual"):
                cid = st.selectbox("Creative", [c.id for c in rows],
                                   format_func=lambda i: next(f"#{c.id} {c.tracking_slug} · {c.hook[:50]}" for c in rows if c.id == i))
                c = st.columns(4)
                vals = {}
                for i, f in enumerate(["impressions", "views", "views_3s", "likes", "comments", "shares", "saves",
                                       "clicks", "installs", "d1_retained", "d7_retained"]):
                    vals[f] = c[i % 4].number_input(f, min_value=0, value=0, step=1)
                vals["avg_watch_pct"] = c[3].number_input("avg_watch_pct", 0.0, 100.0, 0.0)
                vals["spend_inr"] = c[0].number_input("spend_inr", 0.0, value=0.0)
                plat = c[1].selectbox("platform", ["instagram", "youtube_shorts", "moj", "josh", "sharechat", "other"])
                if st.form_submit_button("Save real metrics"):
                    metrics.add_metric(cid, source="real", platform=plat, **vals)
                    st.success("Saved")
        st.subheader("CSV import (real data)")
        tmpl = ",".join(metrics.CSV_TEMPLATE_COLUMNS) + "\n" + "\n".join(
            f"{c.tracking_slug},instagram,,,,,,,,,,,,0,," for c in rows[:20])
        st.download_button("Download CSV template (with your slugs)", tmpl, file_name="metrics_template.csv")
        up = st.file_uploader("Upload CSV", type=["csv"])
        if up and st.button("Import"):
            n, errors = metrics.import_csv(up.getvalue().decode("utf-8"))
            st.success(f"Imported {n} rows")
            for e in errors[:20]:
                st.warning(e)


def _stat_table(rows: pd.DataFrame, metric: str) -> pd.DataFrame:
    if rows.empty:
        return rows
    out = rows[["value", "creatives", "views", "metric", "lift", "prob_best", "status"]].copy()
    out = out.rename(columns={"metric": metrics.METRIC_LABELS.get(metric, metric)})
    return out


def page_insights() -> None:
    st.title("AI insights")
    a = require_app()
    if not a:
        return
    c1, c2 = st.columns([1, 3])
    source = c1.radio("Data", ["all", "real", "simulated"], horizontal=False)
    if c2.button("Recompute insights", type="primary"):
        with st.spinner("Statistics → narrative…"):
            insights_perf.run(a.id, source=source)
    stats = insights_perf.compute(a.id, source=source)
    if stats.get("empty"):
        st.info("No performance data yet.")
        return
    sim_banner(stats["sources"])
    ins = insights_perf.latest(a.id)
    if ins and ins.narrative:
        st.markdown(ins.narrative)
        st.caption(f"Narrative from insights run for Gen {ins.generation}. Numbers are copied from the tables below; "
                   "lines citing any other number are dropped automatically.")
    st.caption(f"Primary metric: **{stats['metric_label']}** · overall {stats['overall']:.2f} · "
               f"{stats['creatives']} creatives · {stats['views']:,} views · winner needs ≥{stats['min_creatives']} "
               f"creatives and ≥{stats['min_views']} views; probability best from Beta posteriors (5,000 draws).")
    tabs = st.tabs(list(stats["dimensions"]) + ["combinations"])
    colcfg = {"lift": st.column_config.NumberColumn(format="percent"),
              "prob_best": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent")}
    for tab, (d, t) in zip(tabs, stats["dimensions"].items()):
        with tab:
            st.dataframe(_stat_table(t, stats["metric"]), hide_index=True, use_container_width=True, column_config=colcfg)
    with tabs[-1]:
        for k, t in stats["combos"].items():
            st.markdown(f"**{k}**")
            st.dataframe(_stat_table(t, stats["metric"]).head(8), hide_index=True, use_container_width=True,
                         column_config=colcfg)
    mvu = insights_perf.market_vs_us(a.id, stats)
    if mvu:
        st.subheader("Market vs. us")
        st.dataframe(pd.DataFrame(mvu), hide_index=True, use_container_width=True)
    st.subheader("Why: top vs bottom creatives")
    c1, c2 = st.columns(2)
    for col, key, label in ((c1, "top", "🏆 Top 3"), (c2, "bottom", "📉 Bottom 3")):
        with col:
            st.markdown(f"**{label}**")
            for r in stats[key]:
                cc = st.columns([1, 3])
                if r.get("thumbnail_path") and Path(r["thumbnail_path"]).exists():
                    cc[0].image(r["thumbnail_path"])
                cc[1].markdown(f"“{r['hook']}”  \n`{r['language']}` · `{r['hook_type']}` · `{r['format']}` · "
                               f"`{r['creator_style']}`  \n**{r[stats['metric']]:.2f}** {stats['metric_label']}")


def page_loop() -> None:
    st.title("Growth loop")
    a = require_app()
    if not a:
        return
    gen = db.current_generation(a.id)
    if gen == 0:
        st.info("Generate Gen 1 first (Generate page or Home → Run Full Loop).")
        return
    df = metrics.metrics_frame(a.id)
    tested = set(df["generation"].unique()) if not df.empty else set()
    c1, c2, c3 = st.columns(3)
    c1.metric("Current generation", gen)
    c2.metric("Tested generations", len(tested))
    c3.metric("Creatives with data", len(df))
    with st.expander("Test the current generation", expanded=gen not in tested):
        do_render = st.checkbox("Render videos first", value=True)
        if st.button(f"Run Gen {gen} test (render → {SIM_BADGE} → insights)"):
            with st.status("Testing…", expanded=True):
                loop.run_generation(a.id, gen, simulated=True, render=do_render, progress=st.write)
            st.rerun()
    st.subheader("Generate next batch")
    c1, c2 = st.columns(2)
    ratio = c1.slider("Explore share", 0.0, 1.0, env_float("EXPLORE_RATIO", 0.3), 0.05)
    size = c2.number_input("Batch size", 2, 60, env_int("GEN1_RENDER_COUNT", 12))
    if st.button(f"🚀 Generate Next Batch (Gen {gen + 1})", type="primary", disabled=gen not in tested):
        with st.spinner("Thompson sampling → hooks → scripts → compliance…"):
            try:
                alloc = optimizer.generate_next_batch(a.id, size=size, explore_ratio=ratio)
                st.success(f"Gen {alloc['generation']}: {alloc['created']} creatives "
                           f"({alloc['origin_counts']}) — go to Studio to render, or test above.")
            except llm.LLMError as e:
                st.error(str(e))
    if gen not in tested:
        st.caption("Test the current generation before generating the next one.")

    summ = loop.generation_summary(a.id)
    if summ:
        st.subheader(title_with_source("Generation-over-generation", {"simulated"} if "simulated" in "/".join(df["source"]) else set()))
        s = pd.DataFrame(summ)
        fig = go.Figure()
        fig.add_bar(x=s["generation"].astype(str), y=s["primary"], name=metrics.METRIC_LABELS.get(s["metric"][0], "primary"),
                    marker_color="#3b82f6", text=s["primary"].round(2), textposition="outside")
        fig.add_scatter(x=s["generation"].astype(str), y=s["ctr"] * 100, name="CTR %", yaxis="y2", mode="lines+markers",
                        line=dict(color="#f59e0b", width=3))
        fig.update_layout(height=360, xaxis_title="Generation", yaxis_title=metrics.METRIC_LABELS.get(s["metric"][0]),
                          yaxis2=dict(title="CTR %", overlaying="y", side="right"), legend=dict(orientation="h"),
                          xaxis=dict(type="category"))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(s, hide_index=True, use_container_width=True)

    ins = insights_perf.latest(a.id)
    alloc = (ins.findings or {}).get("next_allocation") if ins else None
    if alloc:
        st.subheader(f"Allocation of Gen {alloc['generation']} ({alloc['exploit']} exploit / {alloc['explore']} explore)")
        dims = list(alloc["share"])
        cols = st.columns(len(dims))
        for col, d in zip(cols, dims):
            sh = pd.DataFrame(sorted(alloc["share"][d].items(), key=lambda kv: -kv[1]), columns=[d, "creatives"])
            fig = px.pie(sh, names=d, values="creatives", hole=0.5, title=d)
            fig.update_layout(height=260, margin=dict(t=40, b=0, l=0, r=0), showlegend=False)
            fig.update_traces(textinfo="label+value")
            col.plotly_chart(fig, use_container_width=True)
        with st.expander("Guidance passed to the LLM for this batch"):
            st.text(alloc.get("guidance", ""))

    st.subheader("Lineage (parent → children)")
    rows = db.creatives(a.id)
    by_id = {c.id: c for c in rows}
    kids = [c for c in rows if c.parent_creative_id]
    if not kids:
        st.caption("No children yet.")
    perf = df.set_index("creative_id")["installs_per_1k_views"].to_dict() if not df.empty else {}
    parents = sorted({c.parent_creative_id for c in kids})
    for pid in parents[:12]:
        p = by_id.get(pid)
        if not p:
            continue
        st.markdown(f"**Gen {p.generation} #{p.id}** ({num(perf.get(p.id), 2)}/1k) — “{p.hook}”")
        for c in [k for k in kids if k.parent_creative_id == pid]:
            st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ Gen {c.generation} #{c.id} `{c.hook_type}` "
                        f"({num(perf.get(c.id), 2)}/1k) — “{c.hook}”")

    st.divider()
    if st.toggle("🔍 Debug: reveal hidden simulator truth (after the demo)"):
        tc = loop.truth_check(a.id)
        if tc:
            st.dataframe(pd.DataFrame(tc).T, use_container_width=True)
            st.caption("favoured = the optimizer's highest estimated effect; true_best = the hidden effect the "
                       "simulator used. The optimizer never reads the hidden effects.")
        truth = simulator.reveal_truth(a.id)
        for d, vals in truth.items():
            st.markdown(f"**{d}**: " + " · ".join(f"{v} ({np.exp(e):.2f}×)" for v, e in vals))


def page_economics() -> None:
    st.title("Economics")
    a = require_app()
    if not a:
        return
    s = cost.summary(a.id)
    c = st.columns(5)
    c[0].metric("Cost per creative", f"₹{s['cost_per_creative_inr']:.2f}", help="Actual Gemini + Apify spend ÷ videos")
    c[1].metric("Time per video", f"{s['seconds_per_video']:.0f}s" if s["seconds_per_video"] else "—")
    c[2].metric("Videos per hour", f"{s['videos_per_hour']:.0f}" if s["videos_per_hour"] else "—")
    c[3].metric("Experiments per week", f"{s['experiments_per_week']:,.0f}" if s["experiments_per_week"] else "—",
                help="One laptop, 40 working hours")
    c[4].metric("Actual API spend", f"₹{s['total_cost_inr']:,.2f}", help="Logged per call from token usage / Apify run charges")
    n = max(1, s["videos_rendered"])
    trad_cost = s["creator_cost_inr"] * n
    trad_days = s["creator_turnaround_days"]
    engine_days = s["total_seconds"] / 86400
    st.subheader(f"Traditional workflow vs engine — for the same {n} videos")
    comp = pd.DataFrame({
        "workflow": ["Traditional creators", "AI growth engine"],
        "cost_inr": [trad_cost, s["total_cost_inr"]],
        "days": [trad_days, round(engine_days, 3)],
    })
    c1, c2 = st.columns(2)
    f1 = px.bar(comp, x="workflow", y="cost_inr", text_auto=",.0f", title="Cost (₹)", color="workflow",
                color_discrete_sequence=["#9ca3af", "#10b981"])
    f1.update_layout(showlegend=False, height=320)
    c1.plotly_chart(f1, use_container_width=True)
    f2 = px.bar(comp, x="workflow", y="days", text_auto=".2f", title="Turnaround (days)", color="workflow",
                color_discrete_sequence=["#9ca3af", "#10b981"])
    f2.update_layout(showlegend=False, height=320)
    c2.plotly_chart(f2, use_container_width=True)
    st.caption(f"Traditional: brief → creator → negotiation → shoot → revisions → approval. Assumes "
               f"₹{s['creator_cost_inr']:,.0f} per reel and {trad_days:.0f} days turnaround "
               "(CREATOR_REEL_COST_INR / CREATOR_TURNAROUND_DAYS in .env).")
    st.subheader("Where the time goes")
    bs = pd.DataFrame([{"step": k, **v} for k, v in s["by_step"].items()]).sort_values("seconds", ascending=False) \
        if s["by_step"] else pd.DataFrame()
    if not bs.empty:
        st.dataframe(bs, hide_index=True, use_container_width=True,
                     column_config={"seconds": st.column_config.NumberColumn(format="%.1f")})
    st.caption(f"{s['creatives_total']} variations · {s['creatives_scripted']} scripted · {s['videos_rendered']} "
               f"videos · {s['total_seconds'] / 60:.1f} min total compute · actual spend ₹{s['total_cost_inr']:.0f}")


# ---------------------------------------------------------------- navigation

def _persona_panel(a: db.App) -> None:
    """This app's recurring characters: propose → candidates (Magnific) → pick a face → library."""
    from engine.production import personas as pz

    cast = pz.personas(a.id)
    ready_n = sum(1 for p in cast if p.status in ("approved", "ready"))
    with st.expander(f"🧑‍🤝‍🧑 Characters for this app ({ready_n}/{len(cast)} ready)", expanded=bool(cast) and ready_n < len(cast)):
        st.caption("Recurring AI characters derived from this app's own audience: protagonists act in *character* "
                   "videos, the presenter talks to camera in *AI character* videos. Always fictional, never testimonials.")
        if not cast:
            if st.button("🎭 Cast characters for this app"):
                pz.propose(a.id)
                pz.write_sheet(a.id)
                st.rerun()
            return
        for p in cast:
            st.markdown(f"**{p.name}** · {p.role} · {p.age} · `{p.status}` — {p.description}")
            if p.status == "proposed":
                st.caption("Waiting for candidate portraits — ask Claude to “run the persona jobs for "
                           f"{a.id}” (≈ 200 credits per character).")
            if p.candidates:
                cols = st.columns(len(p.candidates))
                for i, (col, cand) in enumerate(zip(cols, p.candidates), start=1):
                    with col:
                        if Path(cand["file"]).exists():
                            st.image(cand["file"])
                        chosen = p.approved.get("file") == cand["file"]
                        if st.button("✅ Chosen" if chosen else f"Pick #{i}", key=f"pick_{p.id}_{i}", disabled=chosen):
                            pz.pick(a.id, p.key, i)
                            st.rerun()
        if pz.pending_jobs(a.id):
            st.info(f"{len(pz.pending_jobs(a.id))} character jobs waiting for Magnific — ask Claude to run them.")


MODE_ICON = {"faceless": "🎞️", "images_vo": "🖼️", "character": "🧑", "ai_character": "🗣️"}


def _scripts_review(a: db.App, b: Any, busy: bool) -> None:
    """Read, edit, approve or reject each script, then approve the batch (starts production)."""
    from engine import batch as bt, magnific, modes as vmodes

    rows = bt.creatives_of(b)
    for c in rows:
        flag = " ⚠️ flagged" if c.compliance_status == "flagged" else ""
        state = {"approved": "✅", "rejected": "❌"}.get(c.status, "🕓")
        with st.expander(f"{state} #{c.id} · {languages()[c.language]['name']} · {MODE_ICON.get(c.visual_mode, '')} "
                         f"{c.visual_mode.replace('_', ' ')} · {c.creator_style} · {c.format} — {c.hook}{flag}",
                         expanded=c.status not in ("approved", "rejected")):
            if c.compliance_issues:
                st.warning("Safety check: " + "; ".join(c.compliance_issues))
            scenes = (c.script or {}).get("scenes", [])
            edits = {}
            for i, sc in enumerate(scenes):
                cc = st.columns([0.6, 5, 3, 3])
                cc[0].markdown(f"**{i + 1}**")
                vo = cc[1].text_area("Voiceover", sc.get("voiceover", ""), key=f"vo{c.id}_{i}", height=70,
                                     label_visibility="collapsed")
                ost = cc[2].text_input("On screen", sc.get("on_screen_text", ""), key=f"os{c.id}_{i}",
                                       label_visibility="collapsed")
                cc[3].caption(f"🎬 {(sc.get('visual') or {}).get('query_en', '')}")
                if vo != sc.get("voiceover", "") or ost != sc.get("on_screen_text", ""):
                    edits[i] = (vo, ost)
            st.caption(f"CTA: {(c.script or {}).get('cta', '')} · caption: {c.post_caption[:160]}")
            bb = st.columns(3)
            if bb[0].button("✅ Approve", key=f"ap{c.id}"):
                for i, (vo, ost) in edits.items():
                    bt.edit_scene(c.id, i, vo, ost)
                bt.decide(c.id, True)
                st.rerun()
            if bb[1].button("❌ Reject", key=f"rj{c.id}"):
                bt.decide(c.id, False)
                st.rerun()
            if edits and bb[2].button("💾 Save edits", key=f"sv{c.id}"):
                for i, (vo, ost) in edits.items():
                    bt.edit_scene(c.id, i, vo, ost)
                st.rerun()
    n_ok = sum(1 for c in rows if c.status != "rejected")
    est = sum(vmodes.mode_cost(c.visual_mode) for c in rows if c.status != "rejected")
    ceiling = int(round(est * (1 + bt.MARGIN)))
    st.divider()
    if not magnific.enabled():
        st.info("No MAGNIFIC_API_KEY in .env: videos that need Magnific will wait at their job sheet. "
                "Faceless videos with good stock matches still finish.")
    if st.button(f"✅ Approve {n_ok} scripts & start production (up to {ceiling:,} Magnific credits)",
                 type="primary", disabled=busy or not n_ok):
        bt.approve(b.id)
        if launch(a.id, ["batch", "run", str(b.id)]):
            st.rerun()


def page_batch() -> None:
    from engine import batch as bt, magnific, modes as vmodes
    from engine.production import assets as crew_assets
    from engine.production.common import latest_run

    st.title("Scripts & approval")
    st.caption("One iteration = 10 scripts. Languages and video modes come from this app's market research. "
               "Read them, edit or reject any, then approve — videos are then made one by one "
               "(director → assets → editor → review → QC → your final OK).")
    a = require_app()
    if not a:
        return
    b = bt.latest(a.id)
    busy = progress.is_running(a.id) or is_busy(a.id)
    if b is None or b.status in ("done", "stopped"):
        if st.button("✍️ Write the next 10 scripts", type="primary", disabled=busy):
            if launch(a.id, ["batch", "create", a.id]):
                st.rerun()
    progress_panel(a.id)
    if b is None or b.status == "building":
        return
    mix = b.mix or {}
    c1, c2, c3 = st.columns(3)
    c1.markdown("**Languages** · " + ", ".join(f"{languages().get(k, {}).get('name', k)} {v}"
                                               for k, v in (mix.get("languages") or {}).items())
                + f"  \n<small>{mix.get('language_source', '')}</small>", unsafe_allow_html=True)
    c2.markdown("**Video modes** · " + ", ".join(f"{MODE_ICON.get(k, '')} {k.replace('_', ' ')} {v}"
                                                 for k, v in (mix.get("modes") or {}).items())
                + f"  \n<small>{mix.get('mode_source', '')}</small>", unsafe_allow_html=True)
    c3.metric("Magnific credits (est.)", f"{b.credits_estimate:,}", help=f"{crew_assets.budget_left():,} left this month")

    rows = bt.creatives_of(b)
    if b.status == "scripts_ready":
        _scripts_review(a, b, busy)
        return

    st.markdown(f"**Batch #{b.id} · {b.status}** · credits {b.credits_spent:,} of {b.credits_approved:,} approved")
    for c in rows:
        if c.status == "rejected":
            continue
        run = latest_run(c.id)
        cols = st.columns([3, 2])
        with cols[0]:
            st.markdown(f"**#{c.id}** · {languages()[c.language]['name']} · {MODE_ICON.get(c.visual_mode, '')} "
                        f"{c.visual_mode.replace('_', ' ')} — {c.hook}")
            st.caption(f"{run.status.replace('_', ' ')} · v{run.version} · QC {run.qc_score or 0:.0f}"
                       if run else ("⏳ in queue" if b.current_creative != c.id else "🔄 in production"))
        if run and run.versions:
            v = run.versions[-1]
            if Path(v["path"]).exists():
                cols[1].video(v["path"])
    st.caption("Approve or send back each finished video on the **Production Crew** page.")


CREW_FLOW = ["director", "assets", "editor", "review", "qc", "owner", "done"]
CREW_LABEL = {"director": "🎬 Director", "assets": "✨ Magnific", "editor": "✂️ Editor", "review": "🔍 Reviewers",
              "qc": "✅ QC", "owner": "👤 Owner", "done": "📦 Export"}
SEV_ICON = {"blocker": "🟥", "major": "🟧", "minor": "🟨"}


def page_crew() -> None:
    from engine.production import assets as crew_assets, pipeline as crew
    from engine.production.common import fmt_tc, tasks as crew_tasks
    from sqlmodel import select as sel

    st.title("Production Crew")
    st.caption("Owner → Director (scene plan) → Magnific (natural voice, hook clip) → Editor (v1) → Reviewers "
               "(text · visual · audio · brand) → QC → revision tasks back to the right role → Editor (v2…) → "
               "Owner approves or sends back → final export.")
    a = require_app()
    if not a:
        return
    k1, k2, k3 = st.columns(3)
    k1.metric("Magnific credits left this month", f"{crew_assets.budget_left():,}",
              help="Monthly cap from config/production.yaml minus credits logged for imported Magnific assets.")
    k2.metric("Spent this month", f"{crew_assets.credits_spent_this_month():,}")
    k3.metric("Per-video cap", f"{crew_assets.mcfg()['per_video_credit_cap']}")

    _persona_panel(a)

    with st.expander("➕ Send a creative to the crew", expanded=False):
        ready = [c for c in db.creatives(a.id) if c.script and c.status in ("approved", "draft", "rendered")]
        if not ready:
            st.info("No scripted creatives yet — generate ideas first.")
        else:
            pick = st.selectbox("Creative", ready, format_func=lambda c: f"#{c.id} · {languages()[c.language]['name']} · "
                                                                           f"{c.creator_style} · {c.hook[:60]}")
            skip = st.checkbox("Don't wait for Magnific (free voice + stock footage)", value=False)
            if st.button("🎬 Start production", type="primary"):
                if launch(a.id, ["crew", "start", str(pick.id)] + (["--skip-assets"] if skip else [])):
                    st.rerun()
    progress_panel(a.id)

    with db.session() as s:
        runs = list(s.exec(sel(db.ProductionRun).where(db.ProductionRun.app_id == a.id)
                           .order_by(db.ProductionRun.id.desc())).all())
    if not runs:
        st.info("No productions yet.")
        return
    run = st.selectbox("Production", runs, format_func=lambda r: f"Run {r.id} · creative #{r.creative_id} · v{r.version} · "
                                                                 f"{r.status.replace('_', ' ')}")
    stage_i = CREW_FLOW.index(run.stage) if run.stage in CREW_FLOW else 0
    cols = st.columns(len(CREW_FLOW))
    for i, (col, k) in enumerate(zip(cols, CREW_FLOW)):
        mark = "✅" if i < stage_i or run.status == "approved" else ("🔵" if i == stage_i else "⚪")
        col.markdown(f"{mark} **{CREW_LABEL[k]}**")
    status_msg = {"waiting_assets": "Waiting for Magnific assets — ask Claude to “run the Magnific jobs for run "
                                    f"{run.id}”, or skip them.",
                  "awaiting_owner": "Waiting for your decision below.", "approved": "Approved and exported.",
                  "running": "Crew at work…", "failed": "Failed — see the log."}.get(run.status, run.status)
    st.info(f"**{run.status.replace('_', ' ').title()}** · {status_msg}")
    if run.plan.get("director_note"):
        st.markdown(f"🎬 *Director:* {run.plan['director_note']}")

    if run.status == "waiting_assets":
        pend = crew_assets.pending(run)
        st.markdown(f"**{len(pend)} Magnific jobs** (~{sum(r['credits'] for r in pend)} credits)")
        st.dataframe([{"job": r["id"], "tool": r["mcp"]["tool"], "credits": r["credits"],
                       "what": (r["mcp"]["arguments"].get("text") or r["mcp"]["arguments"].get("prompt", ""))[:90]}
                      for r in pend], hide_index=True, use_container_width=True)
        b1, b2 = st.columns(2)
        if b1.button("▶️ Continue (assets imported)"):
            if launch(a.id, ["crew", "resume", str(run.id)]):
                st.rerun()
        if b2.button("⏭️ Skip Magnific, use free fallbacks"):
            if launch(a.id, ["crew", "resume", str(run.id), "--skip-assets"]):
                st.rerun()

    vers = run.versions or []
    if vers:
        st.subheader("Versions")
        vcols = st.columns(min(4, len(vers)))
        for col, v in zip(vcols, vers[-4:]):
            with col:
                if Path(v["path"]).exists():
                    st.video(v["path"])
                qc_txt = f"QC {v['qc_score']:.0f}" if v.get("qc_score") is not None else "QC —"
                st.markdown(f"**v{v['version']}** · {v['duration']:.0f}s · {qc_txt} · "
                            + ("✅ pass" if v.get("passed") else "❌ fail" if "passed" in v else "…"))

    ts = crew_tasks(run.id)
    if ts:
        st.subheader("Review tasks")
        st.dataframe([{"v": t.version, "sev": f"{SEV_ICON.get(t.severity, '')} {t.severity}", "time": fmt_tc(t.timecode),
                       "scene": t.scene, "raised by": t.raised_by, "→ fix by": t.assigned_to, "issue": t.issue,
                       "fix": (t.fix or {}).get("type"), "status": t.status} for t in ts],
                     hide_index=True, use_container_width=True)

    if run.status == "awaiting_owner":
        st.subheader("👤 Owner decision")
        note = st.text_area("Note (required to send back: say what to change, in plain words)", key=f"note{run.id}")
        o1, o2 = st.columns(2)
        if o1.button("✅ Approve & export", type="primary"):
            crew.approve(run.id, note)
            st.success("Approved — the video is now the creative's final cut (Studio).")
            st.rerun()
        if o2.button("↩️ Send back to the crew", disabled=not note.strip()):
            if launch(a.id, ["crew", "reject", str(run.id), "--note", note]):
                st.rerun()

    with st.expander("Crew log", expanded=run.status != "approved"):
        for e in reversed(run.log or []):
            st.markdown(f"`{e['at'][11:19]}` **{e['role']}** — {e['message']}")


# ---------------------------------------------------------------- the guided engine (one page, step by step)

FLOW = [("app", "App"), ("research", "Market research"), ("understand", "Understanding"),
        ("scripts", "Scripts & approval"), ("production", "Production")]

STYLE = """
<style>
:root{--bg:#0B0D12;--card:#131722;--card2:#181D2A;--line:#242A38;--muted:#8A93A6;--text:#E7E9EF;
 --brand:#7C5CFF;--brand2:#4F8CFF;--ok:#22C55E;--warn:#F59E0B;--bad:#EF4444;--you:#A78BFA}
.block-container{max-width:1200px;padding-top:2.2rem;padding-bottom:4rem}
[data-testid="stHeader"]{background:transparent}
[data-testid="stAppDeployButton"],[data-testid="stMainMenu"]{display:none}
h1,h2,h3{letter-spacing:-.02em}
/* buttons */
[data-testid="stBaseButton-primary"]{background:linear-gradient(135deg,var(--brand),var(--brand2));border:0;
 font-weight:600;box-shadow:0 6px 20px rgba(124,92,255,.28)}
[data-testid="stBaseButton-primary"]:hover{filter:brightness(1.08);border:0}
[data-testid="stBaseButton-secondary"]{background:var(--card2);font-weight:500}
/* containers with key card_* become cards */
[class*="st-key-card"]{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px 18px 14px;
 box-shadow:0 1px 0 rgba(255,255,255,.03) inset,0 12px 30px rgba(0,0,0,.25)}
[class*="st-key-cardhero"]{background:radial-gradient(1200px 300px at 0% 0%,rgba(124,92,255,.18),transparent 60%),
 radial-gradient(800px 260px at 100% 0%,rgba(79,140,255,.14),transparent 60%),var(--card)}
[data-testid="stExpander"] details{background:var(--card);border:1px solid var(--line);border-radius:16px}
[data-testid="stExpander"] summary{font-weight:600;padding:14px 16px}
[data-testid="stProgress"] > div > div > div > div{background:linear-gradient(90deg,var(--brand),var(--brand2))}
[data-testid="stVideo"] video,video{border-radius:12px;background:#000}
[class*="st-key-card_vid"] video{aspect-ratio:9/16;height:auto;max-height:430px;width:100%;object-fit:contain}
[data-testid="stHeaderActionElements"]{display:none}
/* typography helpers */
.eyebrow{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:600}
.hero h1{font-size:2.3rem;margin:.1rem 0 .3rem;font-weight:800;
 background:linear-gradient(90deg,#fff,#C9BEFF 60%,#9DB9FF);-webkit-background-clip:text;color:transparent}
.hero p{color:var(--muted);font-size:1.02rem;max-width:760px;margin:0}
.apphead{display:flex;align-items:center;gap:16px}
.apphead img{width:60px;height:60px;border-radius:16px;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.apphead h2{margin:0;font-size:1.7rem}.apphead p{margin:2px 0 0;color:var(--muted)}
/* stepper */
.stepper{display:flex;align-items:flex-start;margin:22px 0 26px}
.stepper .s{flex:1;position:relative;text-align:center}
.stepper .s:not(:last-child)::after{content:"";position:absolute;top:17px;left:calc(50% + 22px);right:calc(-50% + 22px);
 height:2px;background:var(--line)}
.stepper .s.done:not(:last-child)::after{background:linear-gradient(90deg,var(--ok),rgba(34,197,94,.35))}
.stepper .dot{width:36px;height:36px;border-radius:50%;margin:0 auto 8px;display:flex;align-items:center;
 justify-content:center;font-weight:700;font-size:.9rem;border:2px solid var(--line);background:var(--card);color:var(--muted)}
.stepper .done .dot{background:rgba(34,197,94,.15);border-color:var(--ok);color:var(--ok)}
.stepper .now .dot{background:linear-gradient(135deg,var(--brand),var(--brand2));border-color:transparent;color:#fff;
 box-shadow:0 0 0 6px rgba(124,92,255,.18)}
.stepper .lbl{font-weight:600;font-size:.9rem}.stepper .sub{font-size:.76rem;color:var(--muted);margin-top:2px}
.stepper .wait .lbl{color:var(--muted)}
/* section title */
.sec{display:flex;align-items:center;gap:12px;margin:28px 0 12px}
.sec .n{width:28px;height:28px;border-radius:9px;background:rgba(124,92,255,.16);color:#C9BEFF;display:flex;
 align-items:center;justify-content:center;font-weight:700;font-size:.85rem}
.sec h3{margin:0;font-size:1.25rem}.sec span.sub{color:var(--muted);font-size:.9rem;margin-left:auto}
/* stats */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:6px 0 14px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px}
.stat .k{font-size:.74rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;font-weight:600}
.stat .v{font-size:1.7rem;font-weight:800;margin-top:4px;letter-spacing:-.02em}
.stat .d{font-size:.78rem;color:var(--muted)}
/* chips */
.cg{margin:2px 0 14px}.cg .t{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;
 font-weight:600;margin-bottom:6px}
.chips{display:flex;flex-wrap:wrap;gap:7px}
.chip{padding:5px 12px;border-radius:999px;font-size:.84rem;border:1px solid var(--line);background:var(--card2)}
.chip.blue{border-color:rgba(79,140,255,.35);background:rgba(79,140,255,.10)}
.chip.red{border-color:rgba(239,68,68,.35);background:rgba(239,68,68,.09)}
.chip.green{border-color:rgba(34,197,94,.35);background:rgba(34,197,94,.09)}
.chip.amber{border-color:rgba(245,158,11,.35);background:rgba(245,158,11,.09)}
.chip.violet{border-color:rgba(167,139,250,.4);background:rgba(167,139,250,.10)}
/* video card */
.vhead{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:.74rem;font-weight:700}
.pill::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.pill.q{color:#9AA3B5;background:rgba(154,163,181,.12)} .pill.run{color:#7FB0FF;background:rgba(79,140,255,.14)}
.pill.wait{color:#FBBF24;background:rgba(245,158,11,.13)} .pill.you{color:#C4B5FD;background:rgba(167,139,250,.15)}
.pill.ok{color:#4ADE80;background:rgba(34,197,94,.13)} .pill.bad{color:#F87171;background:rgba(239,68,68,.13)}
.tag{font-size:.74rem;color:var(--muted);padding:2px 8px;border:1px solid var(--line);border-radius:7px}
.vid{font-weight:700}
.mini{display:flex;gap:4px;margin:4px 0 12px}
.mini span{flex:1;height:5px;border-radius:3px;background:var(--line)}
.mini span.d{background:var(--ok)} .mini span.n{background:linear-gradient(90deg,var(--brand),var(--brand2))}
.hook{font-size:.92rem;line-height:1.45;margin:10px 0 6px}
.vfoot{display:flex;gap:14px;font-size:.78rem;color:var(--muted);margin-bottom:10px}
.vfoot b{color:var(--text);font-weight:600}
.empty{height:230px;border-radius:12px;background:repeating-linear-gradient(45deg,var(--card2),var(--card2) 10px,
 var(--card) 10px,var(--card) 20px);display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:.85rem}
/* app cards */
.appc{display:flex;gap:12px;align-items:center;margin-bottom:8px}
.appc img,.appc .ph{width:48px;height:48px;border-radius:13px}
.appc .ph{background:linear-gradient(135deg,var(--brand),var(--brand2));display:flex;align-items:center;justify-content:center;
 font-weight:800;font-size:1.2rem;color:#fff}
.appc b{font-size:1.05rem}.appc small{display:block;color:var(--muted)}
.appdesc{color:var(--muted);font-size:.88rem;min-height:44px}
.dots{display:flex;gap:5px;margin:10px 0 12px}.dots span{flex:1;height:4px;border-radius:2px;background:var(--line)}
.dots span.d{background:var(--ok)}
/* sidebar */
section[data-testid="stSidebar"] .brand{font-weight:800;font-size:1.05rem;margin:4px 0 10px}
section[data-testid="stSidebar"] .brand span{background:linear-gradient(135deg,var(--brand),var(--brand2));
 -webkit-background-clip:text;color:transparent}
.sbstat{font-size:.78rem;color:var(--muted);line-height:1.7}
.sbstat b{color:var(--text);font-weight:600}
</style>"""


def html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def _esc(x: Any) -> str:
    import html as _h

    return _h.escape(str(x))


def chips(items: list[str], color: str = "", title: str = "") -> None:
    items = [i for i in items if i]
    if items:
        html(f"<div class='cg'>{f'<div class=t>{_esc(title)}</div>' if title else ''}<div class='chips'>"
             + "".join(f"<span class='chip {color}'>{_esc(t)}</span>" for t in items) + "</div></div>")


def stats(rows: list[tuple[str, Any, str]]) -> None:
    html("<div class='stats'>" + "".join(f"<div class='stat'><div class='k'>{_esc(k)}</div><div class='v'>{_esc(v)}</div>"
                                         f"<div class='d'>{_esc(d)}</div></div>" for k, v, d in rows) + "</div>")


def section(n: Any, title: str, sub: str = "") -> None:
    html(f"<div class='sec'><div class='n'>{n}</div><h3>{_esc(title)}</h3><span class='sub'>{_esc(sub)}</span></div>")


def _img64(path: Optional[str]) -> str:
    import base64

    if not path or not Path(path).exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def _flow_state(a: db.App) -> tuple[dict[str, tuple[str, str]], Any]:
    """Each stage → (done|now|wait, one-line summary), plus the latest batch."""
    from engine import batch as bt
    from engine.research import run as research

    pb, u, b = research.latest_playbook(a.id), db.latest_understanding(a.id), bt.latest(a.id)
    s: dict[str, tuple[str, str]] = {"app": ("done", "ready")}
    s["research"] = ("done", f"{pb.created_at:%d %b}") if pb else ("wait", "not run yet")
    s["understand"] = ("done", f"{len(u.personas)} personas") if u else ("wait", "—")
    if b is None:
        s["scripts"], s["production"] = ("wait", "—"), ("wait", "—")
    elif b.status == "building":
        s["scripts"], s["production"] = ("now", "writing…"), ("wait", "—")
    elif b.status == "scripts_ready":
        s["scripts"], s["production"] = ("now", "needs your OK"), ("wait", "—")
    else:
        vids = [c for c in bt.creatives_of(b) if c.status != "rejected"]
        s["scripts"] = ("done", f"{len(vids)} approved")
        done = sum(1 for c in vids if (r := _run_of(c.id)) and r.status in ("awaiting_owner", "approved"))
        s["production"] = ("done" if done == len(vids) else "now", f"{done}/{len(vids)} videos")
    if progress.is_running(a.id):  # a Go run is live: the first unfinished stage is the one working
        for k in ("research", "understand", "scripts"):
            if s[k][0] != "done":
                s[k] = ("now", "working…")
                break
    elif s["research"][0] == "wait":
        s["research"] = ("now", "press Go")
    return s, b


def _run_of(creative_id: int) -> Any:
    from engine.production.common import latest_run

    return latest_run(creative_id)


def _flow_bar(state: dict[str, tuple[str, str]]) -> None:
    cells = []
    for i, (k, label) in enumerate(FLOW, start=1):
        cls, sub = state.get(k, ("wait", ""))
        dot = "✓" if cls == "done" else str(i)
        cells.append(f"<div class='s {cls}'><div class='dot'>{dot}</div><div class='lbl'>{label}</div>"
                     f"<div class='sub'>{_esc(sub)}</div></div>")
    html("<div class='stepper'>" + "".join(cells) + "</div>")


@st.dialog("Delete app")
def _confirm_delete(a: db.App) -> None:
    st.markdown(f"This permanently removes **{a.name}**: its brief, screenshots, research, scripts, videos and "
                "characters. Spend history is kept so budgets stay correct. This can't be undone.")
    typed = st.text_input(f"Type **{a.name}** to confirm")
    busy = progress.is_running(a.id) or is_busy(a.id)
    if busy:
        st.warning("The engine is working on this app right now — stop it first.")
    if st.button("Delete permanently", type="primary", disabled=typed.strip() != a.name or busy,
                 use_container_width=True):
        db.delete_app(a.id)
        for k in ("app_id", "flow_open"):
            st.session_state.pop(k, None)
        st.query_params.clear()
        st.rerun()


def _pick_app() -> None:
    html("<div class='hero'><div class='eyebrow'>AI UGC growth engine · India</div><h1>Which app are we growing?</h1>"
         "<p>Pick an app or create a new one, then press <b>Go</b>. The engine studies the market, understands the "
         "app, writes 10 scripts for your approval and produces the videos one by one.</p></div>")
    html("<div style='height:22px'></div>")
    apps = db.list_apps()
    cols = st.columns(3, gap="medium")
    for i, a in enumerate(apps):
        state, _ = _flow_state(a)
        with cols[i % 3], st.container(key=f"card_app_{a.id}"):
            icon = _img64(a.icon_path)
            pic = f"<img src='{icon}'>" if icon else f"<div class='ph'>{_esc(a.name[:1])}</div>"
            html(f"<div class='appc'>{pic}<div><b>{_esc(a.name)}</b><small>{_esc(a.category)}</small></div></div>"
                 f"<div class='appdesc'>{_esc((a.brief or {}).get('one_liner', '')[:120])}</div>"
                 "<div class='dots'>" + "".join(f"<span class='{'d' if state[k][0] == 'done' else ''}' title='{lbl}'></span>"
                                                for k, lbl in FLOW[1:]) + "</div>")
            o, d = st.columns([3, 1])
            if o.button("Open", key=f"open_{a.id}", type="primary", use_container_width=True):
                st.session_state["app_id"], st.session_state["flow_new"] = a.id, False
                st.session_state["flow_open"] = True
                st.query_params["app"] = a.id
                st.rerun()
            if d.button("", key=f"del_{a.id}", icon=":material/delete:", help=f"Delete {a.name}",
                        use_container_width=True):
                _confirm_delete(a)
    with cols[len(apps) % 3], st.container(key="card_app_new"):
        html("<div class='appc'><div class='ph' style='background:var(--card2);color:var(--muted)'>+</div>"
             "<div><b>New app</b><small>Start from its brief</small></div></div>"
             "<div class='appdesc'>Describe the app — a Play Store link adds its real screenshots and reviews.</div>"
             "<div class='dots'><span></span><span></span><span></span><span></span></div>")
        if st.button("Create new app", use_container_width=True):
            st.session_state["flow_new"] = True
            st.rerun()


def _new_app() -> None:
    if st.button("← All apps"):
        st.session_state["flow_new"] = False
        st.rerun()
    html("<div class='hero'><div class='eyebrow'>Step 1</div><h1>Create a new app</h1>"
         "<p>Everything else — research, personas, scripts, video types — is derived from this brief.</p></div>")
    html("<div style='height:16px'></div>")
    samples = sample_briefs()
    with st.container(key="card_newapp"):
        src = st.radio("Start from", ["Blank", "Sample brief"], horizontal=True)
        base: dict[str, Any] = {}
        if src == "Sample brief":
            k = st.selectbox("Sample", list(samples))
            base = json.loads(samples[k].read_text(encoding="utf-8"))
        a = brief_editor(base, "flow_brief")
    if a:
        st.session_state["flow_new"], st.session_state["flow_open"] = False, True
        st.query_params["app"] = a.id
        st.rerun()


def _research_view(a: db.App) -> None:
    from engine.research import run as research

    pb = research.latest_playbook(a.id)
    if pb is None:
        st.caption("Not run yet — press Go.")
        return
    sm = research.summary(pb.run_id)
    stats([("Demand keywords", sm["keywords"], "what people search"), ("Videos found", sm["candidates"], "YouTube + Instagram"),
           ("Relevant", sm["relevant"], "about this problem"), ("Watched by AI", sm["analysed"], "patterns extracted")])
    kw, _V, A = research.run_frames(pb.run_id)
    if not kw.empty:
        chips(list(kw.sort_values("demand_score", ascending=False)["text"].head(14)), "blue", "What people search for")
    pat = research.market_patterns(a.id)
    c1, c2 = st.columns(2, gap="large")
    for i, (dim, label, color) in enumerate((("format", "Formats that win", "green"), ("hook_type", "Hooks that win", "amber"),
                                             ("language", "Languages", "violet"), ("production_style", "Production styles", ""))):
        t = pat[(pat["dimension"] == dim) & (pat["signal"] == "pattern")].sort_values("weighted_share", ascending=False)
        if not t.empty:
            with (c1 if i % 2 == 0 else c2):
                chips([f"{r.value.replace('_', ' ')} · {r.weighted_share:.0%}" for r in t.head(6).itertuples()], color, label)
    if not A.empty:
        html("<div class='cg'><div class='t'>Top over-performing videos</div></div>")
        top = A.sort_values("market_score", ascending=False).head(6)
        cols = st.columns(6)
        for col, r in zip(cols, top.itertuples()):
            with col:
                if r.platform == "youtube":
                    st.image(f"https://i.ytimg.com/vi/{r.video_id}/mqdefault.jpg")
                st.caption(f"[{r.title[:44]}]({r.url})  \n{r.views:,} views · **{r.outlier_score:.1f}×**")
    with st.expander("Market playbook"):
        st.markdown(pb.narrative)
    st.page_link(st.session_state["_pages"]["market-research"], label="All research details →")


def _understand_view(a: db.App) -> None:
    u = db.latest_understanding(a.id)
    if not u:
        st.caption("Not run yet.")
        return
    c1, c2 = st.columns(2, gap="large")
    with c1:
        chips([f"{p['name']} · {p.get('age_range', '')}" for p in u.personas], "violet", "Who it's for")
        chips([p["pain"] for p in u.pain_points], "red", "Their pains")
        chips([m["moment"] for m in u.india_moments], "amber", "Moments this season")
    with c2:
        chips([m["message"] for m in u.key_messages], "green", "What we tell them")
        chips(u.user_phrases[:10], "", "How they talk")
        chips(u.loved_features, "blue", "Loved features")


CREW_STEPS = ["director", "assets", "editor", "review", "qc", "owner"]
MODE_NAME = {"faceless": "Faceless", "images_vo": "Images + voice", "character": "Character", "ai_character": "Talking presenter"}


def _video_status(c: Any, run: Any, b: Any) -> tuple[str, str, int]:
    """(label, pill class, crew steps done) for one video in the batch."""
    if run is None:
        return ("Starting", "run", 0) if b.current_creative == c.id else ("Queued", "q", 0)
    step = CREW_STEPS.index(run.stage) if run.stage in CREW_STEPS else 0
    return {"awaiting_owner": ("Ready for you", "you", 6), "approved": ("Approved", "ok", 6),
            "failed": ("Failed", "bad", step), "rejected": ("Sent back", "wait", step),
            "waiting_assets": ("Making AI shots" if b.current_creative == c.id else "Waiting for assets",
                               "wait", 1)}.get(run.status, (run.stage.title(), "run", step))


def _video_name(a: db.App, c: Any, version: int) -> str:
    return f"{a.id}_{c.id}_{c.language}_{c.visual_mode}_v{version}.mp4"


def _zip_batch(a: db.App, items: list[tuple[Any, Any]]) -> bytes:
    """The batch's ready videos + each one's caption.txt (post caption, hashtags, tracking link)."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for c, run in items:
            path = Path(run.versions[-1]["path"])
            name = _video_name(a, c, run.version)
            z.write(path, name)
            if (cap := path.with_name("caption.txt")).exists():
                z.write(cap, name.replace(".mp4", "_caption.txt"))
    return buf.getvalue()


def _production_board(a: db.App, b: Any) -> None:
    from engine import batch as bt
    from engine.production import pipeline as crew
    from engine.production.common import tasks as crew_tasks

    vids = [c for c in bt.creatives_of(b) if c.status != "rejected"]
    runs = {c.id: _run_of(c.id) for c in vids}
    st_of = {c.id: _video_status(c, runs[c.id], b) for c in vids}
    ready = sum(1 for c in vids if st_of[c.id][1] in ("you", "ok"))
    spent = sum(sum(q.get("credits", 0) for q in (runs[c.id].asset_requests or []) if q.get("status") == "done")
                for c in vids if runs[c.id])
    stats([("Videos ready", f"{ready} / {len(vids)}", f"batch #{b.id} · {b.status}"),
           ("Waiting for you", sum(1 for c in vids if st_of[c.id][1] == "you"), "approve or send back"),
           ("Approved", sum(1 for c in vids if st_of[c.id][1] == "ok"), "final cuts"),
           ("Magnific credits", f"{spent:,}", f"of {b.credits_approved:,} approved")])
    st.progress(ready / len(vids) if vids else 0.0)
    busy = progress.is_running(a.id) or is_busy(a.id)
    if b.status in ("stopped", "done") and ready < len(vids) and not busy:
        if st.button("Resume production", type="primary"):
            if launch(a.id, ["batch", "run", str(b.id)]):
                st.rerun()

    f = st.columns([3, 2, 2, 2, 2])
    ready_files = [(c, runs[c.id]) for c in vids if st_of[c.id][1] in ("you", "ok") and runs[c.id].versions
                   and Path(runs[c.id].versions[-1]["path"]).exists()]
    f[4].download_button(f"Download all ({len(ready_files)})", (lambda: _zip_batch(a, ready_files)),
                         icon=":material/folder_zip:", file_name=f"{a.id}_batch{b.id}.zip", mime="application/zip",
                         key=f"zip{b.id}", disabled=not ready_files, on_click="ignore", use_container_width=True,
                         help="Every ready video with its post caption + hashtags, as one .zip")
    q = f[0].text_input("Search", placeholder="Search hook text or #id", key="board_q", label_visibility="collapsed")
    fs = f[1].multiselect("Status", sorted({v[0] for v in st_of.values()}), key="board_status",
                          placeholder="Any status", label_visibility="collapsed")
    fl = f[2].multiselect("Language", sorted({c.language for c in vids}), format_func=lambda l: languages()[l]["name"],
                          key="board_lang", placeholder="Any language", label_visibility="collapsed")
    fm = f[3].multiselect("Type", sorted({c.visual_mode for c in vids}), format_func=lambda m: MODE_NAME.get(m, m),
                          key="board_mode", placeholder="Any video type", label_visibility="collapsed")
    shown = [c for c in vids
             if (not q or q.lower().lstrip("#") in f"{c.id} {c.hook}".lower())
             and (not fs or st_of[c.id][0] in fs) and (not fl or c.language in fl) and (not fm or c.visual_mode in fm)]
    if not shown:
        st.caption("No videos match these filters.")
    for i in range(0, len(shown), 3):
        cols = st.columns(3, gap="medium")
        for col, c in zip(cols, shown[i:i + 3]):
            run, (label, cls, n_done) = runs[c.id], st_of[c.id]
            with col, st.container(key=f"card_vid_{c.id}"):
                v = (run.versions or [])[-1] if run and run.versions else None
                has_video = bool(v and Path(v["path"]).exists())
                top = st.columns([6, 1], vertical_alignment="center")
                top[0].markdown(f"<div class='vhead'><span class='pill {cls}'>{label}</span><span class='vid'>#{c.id}</span>"
                                f"<span class='tag'>{languages()[c.language]['name']}</span>"
                                f"<span class='tag'>{MODE_NAME.get(c.visual_mode, c.visual_mode)}</span></div>",
                                unsafe_allow_html=True)
                if has_video:  # download sits with the video's identity, always visible
                    top[1].download_button("", (lambda p=v["path"]: Path(p).read_bytes()), icon=":material/download:",
                                           file_name=_video_name(a, c, run.version), mime="video/mp4",
                                           key=f"dl{c.id}_{run.version}", help=f"Download #{c.id} (v{run.version})",
                                           on_click="ignore", type="tertiary")
                html("<div class='mini'>" + "".join(
                    f"<span class='{'d' if j < n_done else ('n' if j == n_done and cls in ('run', 'wait') else '')}' "
                    f"title='{s}'></span>" for j, s in enumerate(CREW_STEPS)) + "</div>")
                if has_video:
                    st.video(v["path"])
                else:
                    html(f"<div class='empty'>{label}…</div>")
                html(f"<div class='hook'>“{_esc(c.hook)}”</div>")
                if run:
                    used = sum(x.get("credits", 0) for x in run.asset_requests or [] if x.get("status") == "done")
                    html(f"<div class='vfoot'><span>Version <b>v{run.version}</b></span>"
                         f"<span>QC <b>{run.qc_score or 0:.0f}</b></span><span><b>{used:,}</b> credits</span></div>")
                if has_video and run.status == "awaiting_owner":
                    if st.button("Approve", key=f"okv{c.id}", type="primary", icon=":material/check:",
                                 use_container_width=True):
                        crew.approve(run.id, "")
                        st.rerun()
                if run and run.status == "awaiting_owner":
                    with st.expander("Send back or see review notes"):
                        for t in [t for t in crew_tasks(run.id) if t.version == run.version][:6]:
                            st.markdown(f"{SEV_ICON.get(t.severity, '')} {t.issue}")
                        note = st.text_area("What should change?", key=f"nb{c.id}", placeholder="e.g. use a rupee "
                                            "envelope shot in scene 2")
                        if st.button("Send back to the crew", key=f"sb{c.id}", disabled=not note.strip() or busy):
                            if launch(a.id, ["crew", "reject", str(run.id), "--note", note]):
                                st.rerun()
                        if busy:
                            st.caption("Available when the batch finishes.")
    st.page_link(st.session_state["_pages"]["crew"], label="Full crew log per video →")


@st.fragment(run_every="6s")
def _live_board(a: db.App, b: Any) -> None:
    from engine import batch as bt

    _production_board(a, bt.get(b.id))


def page_engine() -> None:
    from engine import batch as bt

    if st.session_state.get("flow_new"):
        _new_app()
        return
    if st.query_params.get("app"):
        st.session_state["flow_open"] = True     # a shared link / refresh opens its app directly
    a = db.get_app(app_id()) if app_id() else None
    if a is None or not st.session_state.get("flow_open"):
        _pick_app()
        return
    h = st.columns([8, 2])
    icon = _img64(a.icon_path)
    pic = f"<img src='{icon}'>" if icon else ""
    h[0].markdown(f"<div class='apphead'>{pic}<div><div class='eyebrow'>{_esc(a.category)}</div><h2>{_esc(a.name)}</h2>"
                  f"<p>{_esc((a.brief or {}).get('one_liner', ''))}</p></div></div>", unsafe_allow_html=True)
    if h[1].button("← All apps", use_container_width=True):
        st.session_state["flow_open"] = False
        st.query_params.clear()
        st.rerun()
    state, b = _flow_state(a)
    _flow_bar(state)
    running, busy = progress.is_running(a.id), progress.is_running(a.id) or is_busy(a.id)

    # the one button that moves the engine forward
    can_go = b is None or (b.status in ("done", "stopped") and all(
        (r := _run_of(c.id)) and r.status in ("awaiting_owner", "approved", "failed")
        for c in bt.creatives_of(b) if c.status != "rejected"))
    if can_go:
        with st.container(key="cardhero_go"):
            g = st.columns([4, 1.3], vertical_alignment="center")
            nxt = "next" if b else "first"
            g[0].markdown(f"<div class='eyebrow'>Next step</div><h3 style='margin:.2rem 0 .3rem'>Make the {nxt} 10 videos"
                          "</h3><div style='color:var(--muted)'>Market research → understanding → 10 scripts. "
                          "Nothing is filmed or paid for until you approve the scripts.</div>", unsafe_allow_html=True)
            if g[1].button("Go  →", type="primary", use_container_width=True, disabled=busy):
                if launch(a.id, ["go", a.id] + (["--force-research"] if st.session_state.get("go_fresh") else [])):
                    st.rerun()
            g[1].checkbox("Fresh research", key="go_fresh", help="Ignore this week's cached research (costs Gemini/Apify).")
    if running:
        progress_panel(a.id)
    elif is_busy(a.id):
        st.info("The engine is working on this app in another process.")

    section(2, "Market research", state["research"][1])
    with st.expander("What's working in the market", expanded=state["research"][0] == "now"):
        _research_view(a)
    section(3, "Understanding", state["understand"][1])
    with st.expander("Who the app is for and what we tell them", expanded=False):
        _understand_view(a)
    if b is not None and b.status == "scripts_ready":
        section(4, "Scripts & approval", "edit, reject, then approve")
        _scripts_review(a, b, busy)
    elif b is not None and b.status != "building":
        section(4, "Scripts & approval", state["scripts"][1])
        with st.expander(f"Batch #{b.id} scripts", expanded=False):
            for c in bt.creatives_of(b):
                st.markdown(f"{'~~' if c.status == 'rejected' else ''}**#{c.id}** · {languages()[c.language]['name']} · "
                            f"{MODE_NAME.get(c.visual_mode, c.visual_mode)} — {c.hook}"
                            f"{'~~' if c.status == 'rejected' else ''}")
        section(5, "Production", state["production"][1])
        if b.status == "processing" or running:
            _live_board(a, b)
        else:
            _production_board(a, b)
    html("<div style='height:24px'></div>")
    with st.expander("Characters and app brief"):
        _persona_panel(a)
        brief_editor(a.brief or {}, "flow_edit_brief")
        st.divider()
        if st.button("Delete this app", icon=":material/delete:"):
            _confirm_delete(a)


engine_page = st.Page(page_engine, title="Engine", icon=":material/rocket_launch:", default=True)
pages = [
    st.Page(page_home, title="Demo loop", icon=":material/all_inclusive:", url_path="home"),
    st.Page(page_brief, title="Brief", icon=":material/description:", url_path="brief"),
    st.Page(page_research, title="Market Research", icon=":material/travel_explore:", url_path="market-research"),
    st.Page(page_understand, title="Understand", icon=":material/psychology:", url_path="understand"),
    st.Page(page_batch, title="Scripts & Approval", icon=":material/fact_check:", url_path="scripts"),
    st.Page(page_generate, title="Generate", icon=":material/edit_note:", url_path="generate"),
    st.Page(page_ideas, title="Video Ideas", icon=":material/lightbulb:", url_path="ideas"),
    st.Page(page_studio, title="Studio", icon=":material/movie:", url_path="studio"),
    st.Page(page_crew, title="Production Crew", icon=":material/groups:", url_path="crew"),
    st.Page(page_performance, title="Performance", icon=":material/monitoring:", url_path="performance"),
    st.Page(page_insights, title="Insights", icon=":material/insights:", url_path="insights"),
    st.Page(page_loop, title="Growth Loop", icon=":material/autorenew:", url_path="growth-loop"),
    st.Page(page_economics, title="Economics", icon=":material/payments:", url_path="economics"),
]
st.session_state["_pages"] = {p.url_path: p for p in pages} | {"engine": engine_page}
nav = st.navigation({"": [engine_page], "Advanced": pages})
st.markdown(STYLE, unsafe_allow_html=True)
sidebar()
nav.run()
