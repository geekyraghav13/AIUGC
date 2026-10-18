"""AI insights: statistics first (lift, Bayesian probability best), then an LLM narrates the numbers only."""
from __future__ import annotations

import re
from typing import Any, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from engine import llm
from engine.db import Insight, current_generation, get_app, save
from engine.metrics import METRIC_LABELS, TAG_COLS, metrics_frame, primary_metric_name
from engine.settings import env_int, log

DRAWS = 5000
COMBOS = [("language", "hook_type"), ("language", "creator_style"), ("hook_type", "format"),
          ("message", "language")]


def min_creatives() -> int:
    return env_int("MIN_CREATIVES", 3)


def min_views() -> int:
    return env_int("MIN_VIEWS", 500)


# ---------------------------------------------------------------- statistics

def successes_trials(df: pd.DataFrame, metric: str) -> tuple[float, float, float]:
    """Map a metric to (successes, trials, display scale) for a Beta posterior."""
    views = float(df["views"].sum())
    if metric == "installs_per_1k_views":
        return float(df["installs"].sum()), views, 1000.0
    if metric == "ctr":
        return float(df["clicks"].sum()), views, 1.0
    if metric == "cvr":
        return float(df["installs"].sum()), float(df["clicks"].sum()), 1.0
    if metric == "hook_rate":
        return float(df["views_3s"].sum()), float(df["impressions"].sum()), 1.0
    # engagement × watch: engaged views weighted by watch fraction
    eng = df[["likes", "comments", "shares", "saves"]].sum(axis=1) * df["avg_watch_pct"].fillna(0) / 100
    return float(eng.sum()), views, 1.0


def prob_best(groups: dict[Any, tuple[float, float]], draws: int = DRAWS, seed: int = 7) -> dict[Any, float]:
    """P(value has the highest rate) from Beta(1+s, 1+n-s) posteriors."""
    if not groups:
        return {}
    rng = np.random.default_rng(seed)
    keys = list(groups)
    samples = np.column_stack([rng.beta(1 + s, 1 + max(0.0, n - s), draws) for s, n in groups.values()])
    wins = np.bincount(samples.argmax(axis=1), minlength=len(keys))
    return {k: float(w / draws) for k, w in zip(keys, wins)}


def dimension_table(df: pd.DataFrame, cols: list[str], metric: str) -> pd.DataFrame:
    """Per tag value (or combination): n, views, metric, lift vs overall, probability best, status."""
    s_all, n_all, scale = successes_trials(df, metric)
    overall = s_all / n_all * scale if n_all else np.nan
    rows = []
    for key, g in df.groupby(cols):
        key = key if isinstance(key, tuple) else (key,)
        s, n, _ = successes_trials(g, metric)
        rows.append({"value": " + ".join(map(str, key)), **dict(zip(cols, key)),
                     "creatives": len(g), "views": int(g["views"].sum()),
                     "metric": s / n * scale if n else np.nan, "successes": s, "trials": n})
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    t["lift"] = t["metric"] / overall - 1 if overall else np.nan
    t["enough_data"] = (t["creatives"] >= min_creatives()) & (t["views"] >= min_views())
    eligible = t[t["enough_data"]]
    pb = prob_best({r.value: (r.successes, r.trials) for r in eligible.itertuples()})
    t["prob_best"] = t["value"].map(pb).fillna(np.nan)
    best = max(pb, key=pb.get) if pb else None

    def status(r: Any) -> str:
        if not r.enough_data:
            return "needs more data"
        if r.value == best and len(eligible) > 1 and r.prob_best >= 0.5 and r.lift > 0:
            return "winner"
        if r.lift <= -0.2 and (r.prob_best or 0) < 0.05:
            return "underperformer"
        return "ok"

    t["status"] = t.apply(status, axis=1)
    return t.sort_values(["enough_data", "metric"], ascending=[False, False]).reset_index(drop=True)


def compute(app_id: str, source: str = "all", upto_generation: Optional[int] = None) -> dict[str, Any]:
    df = metrics_frame(app_id, source=source)
    if upto_generation is not None and not df.empty:
        df = df[df["generation"] <= upto_generation]
    if df.empty:
        return {"empty": True}
    metric = primary_metric_name(df)
    s, n, scale = successes_trials(df, metric)
    dims = {d: dimension_table(df, [d], metric) for d in TAG_COLS if df[d].astype(str).str.len().gt(0).any()}
    combos = {f"{a} × {b}": dimension_table(df, [a, b], metric) for a, b in COMBOS}
    ranked = df.sort_values(metric, ascending=False)
    cols = ["creative_id", "generation", "language", "hook_type", "format", "creator_style", "hook", metric,
            "views", "thumbnail_path", "video_path"]
    return {
        "empty": False, "metric": metric, "metric_label": METRIC_LABELS.get(metric, metric),
        "overall": s / n * scale if n else np.nan, "creatives": len(df), "views": int(df["views"].sum()),
        "sources": sorted(set("/".join(df["source"]).split("/"))),
        "dimensions": dims, "combos": combos,
        "top": ranked.head(3)[cols].to_dict("records"), "bottom": ranked.tail(3)[cols].iloc[::-1].to_dict("records"),
        "min_creatives": min_creatives(), "min_views": min_views(),
    }


