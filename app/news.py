"""Pulls current affairs from GNews (optional) and publisher RSS feeds, then
cleans, de-duplicates and sorts them into one list of cards."""
import asyncio
import hashlib
import html
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import feedparser
import httpx

from .sources import CATEGORIES

CACHE_TTL = int(os.getenv("PULSE_CACHE_SECONDS", "600"))  # 10 minutes
HEADERS = {"User-Agent": "Mozilla/5.0 (PulseNewsReader/1.0; +https://localhost)"}
_cache: dict[str, tuple[float, list[dict]]] = {}

# Feed titles are often descriptions ("Technology News Today, Latest Tech News"),
# so name the publisher from the story's web address instead.
PUBLISHERS = {
    "bbc.co.uk": "BBC News", "bbc.com": "BBC News", "thehindu.com": "The Hindu",
    "timesofindia.indiatimes.com": "Times of India", "economictimes.indiatimes.com": "Economic Times",
    "indianexpress.com": "Indian Express", "aljazeera.com": "Al Jazeera", "techcrunch.com": "TechCrunch",
    "espncricinfo.com": "ESPNcricinfo", "cricinfo.com": "ESPNcricinfo",
}


def publisher(url: str, fallback: str | None) -> str | None:
    host = (urlparse(url or "").hostname or "").removeprefix("www.")
    name = next((v for k, v in PUBLISHERS.items() if host == k or host.endswith("." + k)), fallback)
    if name == "BBC News" and "/sport/" in (url or ""):
        return "BBC Sport"
    return name


TAG_RE = re.compile(r"<[^>]+>")
IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)


def _id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:20]


def _clean(text: str | None, limit: int = 320) -> str:
    if not text:
        return ""
    text = html.unescape(TAG_RE.sub(" ", text))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return text


def _bigger_image(url: str | None) -> str | None:
    if not url:
        return None
    # BBC serves 240px thumbnails in RSS; ask for a larger rendition.
    url = re.sub(r"/ace/(standard|ws)/\d+/", r"/ace/\1/976/", url)
    return url


def _iso(value) -> str | None:
    if not value:
        return None
    try:
        if isinstance(value, time.struct_time):
            return datetime(*value[:6], tzinfo=timezone.utc).isoformat()
        if isinstance(value, str):
            try:
                return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None
    return None


def _entry_image(e) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key, []) or []:
            if m.get("url") and (m.get("medium") in (None, "image") or "image" in (m.get("type") or "image")):
                return m["url"]
    for link in e.get("links", []) or []:
        if link.get("rel") == "enclosure" and (link.get("type") or "").startswith("image"):
            return link.get("href")
    for enc in e.get("enclosures", []) or []:
        if (enc.get("type") or "").startswith("image"):
            return enc.get("href")
    for field in ("summary", "description"):
        m = IMG_RE.search(e.get(field) or "")
        if m:
            return m.group(1)
    for c in e.get("content", []) or []:
        m = IMG_RE.search(c.get("value") or "")
        if m:
            return m.group(1)
    return None


async def _fetch_url(client: httpx.AsyncClient, url: str, params: dict | None = None) -> bytes | None:
    try:
        r = await client.get(url, params=params, headers=HEADERS, timeout=10, follow_redirects=True)
        if r.status_code == 200:
            return r.content
    except httpx.HTTPError:
        pass
    return None


def _parse_rss(raw: bytes, category: str) -> list[dict]:
    feed = feedparser.parse(raw)
    source = _clean(feed.feed.get("title"), 60) or "News"
    source = re.sub(r"\s*[-|:].*$", "", source) or source  # "BBC News - World" -> "BBC News"
    out = []
    for e in feed.entries[:25]:
        link, title = e.get("link"), _clean(e.get("title"), 200)
        if not link or not title:
            continue
        name = publisher(link, source)
        out.append({
            "id": _id(link),
            "title": title,
            "summary": _clean(e.get("summary") or e.get("description")),
            "url": link,
            "image_url": _bigger_image(_entry_image(e)),
            "source": name,
            "category": category,
            "provider": "rss",
            "published_at": _iso(e.get("published_parsed") or e.get("updated_parsed") or e.get("published")),
        })
    return out


async def _gnews(client: httpx.AsyncClient, category: str) -> list[dict]:
    key = os.getenv("GNEWS_API_KEY")
    if not key:
        return []
    cfg = CATEGORIES[category]["gnews"]
    params = {"category": cfg["category"], "lang": "en", "max": 25, "apikey": key}
    if cfg.get("country"):
        params["country"] = cfg["country"]
    raw = await _fetch_url(client, "https://gnews.io/api/v4/top-headlines", params)
    if not raw:
        return []
    try:
        import json
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    for a in data.get("articles", []):
        if not a.get("url") or not a.get("title"):
            continue
        out.append({
            "id": _id(a["url"]),
            "title": _clean(a["title"], 200),
            "summary": _clean(a.get("description") or a.get("content")),
            "url": a["url"],
            "image_url": a.get("image"),
            "source": (a.get("source") or {}).get("name") or "GNews",
            "category": category,
            "provider": "gnews",
            "published_at": _iso(a.get("publishedAt")),
        })
    return out


def dedupe(items: list[dict]) -> list[dict]:
    seen_ids, seen_titles, out = set(), set(), []
    for it in items:
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
        if it["id"] in seen_ids or key in seen_titles:
            continue
        seen_ids.add(it["id"])
        seen_titles.add(key)
        out.append(it)
    return out


def interleave(groups: list[list[dict]]) -> list[dict]:
    """Mix sources so the deck isn't ten BBC cards in a row, while keeping newest first overall."""
    for g in groups:
        g.sort(key=lambda x: x["published_at"] or "", reverse=True)
    out, i = [], 0
    while any(i < len(g) for g in groups):
        for g in groups:
            if i < len(g):
                out.append(g[i])
        i += 1
    return out


async def get_articles(category: str, refresh: bool = False) -> list[dict]:
    if category not in CATEGORIES:
        raise KeyError(category)
    hit = _cache.get(category)
    if hit and not refresh and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    async with httpx.AsyncClient() as client:
        rss_urls = CATEGORIES[category]["rss"]
        results = await asyncio.gather(
            _gnews(client, category),
            *[_fetch_url(client, u) for u in rss_urls],
        )
    groups = [results[0]] + [_parse_rss(raw, category) for raw in results[1:] if raw]
    items = dedupe(interleave([g for g in groups if g]))
    _cache[category] = (time.time(), items)
    return items
