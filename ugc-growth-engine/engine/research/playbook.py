"""Step 6 — Market Playbook: computed facts + an LLM narrative that may only use numbers from the facts."""
from __future__ import annotations

import re
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from engine import llm
from engine.insights_perf import _allowed_numbers, numbers_ok
from engine.research.patterns import tokens
from engine.settings import creator_styles, formats, languages, log, taxonomy

BLANK = re.compile(r"_{2,}|\[[^\]]+\]|<[^>]+>|…|\.\.\.")


class Recipe(BaseModel):
    name: str = ""
    format: str = ""
    hook_type: str = ""
    language: str = ""
    creator_style: str = ""
    evidence: str = ""
    example_urls: list[str] = Field(default_factory=list)


class PlaybookOut(BaseModel):
    recipes: list[Recipe] = Field(default_factory=list)
    hook_templates: list[str] = Field(default_factory=list)
    topic_angles: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    narrative: str = ""


# ---------------------------------------------------------------- facts (pre-formatted numbers)

def fmt_row(r: dict[str, Any]) -> dict[str, Any]:
    return {"value": r["value"], "videos": int(r["videos"]), "share": f"{r['share'] * 100:.0f}%",
            "weighted_share": f"{r['weighted_share'] * 100:.0f}%", "avg_outlier": f"{r['avg_outlier']:.1f}×",
            "lift": f"{r['lift'] * 100:+.0f}%", "signal": r["signal"]}


