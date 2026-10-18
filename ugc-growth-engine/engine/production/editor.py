"""VIDEO EDITOR — assembles each version from the director's plan (Remotion render) and applies the fixes the
reviewers defined, producing V2, V3, … Every applied fix is recorded against its task."""
from __future__ import annotations

import copy
import shutil
from typing import Any

from engine.db import ProductionRun, ReviewTask, save, session
from engine.production import assets
from engine.production.common import FIX_OWNER, persist, say, version_dir
from engine.production.director import BANDS, PACE_CUT
from engine.render import render_creative


def render_version(run: ProductionRun) -> dict[str, Any]:
    """Cut the next version (v1, v2, …) from the current plan into production/v<n>/."""
    n = run.version + 1
    out = version_dir(run, n)
    for old in ("frames", "work"):
        shutil.rmtree(out / old, ignore_errors=True)
    say(run, "editor", f"Cutting v{n}: voice → footage → captions → music → export")
    res = render_creative(run.creative_id, plan=copy.deepcopy(run.plan), out_dir=out, register=False)
    run.version = n
    run.versions = list(run.versions or []) + [{"version": n, "path": res["path"], "thumb": res["thumb"],
                                                "duration": round(res["duration"], 2),
                                                "render_seconds": round(res["render_seconds"], 1)}]
    persist(run)
    say(run, "editor", f"v{n} ready · {res['duration']:.1f}s · rendered in {res['render_seconds']:.0f}s")
    return res


def block_screenshots(run: ProductionRun) -> None:
    """The visual reviewer saw ANOTHER app in our screenshots: stop using screenshots for this video."""
    if run.plan.get("no_screenshots"):
        return
    run.plan["no_screenshots"] = True
    for sc in run.plan["scenes"]:
        if sc.get("shot") == "screenshot":
            sc["shot"] = "stock"
            sc["query_en"] = sc.get("query_en") or "indian person using smartphone app close up"
    say(run, "editor", "App screenshots show a different app — using footage instead; replace the files in "
                       "data/apps/<app>/ to bring app screens back")


