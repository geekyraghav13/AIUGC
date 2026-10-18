"""Safety + disclosure checks: rule-based first, then an LLM review. Flagged items need manual approval."""
from __future__ import annotations

import re
from typing import Any, Iterable

from pydantic import BaseModel, Field

from engine import llm
from engine.brief import Brief
from engine.db import Creative, save
from engine.localize import script_texts
from engine.settings import log

# (pattern, issue). Case-insensitive; covers English, Hinglish and common Hindi phrasing.
RULES: list[tuple[str, str]] = [
    # fake testimonials
    (r"\b(i am|i'm|im|main|mera naam|my name is)\s+[A-Z]?[a-z]+\s+(from|se)\s+[A-Z]?[a-z]+", "fake testimonial: named person from a city"),
    (r"मेरा नाम\s+\S+", "fake testimonial: named person"),
    (r"changed my life|life badal (di|gayi)|zindagi badal|ज़िंदगी बदल|जिंदगी बदल", "fake testimonial: life-changing claim"),
    (r"\b(i|maine|mujhe)\b.{0,30}\b(saved|earned|bachaye|kamaye|kamaaye)\b", "fake testimonial: personal result claim"),
    # guarantees
    (r"guarantee|गारंटी|100\s?%|sure[- ]?shot|pakka result|पक्का", "guaranteed outcome"),
    (r"(earn|kamao|kamaye|कमाओ|कमाएं)\s*(₹|rs\.?|inr)?\s*\d", "earning promise with an amount"),
    (r"(₹|rs\.?\s?|inr\s?)\d[\d,]*\s*(daily|per day|roz|रोज़|a day|monthly)", "earning promise with an amount"),
    # invented numbers / statistics
    (r"\b\d{1,3}(\.\d+)?\s?%", "statistic/percentage (not in brief?)"),
    (r"\b\d+(\.\d+)?\s?(lakh|crore|million|k\+|m\+)\s*(users|log|downloads|students)", "user-count statistic"),
    # health / finance promises
    (r"cure|ilaaj|इलाज|weight loss in|returns? of|double your money|paisa double|loan approv|credit score (badh|improv)",
     "health/finance promise"),
    # religious / political targeting
    (r"\b(hindu|muslim|christian|sikh|bjp|congress|modi|rahul gandhi)\b|हिंदू|मुसलमान", "religious/political reference"),
]
_COMPILED = [(re.compile(p, re.I), msg) for p, msg in RULES]
_NUM = re.compile(r"\d[\d,.]*")


def rule_check(text: str, brief: Brief) -> list[str]:
    """Rule-based issues for one piece of viewer-facing text."""
    issues: list[str] = []
    facts = " ".join([brief.description, brief.one_liner, brief.usp, " ".join(brief.key_features)]).lower()
    for rx, msg in _COMPILED:
        m = rx.search(text)
        if not m:
            continue
        if msg.startswith("statistic") and m.group(0).strip() in facts:
            continue  # number comes from the brief
        issues.append(f"{msg}: “{m.group(0).strip()}”")
    for claim in brief.claims_to_avoid:
        words = [w for w in re.findall(r"\w+", claim.lower()) if len(w) > 3]
        if words and all(w in text.lower() for w in words):
            issues.append(f"claims_to_avoid: {claim}")
    return sorted(set(issues))


class Review(BaseModel):
    id: int
    # the LLM key is "pass" (a Python keyword)
    ok: bool = Field(alias="pass")
    issues: list[str] = Field(default_factory=list)


class Reviews(BaseModel):
    reviews: list[Review] = Field(default_factory=list)


def llm_check(items: list[tuple[int, dict[str, Any]]], brief: Brief, app_id: str) -> dict[int, list[str]]:
    """Batch LLM review → {creative_id: issues}. Soft-fails to {} (rules still apply)."""
    if not items:
        return {}
    payload = [{"id": cid, "text": script_texts(sc), "caption": sc.get("post_caption", "")} for cid, sc in items]
    prompt = llm.render_prompt(
        "compliance",
        claims_to_avoid=", ".join(brief.claims_to_avoid) or "(none)",
        facts={"one_liner": brief.one_liner, "features": brief.key_features, "usp": brief.usp},
        scripts=payload,
    )
    try:
        out: Reviews = llm.complete_json("compliance", prompt, Reviews, temperature=0.0, app_id=app_id,
                                         mock_payload=lambda: {"reviews": [{"id": cid, "pass": True, "issues": []}
                                                                           for cid, _ in items]})
    except llm.LLMError as e:
        log.warning(f"LLM compliance check unavailable ({e}); rule-based only")
        return {}
    return {r.id: [i for i in r.issues if i] for r in out.reviews if not r.ok}


def copy_check(script: dict[str, Any], market_hooks: list[str], threshold: float = 0.6, app_name: str = "") -> list[str]:
    """Flag script lines with high token overlap with a stored market hook (D8.5: never copy other creators)."""
    from engine.research.playbook import copies_market_hook

    issues = []
    lines = [script.get("hook", "")] + [x for sc in script.get("scenes", [])
                                        for x in (sc.get("voiceover", ""), sc.get("on_screen_text", ""))]
    for line in lines:
        if line and copies_market_hook(line, market_hooks, threshold, ignore=app_name):
            issues.append(f"copies a market creator's hook: “{line[:60]}”")
    return sorted(set(issues))


def check_creatives(creatives: Iterable[Creative], brief: Brief, use_llm: bool = True,
                    batch: int = 8) -> dict[str, int]:
    """Run compliance on scripted creatives and persist status. Returns counts."""
    from engine.research import cfg as research_cfg
    from engine.research.run import market_hooks

    items = [c for c in creatives if c.script]
    hooks = market_hooks(items[0].app_id) if items else []
    thr = float(research_cfg()["copy_overlap_threshold"])
    for c in items:
        text = script_texts(c.script) + " " + (c.post_caption or "")
        c.compliance_issues = rule_check(text, brief) + (copy_check(c.script, hooks, thr, brief.app_name) if hooks else [])
    if use_llm:
        for i in range(0, len(items), batch):
            chunk = items[i : i + batch]
            found = llm_check([(c.id, c.script) for c in chunk], brief, chunk[0].app_id)
            for c in chunk:
                c.compliance_issues = sorted(set(c.compliance_issues) | {f"LLM: {x}" for x in found.get(c.id, [])})
    counts = {"pass": 0, "flagged": 0}
    for c in items:
        if c.compliance_status in ("approved", "rejected"):
            continue  # a human already decided
        c.compliance_status = "flagged" if c.compliance_issues else "pass"
        counts[c.compliance_status] += 1
    save(*items)
    return counts


def is_publishable(c: Creative) -> bool:
    return c.compliance_status in ("pass", "approved")
