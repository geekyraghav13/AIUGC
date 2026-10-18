"""Stock visuals: Pexels video → Pexels photo → Pixabay → gradient. Plus placeholder app screens."""
from __future__ import annotations

import colorsys
import hashlib
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Optional

import requests

from engine import cache, cost
from engine.settings import CACHE_DIR, DATA_DIR, FONTS_DIR, env, ffmpeg_bin, log, market

TIMEOUT = 30


def _get(url: str, **kw: Any) -> Optional[dict]:
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=TIMEOUT, **kw)
            if r.status_code == 429:
                raise requests.RequestException("rate limited")
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log.debug(f"stock search failed ({attempt + 1}/3): {e}")
    return None


def _download(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    try:
        with requests.get(url, timeout=120, stream=True) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            tmp.replace(dest)
        return True
    except requests.RequestException as e:
        log.warning(f"download failed: {e}")
        return False


INDIA_WORDS = re.compile(
    r"\b(india|indian|desi|hindu|sikh|mumbai|delhi|new-delhi|bengaluru|bangalore|kolkata|calcutta|chennai|hyderabad|"
    r"pune|jaipur|goa|kerala|kochi|ladakh|spiti|himalaya|himalayan|varanasi|agra|udaipur|jodhpur|amritsar|punjab|"
    r"rajasthan|gujarat|ahmedabad|surat|lucknow|kanpur|patna|bihar|tamil|telugu|bengali|marathi|mysore|coimbatore|"
    r"madurai|rickshaw|rickshaws|auto-rickshaw|tuk-tuk|saree|sari|kurta|turban|chai|rupee|rupees|diwali|holi|"
    r"navratri|ganesh|bollywood|dhaba|kirana)\b", re.I)


def india_score(text: str) -> int:
    """How clearly a stock item is Indian, from its page slug / alt text (0 = no Indian cue)."""
    return len(INDIA_WORDS.findall((text or "").replace("-", " ")))


def _pick_indian(items: list[dict], text_of) -> list[dict]:  # noqa: ANN001
    """Items with an Indian cue, most Indian first (stable for ties)."""
    scored = [(india_score(text_of(it)), i, it) for i, it in enumerate(items)]
    return [it for s, _, it in sorted(scored, key=lambda x: (-x[0], x[1])) if s > 0]


MAGNIFIC_API = "https://api.magnific.com"


def _magnific_search(query: str, n: int = 20) -> list[dict]:
    """Magnific stock footage (licensed, commercial use): vertical 9:16, real footage only (no AI-generated clips),
    1080p preferred over 4K (file size). Searching is free; only the chosen clip is downloaded."""
    key = env("MAGNIFIC_API_KEY")
    if not key:
        return []
    cw, seen, words = content_words(query), set(), []
    for w in re.findall(r"[a-z]+", query.lower().replace("-", " ")):    # key words in order: short terms search better
        s = w[:-1] if w.endswith("s") and len(w) > 3 else w
        if s in cw and s not in seen:
            seen.add(s)
            words.append(w)
    term = " ".join(words[:4]) or query
    data = _get(f"{MAGNIFIC_API}/v1/videos", headers={"x-magnific-api-key": key},
                params=[("term", term),("filters[aspect_ratio][]", "9:16"), ("filters[ai-generated][excluded]", "1")])
    out = []
    for v in (data or {}).get("data", [])[:n]:
        if v.get("is_ai_generated") or v.get("quality") not in ("720p", "1080p", "2k", "4k"):
            continue
        out.append({"kind": "video", "mid": v["id"], "ext": ".mp4", "quality": v["quality"],
                    "desc": f"{v.get('name', '')} {v.get('url', '')}",
                    "attribution": f"Stock video from Magnific (id {v['id']}), commercial license"})
    return out


def _magnific_resolve(mid: int) -> Optional[str]:
    """Download link for one chosen clip (counts as that clip's licensed use; costs no AI credits)."""
    data = _get(f"{MAGNIFIC_API}/v1/videos/{mid}/download", headers={"x-magnific-api-key": env("MAGNIFIC_API_KEY")})
    return ((data or {}).get("data") or {}).get("url")


def _shrink(path: Path, max_s: int = 15) -> None:
    """Magnific 'original' downloads can be hundreds of MB (4K, minutes long): keep the first `max_s` seconds at
    1080p without audio. Replaces the file in place; small files are left alone."""
    marker = path.with_suffix(".norm")
    if not path.exists() or marker.exists():
        return
    tmp = path.with_suffix(".small.mp4")
    # 30 fps, a keyframe every second, faststart: stock originals (60 fps, 10-bit, long GOP) can stall Remotion's
    # per-frame decoding ("Timeout exceeded rendering the frame")
    p = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(path), "-t", str(max_s), "-an",
                        "-vf", "scale='min(1080,iw)':-2,fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "24", "-g", "30", "-movflags", "+faststart", str(tmp)], capture_output=True, text=True)
    if p.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(path)
        marker.touch()
    else:
        tmp.unlink(missing_ok=True)
        log.warning(f"could not shrink {path.name}: {p.stderr[-200:]}")


def _magnific_video(query: str, india_only: bool = True) -> Optional[dict]:
    found = _magnific_search(query)
    found.sort(key=lambda c: (c["quality"] == "4k", -match_score(query, c["desc"])))
    for c in found[:3]:
        url = _magnific_resolve(c["mid"])
        if url:
            return {"kind": "video", "url": url, "ext": ".mp4", "attribution": c["attribution"]}
    return None


def _pexels_video(query: str, india_only: bool = True) -> Optional[dict]:
    key = env("PEXELS_API_KEY")
    if not key:
        return None
    data = _get("https://api.pexels.com/videos/search", headers={"Authorization": key},
                params={"query": query, "orientation": "portrait", "per_page": 15, "size": "medium"})
    videos = (data or {}).get("videos", [])
    if india_only:
        videos = _pick_indian(videos, lambda v: v.get("url", ""))
    for v in videos:
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"
                 and min(f.get("width") or 0, f.get("height") or 0) >= 720]
        if not files:
            continue
        f = min(files, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))
        return {"kind": "video", "url": f["link"], "ext": ".mp4",
                "attribution": f"Video by {v.get('user', {}).get('name', 'Pexels creator')} on Pexels ({v.get('url')})"}
    return None


