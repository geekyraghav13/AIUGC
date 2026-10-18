"""Per-creative tracking slug + Play Store UTM referrer link."""
from __future__ import annotations

import hashlib
from typing import Iterable
from urllib.parse import quote

from engine.brief import brief_of
from engine.db import App, Creative, get_app, save

LANG_SHORT = {"hinglish": "hg"}


def slug_for(c: Creative) -> str:
    h = hashlib.sha1(f"{c.app_id}:{c.id}".encode()).hexdigest()[:5]
    lang = LANG_SHORT.get(c.language, c.language)[:2]
    return f"g{c.generation}-{lang}-{c.id}-{h}"


def link(app: App, c: Creative, platform: str = "instagram") -> str:
    pkg = brief_of(app).package()
    referrer = (f"utm_source={platform}&utm_medium=ai_ugc&utm_campaign={app.id}_gen{c.generation}"
                f"&utm_content={c.tracking_slug or slug_for(c)}")
    return f"https://play.google.com/store/apps/details?id={pkg}&referrer={quote(referrer, safe='')}"


def assign(app_id: str, items: Iterable[Creative]) -> None:
    items = list(items)
    for c in items:
        if not c.tracking_slug:
            c.tracking_slug = slug_for(c)
    save(*items)


def link_for(c: Creative, platform: str = "instagram") -> str:
    app = get_app(c.app_id)
    return link(app, c, platform) if app else ""
