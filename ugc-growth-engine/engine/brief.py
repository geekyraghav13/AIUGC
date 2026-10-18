"""App brief model + optional Play Store enrichment (google-play-scraper)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import requests
from pydantic import BaseModel, Field

from engine import cache, cost
from engine.db import App, session
from engine.settings import DATA_DIR, log


class Brief(BaseModel):
    app_id: str = ""
    app_name: str
    category: str = ""
    one_liner: str = ""
    description: str = ""
    key_features: list[str] = Field(default_factory=list)
    usp: str = ""
    target_audience: str = ""
    city_tiers: list[str] = Field(default_factory=lambda: ["1", "2", "3"])
    languages: list[str] = Field(default_factory=lambda: ["hinglish", "hi", "ta"])
    languages_strict: bool = False                               # True = never use a language outside `languages` (e.g. English only)
    mode_mix: dict[str, int] = Field(default_factory=dict)       # optional fixed video-mode mix per batch, e.g. {"character": 4, "faceless": 4}
    research_focus: list[str] = Field(default_factory=list)   # extra research angles, e.g. how people actually use this kind of app
    brand_tone: str = "desi-casual"
    cta: str = "Download free on Play Store"
    play_store_id: str = ""
    play_store_url: str = ""
    claims_to_avoid: list[str] = Field(default_factory=list)
    # optional per-app look & voice: brand colours tilt the video palettes; voices override the default casting
    # e.g. {"hi": {"female": [1002], "male": [808]}}
    brand_colors: list[str] = Field(default_factory=list)
    voices: dict[str, dict[str, list[int]]] = Field(default_factory=dict)
    # filled by enrichment
    reviews: list[str] = Field(default_factory=list)
    store_description: str = ""

    def slug(self) -> str:
        return self.app_id or re.sub(r"[^a-z0-9]+", "", self.app_name.lower())[:24] or "app"

    def package(self) -> str:
        if self.play_store_id:
            return self.play_store_id
        m = re.search(r"id=([\w.]+)", self.play_store_url or "")
        return m.group(1) if m else f"com.example.{self.slug()}"


def load_brief(path: str | Path) -> Brief:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    b = Brief.model_validate(data)
    b.app_id = b.slug()
    return b


def sample_briefs() -> dict[str, Path]:
    return {p.stem: p for p in sorted((DATA_DIR / "sample_briefs").glob("*.json"))}


def enrich_from_play_store(brief: Brief, n_reviews: int = 200) -> tuple[Brief, list[str], Optional[str]]:
    """Add store description, ~200 reviews (en + hi, country=in) and screenshots. Never raises."""
    pkg = brief.package()
    if (not brief.play_store_url and not brief.play_store_id) or pkg.startswith("com.example."):
        return brief, [], None
    try:
        from google_play_scraper import Sort, app as gp_app, reviews as gp_reviews
    except ImportError:
        log.warning("google-play-scraper not installed; using brief only")
        return brief, [], None

    with cost.timed("playstore:enrich", "google-play-scraper", app_id=brief.slug()):
        k = cache.key("gplay", pkg, n_reviews)
        data = cache.get_json("playstore", k)
        if data is None:
            try:
                info = gp_app(pkg, lang="en", country="in")
                revs: list[str] = []
                for lang in ("en", "hi"):
                    got, _ = gp_reviews(pkg, lang=lang, country="in", sort=Sort.MOST_RELEVANT, count=n_reviews // 2)
                    revs += [r["content"] for r in got if r.get("content")]
                data = {"description": info.get("description", ""), "icon": info.get("icon"),
                        "screenshots": info.get("screenshots", [])[:8], "reviews": revs}
                cache.put_json("playstore", k, data)
            except Exception as e:  # noqa: BLE001
                log.warning(f"Play Store enrichment failed for {pkg}: {e}; using brief only")
                return brief, [], None

    brief.store_description = (data.get("description") or "")[:3000]
    brief.reviews = data.get("reviews", [])[:n_reviews]
    out_dir = DATA_DIR / "apps" / brief.slug()
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = []
    for i, url in enumerate(data.get("screenshots", [])):
        p = out_dir / f"screenshot_{i}.png"
        if _download(url, p):
            shots.append(str(p))
    icon = out_dir / "icon.png"
    icon_path = str(icon) if data.get("icon") and _download(data["icon"], icon) else None
    return brief, shots, icon_path


def _download(url: str, dest: Path) -> bool:
    if dest.exists():
        return True
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return True
    except requests.RequestException:
        return False


def local_assets(app_id: str) -> tuple[list[str], Optional[str]]:
    """Screenshots/icon a user dropped into data/apps/<app_id>/ (used when no Play Store URL)."""
    d = DATA_DIR / "apps" / app_id
    if not d.exists():
        return [], None
    shots = sorted(str(p) for p in d.glob("screenshot_*.*"))
    # a screen that is byte-identical to another app's belongs to that app, never to this one
    others = {_digest(p) for p in (DATA_DIR / "apps").glob("*/screenshot_*.*") if p.parent != d}
    foreign = [s for s in shots if _digest(Path(s)) in others]
    if foreign:
        log.warning(f"{app_id}: ignoring {len(foreign)} screenshot(s) copied from another app — add this app's own")
        shots = [s for s in shots if s not in foreign]
    icons = [str(p) for p in d.glob("icon.*")]
    return shots, (icons[0] if icons else None)


def _digest(p: Path) -> str:
    import hashlib

    return hashlib.md5(p.read_bytes()).hexdigest()


def register_app(brief: Brief, enrich: bool = True) -> App:
    """Create/update the App row from a brief (optionally enriched from the Play Store)."""
    brief.app_id = brief.slug()
    shots, icon = [], None
    if enrich:
        brief, shots, icon = enrich_from_play_store(brief)
    if not shots:
        shots, icon2 = local_assets(brief.app_id)
        icon = icon or icon2
    if not shots:
        from engine.visuals import make_placeholder_screens  # lazy: needs ffmpeg

        shots, icon3 = make_placeholder_screens(brief)
        icon = icon or icon3
    with session() as s:
        app = s.get(App, brief.app_id) or App(id=brief.app_id, name=brief.app_name)
        app.name = brief.app_name
        app.category = brief.category
        app.brief = brief.model_dump()
        app.play_store_id = brief.package()
        app.screenshots = shots
        app.icon_path = icon
        s.add(app)
        s.commit()
        s.refresh(app)
    log.info(f"App registered: {app.id} ({len(brief.reviews)} reviews, {len(shots)} screenshots)")
    return app


def brief_of(app: App) -> Brief:
    return Brief.model_validate(app.brief)


def brief_for_prompt(b: Brief) -> dict[str, Any]:
    d = b.model_dump(exclude={"reviews", "store_description", "play_store_url"})
    if b.store_description:
        d["play_store_description"] = b.store_description[:1500]
    return d

