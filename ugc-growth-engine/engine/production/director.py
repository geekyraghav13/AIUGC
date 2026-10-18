"""VIDEO DIRECTOR — turns the approved script into a scene-by-scene plan (shots, text placement, pacing,
transitions, voice direction) and decides which Magnific assets the video needs."""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from engine import llm
from engine.brief import brief_for_prompt, brief_of
from engine.db import ProductionRun, get_app
from engine.localize import language_block
from engine.production import assets
from engine.production.common import cfg, creative_of, say
from engine.settings import creator_styles, formats, languages, load_yaml

PACE_CUT = {"fast": 2.0, "medium": 3.0, "slow": 4.5}
BANDS = ("top", "upper", "middle", "lower")
SHOTS = ("stock", "stock_photo", "screenshot", "motion_graphic", "scene_image", "persona_image", "persona_clip",
         "talking")
TALK_MAX_WORDS = 9          # ≈ 3 s of lip-sync (billed per second)

# What each visual mode may use, and how to direct it (sent to the director; enforced again in normalise()).
MODE_SHOTS = {
    "faceless": ("stock", "screenshot", "motion_graphic", "scene_image"),
    "images_vo": ("stock_photo", "screenshot", "motion_graphic", "scene_image"),
    "character": ("stock", "screenshot", "motion_graphic", "scene_image", "persona_image", "persona_clip"),
    "ai_character": SHOTS,
}
MODE_RULES = {
    "faceless": "No recurring character and no talking to camera. Settings come from `stock` video, what the app does "
                "from `screenshot`, lists / numbers / old-vs-new from `motion_graphic`. People may appear in stock "
                "footage but never as the focus; use `scene_image` only for objects or hands (no faces).",
    "images_vo": "Every scene is a STILL with its big `headline`: `stock_photo` for generic places, `scene_image` for a "
                 "specific person or moment, `screenshot` for the app, `motion_graphic` for steps or old-vs-new. "
                 "No video clips, no talking.",
    "character": "One PROTAGONIST character from the cast carries the story: 2–3 scenes are `persona_image` of them in "
                 "their situation, and at most ONE `persona_clip` (the hook is the best place). Other scenes: `stock` "
                 "for settings, `screenshot` for the app. Nobody talks to camera — a narrator voice tells the story.",
    "ai_character": "The PRESENTER character talks to camera in the FIRST scene (hook) and the LAST scene (CTA): shot "
                    "`talking`, each line ≤ 9 words. In between: the protagonist (`persona_image`, at most one "
                    "`persona_clip`), `screenshot` for the app, `stock` for settings. Exactly two `talking` scenes.",
}


class Graphic(BaseModel):
    type: str = "steps"
    items: list[str] = Field(default_factory=list)


class ScenePlan(BaseModel):
    scene: int = 0
    shot: str = "stock"
    query_en: str = ""
    image_prompt_en: str = ""
    motion: str = ""
    persona: str = ""
    graphic: Graphic = Field(default_factory=Graphic)
    screenshot_index: int = 0
    voiceover: str = ""
    on_screen_text: str = ""
    headline: str = ""
    text_band: str = "auto"
    camera: str = "static"
    delivery: str = ""
    purpose: str = ""


class DirectorPlan(BaseModel):
    scenes: list[ScenePlan] = Field(min_length=1)
    pace: str = "medium"
    transition: str = "punch"
    music: bool = True
    voice_direction: str = ""
    director_note: str = ""


def _mock(script: dict[str, Any], n_screens: int, mode: str, cast: list[dict[str, Any]]) -> dict[str, Any]:
    scenes = []
    last = len(script["scenes"]) - 1
    prot = next((p["key"] for p in cast if p["role"] == "protagonist"), "")
    for i, sc in enumerate(script["scenes"]):
        v = sc.get("visual") or {}
        shot = "screenshot" if v.get("type") == "screenshot" and n_screens else ("stock_photo" if mode == "images_vo" else "stock")
        if mode in ("character", "ai_character") and i in (1, 2):
            shot = "persona_image"
        if mode == "ai_character" and i in (0, last):
            shot = "talking"
        scenes.append({"scene": i, "shot": shot, "query_en": v.get("query_en", ""), "voiceover": sc["voiceover"],
                       "on_screen_text": sc.get("on_screen_text", ""), "headline": sc.get("on_screen_text", "")[:30],
                       "screenshot_index": v.get("screenshot_index", 0), "persona": prot,
                       "image_prompt_en": v.get("query_en", "") or "person in their everyday situation",
                       "text_band": "auto", "purpose": "hook" if i == 0 else "beat"})
    return {"scenes": scenes, "pace": "medium", "transition": "punch", "music": True,
            "voice_direction": "warm, upbeat young creator talking to a friend", "director_note": "mock plan"}