def _pexels_photo(query: str, india_only: bool = True) -> Optional[dict]:
    key = env("PEXELS_API_KEY")
    if not key:
        return None
    data = _get("https://api.pexels.com/v1/search", headers={"Authorization": key},
                params={"query": query, "orientation": "portrait", "per_page": 15})
    photos = (data or {}).get("photos", [])
    if india_only:
        photos = _pick_indian(photos, lambda p: f"{p.get('url', '')} {p.get('alt', '')}")
    for p in photos:
        return {"kind": "image", "url": p["src"]["large2x"], "ext": ".jpg",
                "attribution": f"Photo by {p.get('photographer', 'Pexels creator')} on Pexels ({p.get('url')})"}
    return None


def _pixabay(query: str, india_only: bool = True) -> Optional[dict]:
    key = env("PIXABAY_API_KEY")
    if not key:
        return None
    data = _get("https://pixabay.com/api/videos/", params={"key": key, "q": query, "per_page": 15})
    hits = (data or {}).get("hits", [])
    if india_only:
        hits = _pick_indian(hits, lambda h: f"{h.get('pageURL', '')} {h.get('tags', '')}")
    for h in hits:
        vids = h.get("videos", {})
        for size in ("medium", "large", "small"):
            f = vids.get(size) or {}
            if f.get("url") and min(f.get("width", 0), f.get("height", 0)) >= 720:
                return {"kind": "video", "url": f["url"], "ext": ".mp4",
                        "attribution": f"Video by {h.get('user', 'Pixabay creator')} on Pixabay ({h.get('pageURL')})"}
    data = _get("https://pixabay.com/api/", params={"key": key, "q": query, "orientation": "vertical",
                                                    "per_page": 15, "image_type": "photo"})
    hits = (data or {}).get("hits", [])
    if india_only:
        hits = _pick_indian(hits, lambda h: f"{h.get('pageURL', '')} {h.get('tags', '')}")
    for h in hits:
        return {"kind": "image", "url": h["largeImageURL"], "ext": ".jpg",
                "attribution": f"Image by {h.get('user', 'Pixabay creator')} on Pixabay ({h.get('pageURL')})"}
    return None


