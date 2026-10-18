"""PERSONAS — each app's recurring AI characters, derived from that app's own audience and brand tone.

Flow (per app, no manual setup):
  propose()          Gemini casts 1–2 protagonists (the app's users, who act) + 1 presenter (talks to camera)
  write_sheet()      Magnific jobs: 4 candidate portraits per character (images_generate, 9:16)
  import_candidates()   … the assistant runs the jobs through the Magnific MCP and imports the results
  pick()             the owner chooses one portrait per character
  (library job)      the chosen portrait becomes a Magnific character-library entry → the same face in every video
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import requests
from pydantic import BaseModel, Field
from sqlmodel import select

from engine import cost, llm
from engine.brief import brief_for_prompt, brief_of
from engine.db import Persona, get_app, latest_understanding, save, session
from engine.settings import DATA_DIR, OUTPUTS_DIR, languages, load_yaml, log, market

def portrait_suffix() -> str:
    m = market()
    return (f". Vertical 9:16 photorealistic portrait, natural skin texture, real {m['person']} person {m['locale']}, no text, "
            f"no logos, no watermark, no phone screen visible; any money shown is {m['money']}.")


CANDIDATES = int((load_yaml('production').get('magnific') or {}).get('persona_candidates', 4))   # portraits per character (50 credits each)


class CastOut(BaseModel):
    key: str
    name: str = ""
    role: str = "protagonist"
    gender: str = "female"
    age: str = ""
    languages: list[str] = Field(default_factory=list)
    description: str = ""
    look_prompt: str = ""


class Cast(BaseModel):
    characters: list[CastOut] = Field(min_length=1)


def personas(app_id: str, role: Optional[str] = None, status: Optional[tuple[str, ...]] = None) -> list[Persona]:
    with session() as s:
        q = select(Persona).where(Persona.app_id == app_id)
        if role:
            q = q.where(Persona.role == role)
        rows = list(s.exec(q.order_by(Persona.id)).all())
    return [p for p in rows if not status or p.status in status]


def usable(app_id: str, role: Optional[str] = None) -> list[Persona]:
    """Characters that can appear in videos: a portrait was chosen (library entry preferred)."""
    return personas(app_id, role, status=("approved", "ready"))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:24] or "character"


def _voice_for(app_id: str, gender: str, langs: list[str]) -> Optional[int]:
    from engine.production.assets import mcfg

    for lang in langs:
        cast = (mcfg()["voice"].get("cast") or {}).get(lang)
        if cast:
            ids = cast.get(gender) or next(iter(cast.values()))
            return int(ids[0])
    return None


def propose(app_id: str, n_protagonists: int = 2, force: bool = False) -> list[Persona]:
    """Cast this app's characters from its brief + audience research (skips if it already has a cast)."""
    existing = personas(app_id)
    if existing and not force:
        return existing
    app = get_app(app_id)
    if app is None:
        raise ValueError(f"unknown app {app_id}")
    b = brief_of(app)
    u = latest_understanding(app_id)
    langs = [l for l in b.languages if l in languages()]
    prompt = llm.render_prompt("crew_personas", brief=brief_for_prompt(b), personas=(u.personas if u else []),
                               languages=[languages()[l]["name"] for l in langs], brand_tone=b.brand_tone,
                               n_protagonists=str(n_protagonists))

    def mock() -> dict[str, Any]:
        return {"characters": [
            {"key": "everyday_user", "name": "Asha", "role": "protagonist", "gender": "female", "age": "30-35",
             "languages": langs[:1], "description": "a typical user of the app", "look_prompt": "woman, 32"},
            {"key": "presenter", "name": "Rohan", "role": "presenter", "gender": "male", "age": "25-30",
             "languages": langs[:1], "description": "friendly creator", "look_prompt": "man, 27, smiling"}]}

    out: Cast = llm.complete_json(f"crew_personas_{app_id}", prompt, Cast, temperature=0.7, app_id=app_id,
                                  mock_payload=mock)
    rows, seen = [], {p.key for p in existing}
    for ch in out.characters:
        key = _slug(ch.key or ch.name)
        if key in seen:
            continue
        seen.add(key)
        chl = [l for l in (ch.languages or langs) if l in languages()] or langs
        rows.append(Persona(app_id=app_id, key=key, name=ch.name, role="presenter" if ch.role == "presenter" else "protagonist",
                            gender="male" if ch.gender.lower().startswith("m") else "female", age=ch.age,
                            languages=chl, description=ch.description, look_prompt=ch.look_prompt.strip().rstrip("."),
                            voice_id=_voice_for(app_id, ch.gender, chl)))
    save(*rows)
    log.info(f"{app_id}: cast {len(rows)} characters ({', '.join(f'{p.name} ({p.role})' for p in rows)})")
    return personas(app_id)