def _degrade(shot: str, mode: str, sp: ScenePlan, cast: dict[str, dict[str, Any]], n_screens: int) -> str:
    """Nearest shot this mode/app can actually do."""
    allowed = MODE_SHOTS.get(mode, SHOTS)
    if shot == "screenshot" and not n_screens:
        return "motion_graphic"
    if shot in ("persona_image", "persona_clip") and sp.persona not in cast:
        return "scene_image" if "scene_image" in allowed else "stock"
    if shot == "talking":
        return "persona_image" if "persona_image" in allowed and sp.persona in cast else "scene_image"
    if shot not in allowed:
        return {"stock": "stock_photo" if "stock_photo" in allowed else "stock",
                "stock_photo": "stock" if "stock" in allowed else "stock_photo",
                "persona_clip": "persona_image" if "persona_image" in allowed else "scene_image",
                "persona_image": "scene_image"}.get(shot, allowed[0])
    return shot


def normalise(dp: DirectorPlan, script: dict[str, Any], n_screens: int, mode: str,
              cast: list[dict[str, Any]]) -> dict[str, Any]:
    """Snap the LLM plan onto what this mode and this app can actually do; one plan scene per script scene."""
    src = script["scenes"]
    last = len(src) - 1
    by_idx = {s.scene: s for s in dp.scenes}
    by_key = {p["key"]: p for p in cast}
    presenter = next((p["key"] for p in cast if p["role"] == "presenter"), None)
    protagonist = next((p["key"] for p in cast if p["role"] == "protagonist"), None) or presenter
    notes: list[str] = []
    caps = (load_yaml("production").get("magnific") or {}).get("character_limits") or {}
    max_images, max_clips = int(caps.get("persona_images", 3)), int(caps.get("persona_clips", 1))
    scenes, clips, images = [], 0, 0
    for i, sc in enumerate(src):
        p = by_idx.get(i) or (dp.scenes[i] if i < len(dp.scenes) else ScenePlan(scene=i))
        shot = p.shot if p.shot in SHOTS else "stock"
        if shot in ("persona_image", "persona_clip") and p.persona not in by_key and protagonist:
            p.persona = protagonist
        if mode == "ai_character" and i in (0, last) and presenter:
            shot, p.persona = "talking", presenter          # the presenter opens and closes the video
        elif shot == "talking":
            shot = "persona_image"                          # talking only for hook + CTA (cost)
        if shot == "talking" and p.persona != presenter:
            p.persona = presenter or ""
        new = shot if shot == "talking" and presenter else _degrade(shot, mode, p, by_key, n_screens)
        if new != shot:
            notes.append(f"scene {i}: {shot} → {new}")
        shot = new
        if shot == "persona_clip":
            clips += 1
            if clips > max_clips:
                shot = "persona_image"
        if shot == "persona_image":
            images += 1
            if images > max_images:                          # paid portraits are capped per video (cost control)
                shot = "stock"
                notes.append(f"scene {i}: persona_image → stock (cap {max_images} per video)")
        graphic = p.graphic.model_dump()
        if shot == "motion_graphic" and not graphic.get("items"):
            graphic = {"type": "steps", "items": [x for x in (p.headline, p.on_screen_text) if x][:2] or [sc["voiceover"][:40]]}
        scenes.append({
            "shot": shot, "query_en": p.query_en.strip() or (sc.get("visual") or {}).get("query_en", ""),
            "image_prompt_en": p.image_prompt_en.strip() or p.query_en.strip(), "motion": p.motion.strip(),
            "persona": p.persona if shot in ("persona_image", "persona_clip", "talking") else "",
            "graphic": graphic if shot == "motion_graphic" else {},
            "screenshot_index": min(max(0, int(p.screenshot_index)), max(0, n_screens - 1)),
            "voiceover": p.voiceover.strip() or sc["voiceover"],
            "on_screen_text": p.on_screen_text.strip() or sc.get("on_screen_text", ""),
            "headline": p.headline.strip(),
            "text_band": p.text_band if p.text_band in BANDS else None,
            "camera": p.camera, "purpose": p.purpose,
            "delivery": re.sub(r"[^a-z ]", "", p.delivery.lower()).strip()[:24],
            "voice_file": None, "asset": None, "talking": shot == "talking",
        })
    if mode == "ai_character" and not presenter:
        notes.append("no approved presenter yet — talking scenes shown as character images")
    if mode in ("character", "ai_character") and not cast:
        notes.append("no approved characters yet — people come from one-off AI photos")
    pace = dp.pace if dp.pace in PACE_CUT else "medium"
    transition = dp.transition if dp.transition in ("punch", "fade") else "punch"
    return {"mode": mode, "scenes": scenes, "pace": pace, "cut_seconds": PACE_CUT[pace], "transition": transition,
            "design": {"transition": transition}, "mix": {"music": bool(dp.music)},
            "voice_direction": dp.voice_direction.strip(), "director_note": dp.director_note.strip(),
            "mode_notes": notes}