def palette(seed: str) -> tuple[str, str]:
    """Two pleasant hex colours derived deterministically from a string."""
    h = int(hashlib.md5(seed.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    c1 = colorsys.hsv_to_rgb(h, 0.65, 0.55)
    c2 = colorsys.hsv_to_rgb((h + 0.12) % 1, 0.75, 0.25)
    to_hex = lambda c: "".join(f"{int(x * 255):02x}" for x in c)  # noqa: E731
    return to_hex(c1), to_hex(c2)


def gradient(seed: str, w: int = 1080, h: int = 1920) -> Path:
    """Vertical gradient still (final fallback, and base for placeholders)."""
    c1, c2 = palette(seed)
    out = CACHE_DIR / "stock" / f"gradient_{c1}_{c2}_{w}x{h}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        subprocess.run(
            [ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"gradients=s={w}x{h}:c0=0x{c1}:c1=0x{c2}:x0=0:y0=0:x1={w}:y1={h}:nb_colors=2",
             "-frames:v", "1", str(out)],
            check=True, timeout=60,
        )
    return out


# Generic Indian scenes used when a scene's own query finds nothing clearly Indian.
INDIA_FALLBACK_QUERIES = ["indian city traffic", "mumbai street", "auto rickshaw india", "indian highway",
                          "delhi street", "bengaluru traffic", "indian market street"]


def fetch_stock(query: str, app_id: Optional[str] = None, india_only: bool = True, photo_only: bool = False,
                generic_fallback: bool = True) -> dict:
    """Return {'kind': video|image|none, 'path', 'attribution', 'source'}; never raises.

    India-first: only stock items whose page/alt text shows an Indian cue are used; then generic Indian scenes;
    then kind='none' so the renderer shows an app screenshot instead of off-market footage.
    `photo_only` skips video (images + voice-over videos); `generic_fallback=False` skips the generic Indian street
    scenes, so a miss comes back as 'none' instead of unrelated footage (the production crew then orders the shot).
    """
    india_only = india_only and market()["id"] == "IN"       # the India-first footage filter only applies to the India market
    q = query.lower().strip()
    k = cache.key("stock_v2", q, india_only, market()["id"]) if not (photo_only or not generic_fallback) else \
        cache.key("stock_v3", q, india_only, photo_only, generic_fallback, market()["id"])
    hit = cache.get_json("stock", k)
    if hit and (hit["kind"] == "none" or Path(hit["path"]).exists()):
        return hit
    queries = [q] if not india_only or re.search(r"\bindia", q) else [f"indian {q}", q]
    if india_only and generic_fallback:
        queries += [INDIA_FALLBACK_QUERIES[int(cache.key(q)[:4], 16) % len(INDIA_FALLBACK_QUERIES)]]
    with cost.timed("stock:search", "pexels/pixabay", app_id=app_id):
        for query_try in queries:
            res = _first_stock(query_try, india_only, photo_only)
            if res:
                cache.put_json("stock", k, res)
                return res
    if india_only or not generic_fallback:
        res = {"kind": "none", "path": "", "attribution": "", "source": "none"}
        cache.put_json("stock", k, res)
        return res
    return {"kind": "image", "path": str(gradient(query)), "attribution": "", "source": "gradient"}


def _first_stock(query: str, india_only: bool, photo_only: bool = False) -> Optional[dict]:
    """First downloadable result across Pexels video → Pexels photo → Pixabay (photos only when `photo_only`)."""
    sources = (("magnific_video", _magnific_video), ("pexels_video", _pexels_video), ("pexels_photo", _pexels_photo),
               ("pixabay", _pixabay))
    for source, fn in sources[2:] if photo_only else sources:
        try:
            found = fn(query, india_only)
        except Exception as e:  # noqa: BLE001
            log.debug(f"{source} error: {e}")
            found = None
        if not found or (photo_only and found["kind"] != "image"):
            continue
        dest = CACHE_DIR / "stock" / f"{cache.key(found['url'])}{found['ext']}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if _download(found["url"], dest):
            if source == "magnific_video":
                _shrink(dest)
            return {"kind": found["kind"], "path": str(dest), "attribution": found["attribution"], "source": source}
    return None


def _esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def make_placeholder_screens(brief: Any, n: int = 4) -> tuple[list[str], Optional[str]]:
    """Mock 'app screenshots' (one per key feature) + icon for apps without real screenshots.

    Clearly generic UI cards: app name + feature text. Replace by dropping real screenshots into
    data/apps/<app_id>/screenshot_*.png.
    """
    out_dir = DATA_DIR / "apps" / brief.slug()
    out_dir.mkdir(parents=True, exist_ok=True)
    font = FONTS_DIR / "NotoSans-Bold.ttf"
    c1, c2 = palette(brief.app_name)
    feats = (brief.key_features or [brief.one_liner or brief.app_name])[:n]
    shots: list[str] = []
    for i, feat in enumerate(feats):
        p = out_dir / f"screenshot_{i}.png"
        if not p.exists():
            lines = textwrap.wrap(feat, 22)[:4]
            draws = [
                "drawbox=x=90:y=260:w=900:h=1400:color=white@0.96:t=fill",
                f"drawbox=x=90:y=260:w=900:h=180:color=0x{c1}:t=fill",
                f"drawtext=fontfile='{font}':text='{_esc(brief.app_name)}':fontcolor=white:fontsize=64:x=140:y=315",
            ]
            for j, line in enumerate(lines):
                draws.append(f"drawtext=fontfile='{font}':text='{_esc(line)}':fontcolor=0x222222:fontsize=58:"
                             f"x=140:y={560 + j * 84}")
            for j in range(4):
                y = 1000 + j * 150
                draws.append(f"drawbox=x=140:y={y}:w=800:h=110:color=0x{c1}@0.{15 + j * 5}:t=fill")
            draws.append(f"drawtext=fontfile='{font}':text='Feature {i + 1} of {len(feats)}':fontcolor=0x777777:"
                         f"fontsize=40:x=140:y=1580")
            subprocess.run(
                [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(gradient(brief.app_name)),
                 "-vf", ",".join(draws), "-frames:v", "1", str(p)],
                check=True, timeout=60,
            )
        shots.append(str(p))
    icon = out_dir / "icon.png"
    if not icon.exists():
        letter = _esc(brief.app_name[:1].upper())
        subprocess.run(
            [ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x{c1}:s=512x512",
             "-vf", f"drawtext=fontfile='{font}':text='{letter}':fontcolor=white:fontsize=300:"
                    f"x=(w-text_w)/2:y=(h-text_h)/2-20",
             "-frames:v", "1", str(icon)],
            check=True, timeout=60,
        )
    return shots, str(icon)


# ---------------------------------------------------------------- stock-first with a similarity check (no LLM)

STOP = {"a", "an", "the", "of", "in", "on", "at", "with", "and", "or", "to", "for", "from", "by", "is", "are", "his",
        "her", "their", "its", "into", "over", "up", "down", "close", "shot", "photo", "video", "vertical", "candid",
        "photorealistic", "realistic", "natural", "light", "9", "16", "no", "text", "logos", "indian", "india"}


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", (text or "").lower().replace("-", " "))
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words if w not in STOP and len(w) > 2}


