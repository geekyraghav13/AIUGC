"""Brief (+ reviews) → personas, pains, phrases, key messages, India moments."""
from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field

from engine import llm
from engine.brief import Brief, brief_for_prompt, brief_of
from engine.db import Understanding, get_app, save
from engine.settings import india_context, log


class Persona(BaseModel):
    name: str
    age_range: str = ""
    city_tier: str = ""
    goal: str = ""
    frustration: str = ""


class PainPoint(BaseModel):
    pain: str
    evidence_phrases: list[str] = Field(default_factory=list)


class KeyMessage(BaseModel):
    id: str
    message: str
    source: str = "brief"  # brief / market


class Moment(BaseModel):
    moment: str
    how_app_fits: str = ""


class UnderstandingOut(BaseModel):
    personas: list[Persona] = Field(min_length=1)
    pain_points: list[PainPoint] = Field(min_length=1)
    loved_features: list[str] = Field(default_factory=list)
    user_phrases: list[str] = Field(default_factory=list)
    key_messages: list[KeyMessage] = Field(min_length=3)
    india_moments: list[Moment] = Field(default_factory=list)


def current_moments(today: dt.date | None = None) -> dict[str, list[str]]:
    today = today or dt.date.today()
    ctx = india_context()
    months = ctx.get("months", {})
    nxt = today.month % 12 + 1
    return {
        "this_month": months.get(today.month, []),
        "next_month": months.get(nxt, []),
        "everyday_scenarios": ctx.get("everyday_scenarios", []),
    }


def _mock(b: Brief) -> dict[str, Any]:
    feats = b.key_features or [b.one_liner]
    return {
        "personas": [
            {"name": "UPI-first student", "age_range": "18-22", "city_tier": "2", "goal": b.one_liner,
             "frustration": "loses track by month-end"},
            {"name": "first-job professional", "age_range": "22-28", "city_tier": "1", "goal": "feel in control",
             "frustration": "no time for manual tracking"},
            {"name": "tier-3 aspirant", "age_range": "18-25", "city_tier": "3", "goal": "do better with less",
             "frustration": "apps feel made for metros"},
        ],
        "pain_points": [{"pain": f"Without {b.app_name}: {f.lower()} is hard", "evidence_phrases": ["(paraphrase) pata hi nahi chalta"]}
                        for f in feats[:4]],
        "loved_features": feats[:5],
        "user_phrases": ["(paraphrase) paisa kahan gaya pata hi nahi chalta", "(paraphrase) month-end pe tension",
                         "(paraphrase) simple hai, apni language mein"],
        "key_messages": [{"id": f"msg_{i+1}", "message": f[:80]} for i, f in enumerate(feats[:6])]
        + ([{"id": "msg_usp", "message": b.usp[:80]}] if b.usp else []),
        "india_moments": [{"moment": m, "how_app_fits": f"{b.app_name} helps during {m}"}
                          for m in current_moments()["this_month"][:4]],
    }


def market_block(app_id: str) -> tuple[str, dict[str, Any]]:
    """Playbook excerpt for the prompt (+ the raw playbook), or '(none)'."""
    from engine.research.market import context

    ctx = context(app_id)
    if not ctx.available:
        return "(none — no market research yet)", {}
    pb = ctx.playbook
    return (
        f"Top demand searches: {[k['keyword'] for k in pb.get('top_demand', [])]}\n"
        f"Topic angles that over-perform: {pb.get('topic_angles', [])}\n"
        f"Messages that resonate: {pb.get('messages', [])}\n"
        f"Content gaps (demand, few good videos): {[g['keyword'] for g in pb.get('gaps', [])]}",
        pb,
    )


def merge_market(out: UnderstandingOut, pb: dict[str, Any]) -> UnderstandingOut:
    """Guarantee the playbook is reflected even if the LLM ignored it: demand → pains, messages → key_messages."""
    if not pb:
        return out
    known = " ".join(p.pain.lower() + " " + " ".join(p.evidence_phrases).lower() for p in out.pain_points)
    for k in pb.get("top_demand", [])[:3]:
        if k["keyword"].lower() not in known:
            out.pain_points.append(PainPoint(pain=f"People search: “{k['keyword']}”", evidence_phrases=[k["keyword"]]))
    if not any(m.source == "market" for m in out.key_messages):
        for i, m in enumerate((pb.get("messages") or pb.get("topic_angles") or [])[:2]):
            out.key_messages.append(KeyMessage(id=f"market_{i + 1}", message=m[:100], source="market"))
    return out


def run(app_id: str) -> Understanding:
    app = get_app(app_id)
    if app is None:
        raise ValueError(f"unknown app {app_id}")
    b = brief_of(app)
    market, pb = market_block(app_id)
    prompt = llm.render_prompt(
        "understand",
        brief=brief_for_prompt(b),
        reviews="\n".join(f"- {r[:300]}" for r in b.reviews[:200]) or "(none — brief only)",
        moments=current_moments(),
        market=market,
    )
    out: UnderstandingOut = llm.complete_json("understand", prompt, UnderstandingOut, temperature=0.4,
                                              app_id=app_id, mock_payload=lambda: _mock(b))
    out = merge_market(out, pb)
    u = Understanding(
        app_id=app_id,
        personas=[p.model_dump() for p in out.personas],
        pain_points=[p.model_dump() for p in out.pain_points],
        loved_features=out.loved_features,
        user_phrases=out.user_phrases,
        key_messages=[m.model_dump() for m in out.key_messages],
        india_moments=[m.model_dump() for m in out.india_moments],
    )
    save(u)
    log.info(f"Understanding saved: {len(u.personas)} personas, {len(u.pain_points)} pains, "
             f"{len(u.key_messages)} messages")
    return u
