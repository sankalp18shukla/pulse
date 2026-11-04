"""Pulse API server.

Run:  uvicorn app.main:app --reload --port 8100   (from the pulse folder)
Then open http://localhost:8100
"""
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from . import db, news, summary  # noqa: E402
from .sources import CATEGORIES  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")

app = FastAPI(title="Pulse", version="1.0")
db.init_db()


class Swipe(BaseModel):
    article_id: str = Field(..., max_length=64)
    action: str = Field(..., pattern="^(save|skip)$")


@app.get("/api/categories")
def categories():
    return [{"id": k, "label": v["label"]} for k, v in CATEGORIES.items()]


HISTORY_DAYS = 14


def _local_day(iso: str | None, tz: int) -> date:
    """Calendar day of a UTC timestamp for a viewer whose clock is `tz` minutes ahead of UTC."""
    try:
        t = datetime.fromisoformat(iso) if iso else datetime.now(timezone.utc)
    except ValueError:
        t = datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (t.astimezone(timezone.utc) + timedelta(minutes=tz)).date()


@app.get("/api/feed")
async def feed(category: str = "top", refresh: bool = False, day: str | None = None, tz: int = 0):
    """Unswiped stories in a category published on one day (the viewer's local day).

    tz is minutes ahead of UTC (IST = 330). Stories come from the live feeds plus
    everything Pulse fetched earlier, so past days keep working.
    """
    if category not in news.CATEGORIES:
        raise HTTPException(404, f"Unknown category '{category}'")
    tz = max(-840, min(840, tz))
    today = _local_day(None, tz)
    try:
        wanted = date.fromisoformat(day) if day else today
    except ValueError:
        raise HTTPException(400, "day must look like 2026-11-04")
    if wanted > today:
        wanted = today

    live = await news.get_articles(category, refresh)
    if live:
        db.upsert_articles(live)

    oldest = today - timedelta(days=HISTORY_DAYS - 1)
    since = datetime.combine(oldest, datetime.min.time(), timezone.utc) - timedelta(minutes=tz)
    done = db.swiped_ids()
    pool: dict[str, dict] = {}
    for a in db.recent_unswiped(category, since.isoformat(timespec="seconds")) + live:
        if a["id"] not in done:
            pool[a["id"]] = {k: a.get(k) for k in
                             ("id", "title", "summary", "url", "image_url", "source", "category", "provider", "published_at")}
            pool[a["id"]]["source"] = news.publisher(a["url"], a.get("source"))

    by_day: dict[date, list[dict]] = defaultdict(list)
    for a in pool.values():
        d = _local_day(a["published_at"], tz)
        if oldest <= d <= today:
            by_day[d].append(a)

    groups: dict[str, list[dict]] = defaultdict(list)
    for a in by_day.get(wanted, []):
        groups[a["source"] or ""].append(a)
    articles = news.dedupe(news.interleave(list(groups.values())))

    return {
        "category": category,
        "day": wanted.isoformat(),
        "today": today.isoformat(),
        "oldest": oldest.isoformat(),
        "days": {d.isoformat(): len(v) for d, v in sorted(by_day.items())},
        "articles": articles[:60],
        "sources_ok": bool(live) or bool(pool),
        "gnews_enabled": bool(os.getenv("GNEWS_API_KEY")),
    }


@app.get("/api/summary/{article_id}")
async def article_summary(article_id: str):
    """Key sentences picked from the article itself (no AI, no external API)."""
    a = db.get_article(article_id)
    if not a:
        raise HTTPException(404, "Article not found")
    cached = db.get_summary(article_id)
    if cached:
        return {**cached, "image_url": a["image_url"]}
    data = await summary.summarize(a["url"], a["title"], a["summary"])
    if data.get("image_url") and not a["image_url"]:
        db.set_image(article_id, data["image_url"])  # feeds like TechCrunch's carry no picture
    if data["points"]:
        db.put_summary(article_id, data)
    return data


@app.post("/api/swipe")
def swipe(s: Swipe):
    if not db.record_swipe(s.article_id, s.action):
        raise HTTPException(404, "Article not found")
    return {"ok": True, **db.stats()}


@app.get("/api/saved")
def saved():
    return [{**a, "source": news.publisher(a["url"], a["source"])} for a in db.saved_articles()]


@app.delete("/api/saved/{article_id}")
def unsave(article_id: str):
    if not db.remove_swipe(article_id):
        raise HTTPException(404, "Not in your saved list")
    return {"ok": True, **db.stats()}


@app.delete("/api/skips")
def reset_skips():
    return {"cleared": db.clear_skips(), **db.stats()}


@app.get("/api/health")
def health():
    """Quick check for deployments: is the server up and can it use its database?"""
    try:
        counts = db.stats()
        db_ok, error = True, None
    except Exception as e:  # noqa: BLE001 - report any storage problem as text
        counts, db_ok, error = {}, False, f"{type(e).__name__}: {e}"
    return {"ok": db_ok, "db_path": os.path.abspath(db.DB_PATH), "on_vercel": bool(os.getenv("VERCEL")),
            "db_error": error, **counts}


@app.get("/api/stats")
def stats():
    return db.stats()


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