def match_score(query: str, description: str) -> float:
    """Share of the query's content words found in a stock item's description / page slug (0–1)."""
    q = content_words(query)
    return len(q & content_words(description)) / len(q) if q else 0.0


def best_stock(query: str, photo_only: bool = False, app_id: Optional[str] = None) -> dict:
    """Best-matching free stock item for a scene: Pexels (videos unless photo_only, then photos), scored by how much of
    the query its description covers, Indian items preferred. Returns {kind, path, attribution, match} or kind 'none'.
    Cached; costs no LLM tokens (only free Pexels calls)."""
    k = cache.key("stock_match_v1", query.lower().strip(), photo_only)
    hit = cache.get_json("stock", k)
    if hit and (hit["kind"] == "none" or Path(hit.get("path", "")).exists()):
        return hit
    key = env("PEXELS_API_KEY")
    cands: list[dict] = []
    if not photo_only:
        with cost.timed("stock:search", "magnific", app_id=app_id):
            for m in _magnific_search(query):
                m["bonus"] = 0.15 - (0.1 if m["quality"] == "4k" else 0.0)   # Magnific stock first; 1080p over 4K
                cands.append(m)
    if key:
        with cost.timed("stock:search", "pexels", app_id=app_id):
            if not photo_only:
                data = _get("https://api.pexels.com/videos/search", headers={"Authorization": key},
                            params={"query": query, "orientation": "portrait", "per_page": 20, "size": "medium"})
                for v in (data or {}).get("videos", []):
                    files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"
                             and min(f.get("width") or 0, f.get("height") or 0) >= 720]
                    if files:
                        f = min(files, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))
                        cands.append({"kind": "video", "url": f["link"], "ext": ".mp4", "desc": v.get("url", ""),
                                      "attribution": f"Video by {v.get('user', {}).get('name', 'Pexels creator')} "
                                                     f"on Pexels ({v.get('url')})"})
            data = _get("https://api.pexels.com/v1/search", headers={"Authorization": key},
                        params={"query": query, "orientation": "portrait", "per_page": 20})
            for p in (data or {}).get("photos", []):
                cands.append({"kind": "image", "url": p["src"]["large2x"], "ext": ".jpg",
                              "desc": f"{p.get('url', '')} {p.get('alt', '')}",
                              "attribution": f"Photo by {p.get('photographer', 'Pexels creator')} on Pexels ({p.get('url')})"})
    for cnd in cands:
        cnd["match"] = round(match_score(query, cnd["desc"]) + (0.1 if india_score(cnd["desc"]) else 0.0), 3)
        cnd["rank"] = cnd["match"] + cnd.get("bonus", 0.0)
    cands.sort(key=lambda x: (-x["rank"], x["kind"] != "video"))
    res: dict = {"kind": "none", "path": "", "attribution": "", "match": 0.0}
    for cnd in cands[:3]:
        if cnd.get("mid"):           # Magnific clip: cached by id; the download link is only requested when it is used
            dest = CACHE_DIR / "stock" / f"magnific_{cnd['mid']}{cnd['ext']}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not (dest.exists() and dest.stat().st_size > 0):
                cnd["url"] = _magnific_resolve(cnd["mid"])
                if not cnd["url"]:
                    continue
        else:
            dest = CACHE_DIR / "stock" / f"{cache.key(cnd['url'])}{cnd['ext']}"
            dest.parent.mkdir(parents=True, exist_ok=True)
        if _download(cnd.get("url", ""), dest):
            if cnd.get("mid"):
                _shrink(dest)
            res = {"kind": cnd["kind"], "path": str(dest), "attribution": cnd["attribution"], "match": cnd["match"],
                   "desc": cnd["desc"][:200]}
            break
    cache.put_json("stock", k, res)
    return res