def facts(tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
    return {
        "patterns": {d: [fmt_row(r) for r in t.head(6).to_dict("records")] for d, t in tables.items()
                     if d not in ("combo", "topic_angle") and not t.empty},
        "combos": [dict(fmt_row(r), examples=[e["url"] for e in r["examples"]]) for r in tables["combo"].head(6).to_dict("records")]
        if "combo" in tables else [],
        "topics": [fmt_row(r) for r in tables["topic_angle"].head(8).to_dict("records")] if "topic_angle" in tables else [],
    }


def top_demand(kw: pd.DataFrame, n: int = 5) -> list[dict[str, Any]]:
    if kw.empty:
        return []
    t = kw.sort_values("demand_score", ascending=False).head(n)
    return [{"keyword": r.text, "language": r.language, "demand_score": round(float(r.demand_score), 2)}
            for r in t.itertuples()]


def avoid_list(tables: dict[str, pd.DataFrame]) -> list[str]:
    """Common in the sample (≥ 10%) but under-performing (lift ≤ -20%) and backed by enough videos."""
    out = []
    for d, t in tables.items():
        if d in ("combo", "topic_angle") or t.empty:
            continue
        for r in t.to_dict("records"):
            if r["signal"] == "pattern" and r["share"] >= 0.10 and r["lift"] <= -0.20 and r["value"] not in ("other", "no"):
                f = fmt_row(r)
                out.append(f"{d} = {r['value']}: {f['share']} of videos but {f['lift']} vs average outlier")
    return out[:6]


def gaps(kw: pd.DataFrame, videos: pd.DataFrame, n: int = 5) -> list[dict[str, Any]]:
    """High-demand searched keywords that surfaced few strong relevant videos (content opportunities)."""
    if kw.empty or videos.empty:
        return []
    searched = kw[kw["searched"]]
    if searched.empty:
        return []
    cut = searched["demand_score"].median()
    strong = videos[(videos["relevant"]) & (videos["outlier_score"] >= 1)]
    out = []
    for r in searched[searched["demand_score"] >= cut].sort_values("demand_score", ascending=False).itertuples():
        good = int(strong["keywords"].map(lambda ks: r.text in ks).sum())
        if good <= 2:
            out.append({"keyword": r.text, "demand_score": round(float(r.demand_score), 2), "strong_videos": good})
    return out[:n]


# ---------------------------------------------------------------- guards

def copies_market_hook(text: str, hooks: list[str], threshold: float = 0.6, ignore: str = "") -> bool:
    """True if `text` shares > threshold of the tokens of any stored market hook (copy detection).
    Words in `ignore` (our own app/brand name) don't count — naming our product isn't copying anyone."""
    skip = tokens(ignore)
    t = tokens(text) - skip
    if len(t) < 3:
        return False  # too short to be a meaningful copy
    for h in hooks:
        ht = tokens(h) - skip
        if len(ht) >= 3 and len(t & ht) / min(len(t), len(ht)) > threshold:
            return True
    return False


def _coerce(v: str, vals: list[str], default: str) -> str:
    v = re.sub(r"[\s\-/]+", "_", (v or "").strip().lower())
    return v if v in vals else default


def _mock(tables: dict[str, pd.DataFrame], ex_urls: list[str]) -> dict[str, Any]:
    def best(d: str, default: str) -> str:
        t = tables.get(d, pd.DataFrame())
        t = t[(t["signal"] == "pattern") & (t["value"] != "other")] if not t.empty else t
        return t.iloc[0]["value"] if not t.empty else default
    recs = []
    for i, (fmt, hk) in enumerate([(best("format", "pov"), best("hook_type", "question")), ("pov", "relatable_pain"),
                                   ("three_reasons", "number_list")]):
        recs.append({"name": f"Recipe {i + 1}", "format": fmt, "hook_type": hk, "language": best("language", "hinglish"),
                     "creator_style": best("closest_creator_style", "bestie_narrator"),
                     "evidence": "see pattern table", "example_urls": ex_urls[i * 2:i * 2 + 2]})
    return {"recipes": recs,
            "hook_templates": ["___ aate hi ___ khatam?", "Nobody tells you this about ___", "3 ___ jo ___ kha jaate hain",
                               "POV: ___ aur ___ khaali", "___ se pehle ye dekh lo", "Kya aap bhi ___ karte ho?",
                               "___ vs ___: kaunsa better?", "Ek chhoti si ___ = bada ___", "___ ka sach", "___ mat karna jab tak ___"],
            "topic_angles": list(tables["topic_angle"]["value"].head(5)) if "topic_angle" in tables else [],
            "messages": ["track small spends", "plan on salary day", "set a budget", "split bills fairly", "know where money goes"],
            "narrative": "Mock playbook (offline dry run) built from the pattern table."}


# ---------------------------------------------------------------- build

def build(app_name: str, app_desc: str, tables: dict[str, pd.DataFrame], A: pd.DataFrame, kw: pd.DataFrame,
          videos: pd.DataFrame, app_id: str) -> tuple[dict[str, Any], str]:
    """Return (playbook JSON, narrative markdown)."""
    f = facts(tables)
    ex = A.sort_values("market_score", ascending=False).head(8)
    examples = [{"url": r.url, "title": r.title[:90], "views": int(r.views), "outlier": f"{r.outlier_score:.1f}×",
                 "hook_first_3s": r.hook_first_3s, "format": r.format, "hook_type": r.hook_type,
                 "language": r.language, "why_it_worked": r.why_it_worked} for r in ex.itertuples()]
    urls = set(A["url"])
    prompt = llm.render_prompt(
        "research_playbook", app=f"{app_name}: {app_desc}", n_videos=str(len(A)), min_videos="3",
        patterns=f["patterns"], combos=f["combos"], topics=f["topics"], examples=examples,
        formats=list(formats()), hook_types=list(taxonomy()["hook_types"]), languages=list(languages()),
        creator_styles=list(creator_styles()),
    )
    try:
        out: PlaybookOut = llm.complete_json("research_playbook", prompt, PlaybookOut, temperature=0.5, app_id=app_id,
                                             mock_payload=lambda: _mock(tables, list(ex["url"])))
    except llm.LLMError as e:
        log.warning(f"playbook LLM unavailable ({e}); using computed playbook only")
        out = PlaybookOut.model_validate(_mock(tables, list(ex["url"])))

    allowed_nums = _allowed_numbers(f) | _allowed_numbers(top_demand(kw)) | {3.0}
    hooks = [h for h in A["hook_first_3s"].tolist() if h]
    dropped = 0
    recipes = []
    for r in out.recipes[:3]:
        r.format = _coerce(r.format, list(formats()), "pov")
        r.hook_type = _coerce(r.hook_type, list(taxonomy()["hook_types"]), "question")
        r.language = _coerce(r.language, list(languages()), "hinglish")
        r.creator_style = _coerce(r.creator_style, list(creator_styles()), "bestie_narrator")
        if not numbers_ok(r.evidence, allowed_nums) or r.evidence in ("", "see pattern table"):
            dropped += 1 if r.evidence not in ("", "see pattern table") else 0
            r.evidence = computed_evidence(tables, r)
        r.example_urls = [u for u in r.example_urls if u in urls][:2] or example_urls_for(tables, r)
        recipes.append(r.model_dump())
    templates = []
    for t in out.hook_templates:
        if copies_market_hook(t, hooks, 0.6) or not BLANK.search(t):
            dropped += 1
            continue
        templates.append(t.strip())
    narrative_lines = [s for s in re.split(r"(?<=[.!?])\s+", out.narrative.strip()) if s]
    kept = [s for s in narrative_lines if numbers_ok(s, allowed_nums)]
    dropped += len(narrative_lines) - len(kept)
    if dropped:
        log.warning(f"playbook: dropped {dropped} items (numbers not in the data, copied hooks or no blanks)")
    pb = {
        "top_demand": top_demand(kw), "recipes": recipes, "hook_templates": templates[:10],
        "topic_angles": out.topic_angles[:5], "messages": out.messages[:5], "avoid": avoid_list(tables),
        "gaps": gaps(kw, videos), "narrative": " ".join(kept), "videos_analysed": len(A),
    }
    return pb, to_markdown(pb)


def computed_evidence(tables: dict[str, pd.DataFrame], r: Recipe) -> str:
    parts = []
    for d, v in (("format", r.format), ("hook_type", r.hook_type), ("language", r.language),
                 ("closest_creator_style", r.creator_style)):
        t = tables.get(d)
        if t is None or t.empty:
            continue
        m = t[t["value"] == v]
        if not m.empty:
            f = fmt_row(m.iloc[0].to_dict())
            parts.append(f"{v}: {f['weighted_share']} weighted share, {f['avg_outlier']} avg outlier ({f['signal']})")
    return "; ".join(parts) or "no direct evidence in the sample"


def example_urls_for(tables: dict[str, pd.DataFrame], r: Recipe) -> list[str]:
    for d, v in (("format", r.format), ("hook_type", r.hook_type)):
        t = tables.get(d)
        if t is not None and not t.empty:
            m = t[t["value"] == v]
            if not m.empty:
                return [e["url"] for e in m.iloc[0]["examples"]][:2]
    return []


def to_markdown(pb: dict[str, Any]) -> str:
    lines = [f"**What works now** — from {pb['videos_analysed']} analysed Shorts.", "", pb["narrative"] or "", "",
             "**Top demand**"]
    lines += [f"- {k['keyword']} ({k['language']}) — demand {k['demand_score']}" for k in pb["top_demand"]]
    lines += ["", "**Proven recipes**"]
    for r in pb["recipes"]:
        lines.append(f"- **{r['name']}**: `{r['format']}` + `{r['hook_type']}` + `{r['language']}` + "
                     f"`{r['creator_style']}` — {r['evidence']}")
    lines += ["", "**Hook templates**"] + [f"- {t}" for t in pb["hook_templates"]]
    if pb["avoid"]:
        lines += ["", "**Avoid**"] + [f"- {a}" for a in pb["avoid"]]
    if pb["gaps"]:
        lines += ["", "**Gaps (demand with few strong videos)**"]
        lines += [f"- {g['keyword']} — demand {g['demand_score']}, {g['strong_videos']} strong videos" for g in pb["gaps"]]
    return "\n".join(lines)