def direct(run: ProductionRun) -> dict[str, Any]:
    """Write the plan and the asset requests onto the run."""
    from engine.production import personas
    from engine.settings import taxonomy

    c = creative_of(run)
    app = get_app(c.app_id)
    b = brief_of(app)
    n_screens = len([s for s in (app.screenshots or [])])
    mode = c.visual_mode or "faceless"
    cast = personas.for_prompt(c.app_id)
    st = creator_styles()[c.creator_style]
    scenes = [{"scene": i, "voiceover": sc["voiceover"], "on_screen_text": sc.get("on_screen_text", ""),
               "visual_idea": (sc.get("visual") or {}).get("query_en", ""), "seconds": sc.get("est_seconds")}
              for i, sc in enumerate(c.script["scenes"])]
    prompt = llm.render_prompt(
        "crew_director", brief=brief_for_prompt(b), language=languages()[c.language]["name"],
        style=c.creator_style, style_description=st.get("description", ""),
        format=f"{c.format}: {formats().get(c.format, {}).get('structure', '')}",
        language_block=language_block(c.language), scenes=scenes, n_screens=str(n_screens),
        max_screen=str(max(0, n_screens - 1)),
        screens_note="" if n_screens else "This app has NO screenshots: use `motion_graphic` to explain what it does.",
        mode=mode, mode_description=taxonomy()["visual_modes"].get(mode, ""), mode_rules=MODE_RULES.get(mode, ""),
        cast=cast or "(no characters approved for this app yet — use scene_image for people)",
        claims_to_avoid=", ".join(b.claims_to_avoid) or "(none)")
    dp: DirectorPlan = llm.complete_json(f"crew_director_{c.id}_{mode}", prompt, DirectorPlan, temperature=0.6,
                                         app_id=c.app_id, mock_payload=lambda: _mock(c.script, n_screens, mode, cast))
    plan = normalise(dp, c.script, n_screens, mode, cast)
    run.plan = plan
    run.asset_requests = assets.requests_for(c, plan)
    run.credits_estimate = sum(r["credits"] for r in run.asset_requests)
    shots = [s["shot"] for s in plan["scenes"]]
    mix = ", ".join(f"{shots.count(k)} {k.replace('_', ' ')}" for k in SHOTS if shots.count(k))
    say(run, "director", f"[{mode}] {plan['director_note'] or 'Plan ready'} · {mix} · pace {plan['pace']} · "
                         f"{plan['transition']} cuts" + (f" · {'; '.join(plan['mode_notes'])}" if plan["mode_notes"] else ""))
    if run.asset_requests:
        say(run, "director", f"Needs {len(run.asset_requests)} Magnific assets (~{run.credits_estimate} credits): "
                             + ", ".join(sorted({r['kind'] for r in run.asset_requests})))
    return plan


class OwnerFix(BaseModel):
    scene: Optional[int] = None
    fix: dict[str, Any] = Field(default_factory=dict)


class OwnerFixes(BaseModel):
    fixes: list[OwnerFix] = Field(default_factory=list)
    reply_to_owner: str = ""


def interpret_owner_note(run: ProductionRun, note: str) -> OwnerFixes:
    """Owner sent the video back with a free-text note → concrete fixes for the editor."""
    from engine.production.common import plan_for_prompt

    c = creative_of(run)
    prompt = llm.render_prompt("crew_owner_revise", note=note, plan=plan_for_prompt(run.plan))
    return llm.complete_json(f"crew_owner_{c.id}_{run.version}", prompt, OwnerFixes, temperature=0.3,
                             app_id=c.app_id, mock_payload=lambda: {"fixes": [], "reply_to_owner": "noted"})