def apply_fix(run: ProductionRun, fix: dict[str, Any], scene: Any) -> str:
    """Apply one machine fix to the plan. Returns '' when applied, else why it needs a human."""
    t = fix.get("type")
    scenes = run.plan["scenes"]
    sc = scene if isinstance(scene, int) and 0 <= scene < len(scenes) else fix.get("scene")
    sc = sc if isinstance(sc, int) and 0 <= sc < len(scenes) else None
    if t == "set_text":
        field, value = fix.get("field", "on_screen_text"), str(fix.get("value") or "").strip()
        if not value:
            return "no corrected text given"
        if field == "cta":
            run.plan["cta"] = value
            return ""
        if sc is None or field not in ("on_screen_text", "voiceover"):
            return "which scene/field to change is unclear"
        if scenes[sc].get(field) == value:
            return "already applied"
        scenes[sc][field] = value
        if field == "voiceover" and scenes[sc].get("voice_file"):
            scenes[sc]["voice_file"] = None  # the recorded line no longer matches: re-record it
            assets.add_request(run, "voice", sc)
        return ""
    if t == "set_visual":
        if sc is None:
            return "no scene given"
        shot = fix.get("shot", "stock")
        if shot not in ("stock", "screenshot"):
            return f"unknown shot {shot}"
        if shot == "screenshot" and run.plan.get("no_screenshots"):
            shot = "stock"
        scenes[sc].update({"shot": shot, "asset": None})
        if fix.get("query_en"):
            scenes[sc]["query_en"] = fix["query_en"]
        if "screenshot_index" in fix:
            scenes[sc]["screenshot_index"] = int(fix["screenshot_index"])
        return ""
    if t == "move_text":
        if sc is None or fix.get("band") not in BANDS:
            return "scene or band missing"
        band = fix["band"]
        key = "headline_band" if fix.get("target") == "headline" else "text_band"
        if scenes[sc].get(key) == band:
            # already there and still in the way (e.g. over a phone held at chest height): go to the other end
            band = "top" if band in ("lower", "middle") else "lower"
        scenes[sc][key] = band
        return ""
    if t == "set_design":
        d = run.plan.setdefault("design", {})
        if fix.get("wordsPerLine") in (2, 3, 4):
            d["wordsPerLine"] = int(fix["wordsPerLine"])
        if fix.get("size_scale"):
            from engine import captions
            from engine.production.common import creative_of

            c = creative_of(run)
            base = captions.design(c.language, c.creator_style, captions.style_seed(c), c.visual_mode)["size"]
            d["size"] = int(round(base * max(0.75, min(1.25, float(fix["size_scale"])))))
        if fix.get("outlinePx"):
            d["outlinePx"] = int(fix["outlinePx"])
        return ""
    if t == "set_pace":
        pace = fix.get("pace")
        if pace in PACE_CUT:
            run.plan["pace"], run.plan["cut_seconds"] = pace, PACE_CUT[pace]
            return ""
        return "unknown pace"
    if t == "set_mix":
        m = run.plan.setdefault("mix", {})
        for k, lo, hi in (("voice_volume", 1.0, 3.5), ("music_volume", 0.0, 0.6)):
            if fix.get(k) is not None:
                m[k] = max(lo, min(hi, float(fix[k])))
        if isinstance(fix.get("music"), bool):
            m["music"] = fix["music"]
        return ""
    if t == "request_asset":
        kind = fix.get("kind")
        targets = range(len(scenes)) if kind == "voice" and fix.get("scene", sc) == -1 else [sc if sc is not None else 0]
        if kind == "image" and any(scenes[i].get("shot") == "screenshot" for i in targets):
            # never pay to invent the app's UI: a wrong screen means the app's real screenshots need replacing
            return "app screens come only from the app's real screenshots"
        made = [assets.add_request(run, kind, i, fix.get("prompt", "")) for i in targets]
        if kind == "image" and any(made):
            for i in targets:
                run.plan["scenes"][i]["asset"] = None
        return "" if any(made) else "Magnific budget/casting doesn't allow it"
    return "needs a human decision"


def apply_tasks(run: ProductionRun, open_tasks: list[ReviewTask]) -> dict[str, int]:
    """Apply every open task's fix to the plan; mark each fixed / needs_human. Returns counts."""
    counts = {"applied": 0, "needs_human": 0}
    from engine.production.common import tasks as all_tasks

    earlier = [x for x in all_tasks(run.id) if x.status == "fixed" and (x.fix or {}).get("type") == "set_visual"]
    for t in open_tasks:
        fx = t.fix or {}
        if (fx.get("type") == "set_visual" and fx.get("shot", "stock") == "stock" and t.category != "wrong_app"
                and any(x.scene == t.scene and x.version < t.version for x in earlier)):
            # stock already failed this scene once: order the exact shot from Magnific instead of searching again
            t.fix = {"type": "request_asset", "kind": "image", "scene": t.scene,
                     "prompt": fx.get("query_en") or run.plan["scenes"][t.scene or 0].get("query_en", "")}
        if t.category == "wrong_app":
            block_screenshots(run)  # one foreign screen means the app's screenshot set can't be trusted
            t.fix = {"type": "set_visual", "shot": "stock",
                     "query_en": (t.fix or {}).get("query_en") or run.plan["scenes"][t.scene or 0].get("query_en")
                     or "indian person using smartphone app close up"}
        why = apply_fix(run, t.fix or {}, t.scene)
        with session() as s:
            row = s.get(ReviewTask, t.id)
            if why in ("", "already applied"):
                row.status, row.resolved_in = "fixed", run.version + 1
                counts["applied"] += 1
                say(run, "editor", f"{FIX_OWNER.get((t.fix or {}).get('type'), t.assigned_to).title()} fix → "
                                   f"{(t.fix or {}).get('type')}: {t.issue[:90]}")
            else:
                row.status = "needs_human"
                counts["needs_human"] += 1
            s.add(row)
            s.commit()
    run.plan = copy.deepcopy(run.plan)
    persist(run)
    return counts