def candidate_job(p: Persona) -> dict[str, Any]:
    return {"request_id": f"persona_{p.key}", "persona": p.key, "tool": "images_generate", "credits": 50 * CANDIDATES,
            "arguments": {"prompt": p.look_prompt + portrait_suffix(), "aspectRatio": "9:16", "mode": "seedream-4",
                          "count": CANDIDATES}}


def library_job(app_id: str, p: Persona) -> dict[str, Any]:
    return {"request_id": f"library_{p.key}", "persona": p.key, "tool": "library_create", "credits": 0,
            "arguments": {"name": f"{app_id}_{p.key}"[:50], "type": "character", "gender": p.gender,
                          "description": f"{p.name}: {p.description}"[:1000],
                          "images": [{"creationIdentifier": p.approved.get("creation")}]}}


def pending_jobs(app_id: str) -> list[dict[str, Any]]:
    """Persona work still to do in Magnific: candidate portraits, then library entries for chosen portraits."""
    jobs = []
    for p in personas(app_id):
        if p.status == "proposed":
            jobs.append(candidate_job(p))
        elif p.status == "approved" and p.approved.get("creation") and not p.library_id:
            jobs.append(library_job(app_id, p))
    return jobs


def write_sheet(app_id: str) -> Path:
    d = OUTPUTS_DIR / app_id / "personas"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "persona_jobs.json"
    p.write_text(json.dumps({
        "app_id": app_id,
        "import_candidates_with": f"python cli.py crew persona import {app_id} <key> <url1> <url2> ... --creations <id1,id2,..>",
        "pick_with": f"python cli.py crew persona pick {app_id} <key> <candidate#>",
        "library_with": f"python cli.py crew persona library {app_id} <key> <library_id>",
        "characters": [{"key": x.key, "name": x.name, "role": x.role, "status": x.status, "description": x.description}
                       for x in personas(app_id)],
        "jobs": pending_jobs(app_id),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _get(app_id: str, key: str) -> Persona:
    with session() as s:
        p = s.exec(select(Persona).where(Persona.app_id == app_id, Persona.key == key)).first()
    if p is None:
        raise ValueError(f"{app_id} has no character '{key}'")
    return p


def import_candidates(app_id: str, key: str, sources: list[str], creations: Optional[list[str]] = None,
                      credits: Optional[int] = None) -> Persona:
    p = _get(app_id, key)
    d = DATA_DIR / "apps" / app_id / "personas"
    d.mkdir(parents=True, exist_ok=True)
    cands = []
    for i, src in enumerate(sources):
        dest = d / f"{key}_c{i + 1}.jpg"
        if src.startswith(("http://", "https://")):
            r = requests.get(src, timeout=120)
            r.raise_for_status()
            dest.write_bytes(r.content)
        else:
            dest.write_bytes(Path(src).read_bytes())
        cands.append({"file": str(dest), "url": src if src.startswith("http") else "",
                      "creation": (creations or [None] * len(sources))[i] if creations and i < len(creations) else None})
    p.candidates, p.status = cands, "candidates"
    save(p)
    used = int(credits if credits is not None else 50 * len(sources))
    cost.log_step("magnific:images_generate", "magnific", 0.0, app_id=app_id,
                  cost_inr=round(used * 0.083, 2), units=used)
    return p


def pick(app_id: str, key: str, index: int) -> Persona:
    """Owner chooses candidate #index (1-based) as the character's face."""
    p = _get(app_id, key)
    if not 1 <= index <= len(p.candidates or []):
        raise ValueError(f"{key} has {len(p.candidates or [])} candidates")
    p.approved, p.status = dict(p.candidates[index - 1]), "approved"
    save(p)
    return p


def set_library(app_id: str, key: str, library_id: str) -> Persona:
    p = _get(app_id, key)
    p.library_id, p.status = str(library_id), "ready"
    save(p)
    return p


def for_prompt(app_id: str) -> list[dict[str, Any]]:
    """The cast as the director sees it."""
    return [{"key": p.key, "name": p.name, "role": p.role, "gender": p.gender, "age": p.age,
             "who": p.description, "look": p.look_prompt} for p in usable(app_id)]
