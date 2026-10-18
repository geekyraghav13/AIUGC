"""Understanding → UGC concepts (persona × pain × message × India scenario × format)."""
from __future__ import annotations

import math
import random
from typing import Any, Optional

from pydantic import BaseModel, Field

from engine import llm
from engine.brief import brief_for_prompt, brief_of
from engine.db import Concept, Understanding, get_app, latest_understanding, save
from engine.settings import formats, log
from engine.understand import current_moments


class ConceptOut(BaseModel):
    title: str
    persona: str = ""
    pain_point: str = ""
    message_id: str = ""
    india_scenario: str = ""
    format: str = ""
    market_evidence: list[int] = Field(default_factory=list)


class ConceptsOut(BaseModel):
    concepts: list[ConceptOut] = Field(min_length=1)


def understanding_for_prompt(u: Understanding) -> dict[str, Any]:
    return {
        "personas": u.personas, "pain_points": u.pain_points, "loved_features": u.loved_features,
        "user_phrases": u.user_phrases, "key_messages": u.key_messages, "india_moments": u.india_moments,
    }


def _mock(u: Understanding, n: int, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    fmts = list(formats())
    scen = current_moments()["everyday_scenarios"]
    out = []
    for i in range(n):
        p = u.personas[i % len(u.personas)]
        m = u.key_messages[i % len(u.key_messages)]
        pain = u.pain_points[i % len(u.pain_points)]
        s = scen[(i * 3 + seed) % len(scen)]
        out.append({"title": f"{s.capitalize()} × {m['id']}", "persona": p["name"], "pain_point": pain["pain"],
                    "message_id": m["id"], "india_scenario": s, "format": fmts[(i + rng.randint(0, 2)) % len(fmts)]})
    return {"concepts": out}


def _coerce(c: ConceptOut, u: Understanding, i: int) -> ConceptOut:
    """Snap LLM output onto allowed values so every creative is fully tagged."""
    persona_names = [p["name"] for p in u.personas]
    msg_ids = [m["id"] for m in u.key_messages]
    fmts = list(formats())
    if c.persona not in persona_names:
        c.persona = next((p for p in persona_names if p.lower() in c.persona.lower() or c.persona.lower() in p.lower()),
                         persona_names[i % len(persona_names)])
    if c.message_id not in msg_ids:
        c.message_id = msg_ids[i % len(msg_ids)]
    if c.format not in fmts:
        c.format = fmts[i % len(fmts)]
    return c


def enforce_market_share(concepts: list[ConceptOut], proven: list[dict[str, Any]], share: float) -> None:
    """Keep only real evidence ids; if fewer than `share` of concepts build on a proven pattern, re-base the
    remaining ones on proven formats/recipes (in order) so Gen 1 starts from what is working."""
    valid = {i for p in proven for i in p["ids"]}
    for c in concepts:
        c.market_evidence = [i for i in c.market_evidence if i in valid]
    need = math.ceil(share * len(concepts)) - sum(1 for c in concepts if c.market_evidence)
    shapeable = [p for p in proven if p.get("format") in formats() and p["ids"]]
    if need <= 0 or not shapeable:
        return
    for j, c in enumerate([c for c in concepts if not c.market_evidence][:need]):
        p = shapeable[j % len(shapeable)]
        c.format = p["format"]
        c.market_evidence = list(p["ids"])


def run(app_id: str, n: int = 10, generation: int = 1, guidance: str = "",
        persist: bool = True) -> list[Concept]:
    app = get_app(app_id)
    u = latest_understanding(app_id)
    if app is None or u is None:
        raise ValueError("run `understand` first")
    b = brief_of(app)
    from engine.research import cfg as research_cfg
    from engine.research.market import context

    ctx = context(app_id)
    proven = ctx.proven() if ctx.available else []
    share = float(research_cfg()["proven_concept_share"]) if proven and generation == 1 else 0.0
    prompt = llm.render_prompt(
        "concepts", brief=brief_for_prompt(b), understanding=understanding_for_prompt(u),
        proven=proven or "(none)", n_proven=str(math.ceil(share * n)),
        formats={k: v["structure"] for k, v in formats().items()}, moments=current_moments(),
        guidance=guidance or "(none — first generation)", n=str(n),
        claims_to_avoid=", ".join(b.claims_to_avoid) or "(none)",
    )
    out: ConceptsOut = llm.complete_json(f"concepts_g{generation}", prompt, ConceptsOut, temperature=0.9,
                                         app_id=app_id, mock_payload=lambda: _mock(u, n, generation))
    cs = [_coerce(c, u, i) for i, c in enumerate(out.concepts[:n])]
    if proven:
        enforce_market_share(cs, proven, share)
    rows = [Concept(app_id=app_id, generation=generation, title=c.title[:200], persona=c.persona,
                    pain_point=c.pain_point, message=c.message_id, india_scenario=c.india_scenario,
                    format=c.format, market_evidence=c.market_evidence) for c in cs]
    if persist:
        save(*rows)
    log.info(f"{len(rows)} concepts for {app_id} (gen {generation})")
    return rows


def message_text(u: Optional[Understanding], message_id: str) -> str:
    if u is None:
        return message_id
    return next((m["message"] for m in u.key_messages if m["id"] == message_id), message_id)