# ---------------------------------------------------------------- narrative

def _fmt_metric(v: float, metric: str) -> str:
    if metric in ("ctr", "cvr", "hook_rate"):
        return f"{v * 100:.2f}%"
    return f"{v:.2f}"


def facts_for_llm(stats: dict[str, Any], max_rows: int = 6) -> dict[str, Any]:
    """Pre-formatted numbers — the narrative may only use these strings."""
    metric = stats["metric"]

    def rows(t: pd.DataFrame) -> list[dict[str, Any]]:
        out = []
        for r in t.head(max_rows).itertuples():
            out.append({"value": r.value, "status": r.status, "creatives": int(r.creatives), "views": int(r.views),
                        "metric": _fmt_metric(r.metric, metric),
                        "vs_average": f"{r.lift + 1:.1f}×" if pd.notna(r.lift) else "n/a",
                        "lift": f"{r.lift * 100:+.0f}%" if pd.notna(r.lift) else "n/a",
                        "prob_best": f"{r.prob_best * 100:.0f}%" if pd.notna(r.prob_best) else "n/a"})
        return out

    return {
        "overall_metric": _fmt_metric(stats["overall"], metric), "creatives_tested": stats["creatives"],
        "total_views": stats["views"], "min_sample": f"{stats['min_creatives']} creatives and {stats['min_views']} views",
        "by_dimension": {d: rows(t) for d, t in stats["dimensions"].items() if not t.empty},
        "top_combinations": {k: rows(t[t["enough_data"]]) for k, t in stats["combos"].items() if not t.empty},
        "top_creatives": [{"hook": r["hook"], "language": r["language"], "hook_type": r["hook_type"],
                           "metric": _fmt_metric(r[metric], metric)} for r in stats["top"]],
        "bottom_creatives": [{"hook": r["hook"], "language": r["language"], "hook_type": r["hook_type"],
                              "metric": _fmt_metric(r[metric], metric)} for r in stats["bottom"]],
    }


_NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?")


def _allowed_numbers(facts: Any) -> set[float]:
    found: set[float] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                walk(k)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        else:
            for m in _NUM.findall(str(x)):
                try:
                    found.add(abs(float(m.replace(",", ""))))
                except ValueError:
                    pass
    walk(facts)
    return found


def numbers_ok(text: str, allowed: set[float]) -> bool:
    """True if every number in `text` appears in the computed facts (small ordinals allowed)."""
    for m in _NUM.findall(text):
        v = abs(float(m.replace(",", "")))
        if v <= 5 and float(v).is_integer():
            continue  # "3 creatives", "top 3", ordinals
        if not any(abs(v - a) <= 0.011 * max(1.0, a) for a in allowed):
            return False
    return True


class Narrative(BaseModel):
    findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    stop_doing: list[str] = Field(default_factory=list)


def template_narrative(facts: dict[str, Any]) -> Narrative:
    """Deterministic narrative from the facts (mock provider + fallback)."""
    findings, recs, stops = [], [], []
    for dim, rows in facts["by_dimension"].items():
        for r in rows:
            if r["status"] == "winner":
                findings.append(f"{dim}: “{r['value']}” leads with {r['metric']} ({r['vs_average']} the average, "
                                f"{r['prob_best']} probability best, {r['creatives']} creatives).")
                recs.append(f"Put more of the next batch into {dim} = {r['value']}.")
            elif r["status"] == "underperformer":
                stops.append(f"Deprioritise {dim} = {r['value']} ({r['lift']} vs average).")
    for combo, rows in facts["top_combinations"].items():
        if rows and rows[0]["status"] == "winner":
            findings.append(f"Best {combo}: {rows[0]['value']} at {rows[0]['metric']} ({rows[0]['prob_best']} probability best).")
    if not findings:
        findings.append(f"No clear winner yet — most tag values need more data (minimum {facts['min_sample']}).")
    if not recs:
        recs.append("Keep exploring: test more creatives per language and hook type before exploiting.")
    return Narrative(findings=findings[:5], recommendations=recs[:3], stop_doing=stops[:2])


def narrate(app_id: str, stats: dict[str, Any], simulated: bool) -> Narrative:
    facts = facts_for_llm(stats)
    allowed = _allowed_numbers(facts)
    app = get_app(app_id)
    prompt = llm.render_prompt("insights", app_name=app.name if app else app_id, facts=facts,
                               metric_label=stats["metric_label"],
                               source_label="SIMULATED test market" if simulated else "real platform data")
    try:
        out: Narrative = llm.complete_json("insights", prompt, Narrative, temperature=0.3, app_id=app_id,
                                           mock_payload=lambda: template_narrative(facts).model_dump())
    except llm.LLMError as e:
        log.warning(f"LLM narrative unavailable ({e}); using template")
        return template_narrative(facts)
    clean = Narrative(
        findings=[f for f in out.findings if numbers_ok(f, allowed)],
        recommendations=[r for r in out.recommendations if numbers_ok(r, allowed)],
        stop_doing=[s for s in out.stop_doing if numbers_ok(s, allowed)],
    )
    dropped = len(out.findings) + len(out.recommendations) + len(out.stop_doing) - (
        len(clean.findings) + len(clean.recommendations) + len(clean.stop_doing))
    if dropped:
        log.warning(f"dropped {dropped} narrative lines citing numbers not in the computed stats")
    if not clean.findings:
        clean.findings = template_narrative(facts).findings
    return clean


def to_markdown(n: Narrative, simulated: bool) -> str:
    head = "**SIMULATED data** — demonstrates the loop, not real audience behaviour.\n\n" if simulated else ""
    return (head + "**Findings**\n" + "\n".join(f"- {x}" for x in n.findings)
            + "\n\n**Recommendations**\n" + "\n".join(f"- {x}" for x in n.recommendations)
            + ("\n\n**Stop doing**\n" + "\n".join(f"- {x}" for x in n.stop_doing) if n.stop_doing else ""))


def market_vs_us(app_id: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare market-proven patterns (D8) with our own winners: agree / disagree / untested."""
    from engine.research.market import PRIOR_DIMS, context

    ctx = context(app_id)
    if not ctx.available:
        return []
    out = []
    for d in PRIOR_DIMS:
        m_best = ctx.best(d)
        if m_best is None:
            continue
        t = stats["dimensions"].get(d, pd.DataFrame())
        ours = t[t["status"] == "winner"]["value"].tolist() if not t.empty else []
        tested = t[(t["value"] == m_best) & (t["enough_data"])] if not t.empty else t
        if tested is None or tested.empty:
            verdict, detail = "untested", "we don't have enough data on the market favourite yet"
        elif ours and ours[0] == m_best:
            verdict, detail = "agree", "the market favourite is also our winner"
        elif ours:
            verdict, detail = "disagree", f"our winner is {ours[0]}"
        else:
            r = tested.iloc[0]
            verdict = "agree" if r["lift"] > 0 else "disagree"
            detail = f"no clear winner yet; market favourite is {r['lift'] * 100:+.0f}% vs our average"
        out.append({"dimension": d, "market_favourite": m_best, "our_winner": ours[0] if ours else None,
                    "verdict": verdict, "detail": detail})
    return out


def _serialise(stats: dict[str, Any]) -> dict[str, Any]:
    def table(t: pd.DataFrame) -> list[dict]:
        return t.replace({np.nan: None}).to_dict("records") if not t.empty else []
    return {
        "metric": stats["metric"], "metric_label": stats["metric_label"], "overall": stats["overall"],
        "creatives": stats["creatives"], "views": stats["views"], "sources": stats["sources"],
        "dimensions": {d: table(t) for d, t in stats["dimensions"].items()},
        "combos": {k: table(t) for k, t in stats["combos"].items()},
        "top": stats["top"], "bottom": stats["bottom"],
    }


def run(app_id: str, generation: Optional[int] = None, source: str = "all") -> Insight:
    gen = generation or current_generation(app_id)
    stats = compute(app_id, source=source, upto_generation=gen)
    if stats.get("empty"):
        ins = Insight(app_id=app_id, generation=gen, findings={}, narrative="No performance data yet.")
        save(ins)
        return ins
    simulated = "simulated" in stats["sources"]
    nar = narrate(app_id, stats, simulated)
    findings = _serialise(stats)
    findings.update(nar.model_dump())
    findings["simulated"] = simulated
    findings["market_vs_us"] = mvu = market_vs_us(app_id, stats)
    md = to_markdown(nar, simulated)
    if mvu:
        md += "\n\n**Market vs. us**\n" + "\n".join(
            f"- {r['dimension']}: market favours `{r['market_favourite']}` → **{r['verdict']}** ({r['detail']})" for r in mvu)
    ins = Insight(app_id=app_id, generation=gen, findings=findings, narrative=md)
    save(ins)
    log.info(f"insights saved for {app_id} gen {gen}: {len(nar.findings)} findings")
    return ins


def latest(app_id: str) -> Optional[Insight]:
    from sqlmodel import select

    from engine.db import session

    with session() as s:
        return s.exec(select(Insight).where(Insight.app_id == app_id).order_by(Insight.id.desc())).first()


