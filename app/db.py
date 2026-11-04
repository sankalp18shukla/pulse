"""SQLite storage for Pulse.

  articles - every story Pulse has fetched (acts as a cache and history)
  swipes   - the user's decision on a story: 'save' (bookmarked) or 'skip' (swiped past)
  summaries - cached extractive summaries, one per article
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

# Serverless hosts (Vercel) only allow writing to /tmp, and it is wiped when the
# instance is recycled. Locally the database sits next to the code.
_DEFAULT_DB = "/tmp/pulse.db" if os.getenv("VERCEL") else os.path.join(os.path.dirname(__file__), "..", "pulse.db")
DB_PATH = os.getenv("PULSE_DB", _DEFAULT_DB)

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id           TEXT PRIMARY KEY,          -- sha1 of the article URL
    title        TEXT NOT NULL,
    summary      TEXT,
    url          TEXT NOT NULL,
    image_url    TEXT,
    source       TEXT,
    category     TEXT NOT NULL,
    provider     TEXT NOT NULL,             -- 'gnews' or 'rss'
    published_at TEXT,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS swipes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT NOT NULL UNIQUE REFERENCES articles(id) ON DELETE CASCADE,
    action     TEXT NOT NULL CHECK (action IN ('save', 'skip')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
    article_id   TEXT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    points       TEXT NOT NULL,             -- JSON list of sentences
    method       TEXT NOT NULL,             -- 'article' or 'feed'
    read_minutes INTEGER,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category, published_at);
CREATE INDEX IF NOT EXISTS idx_swipes_action ON swipes(action);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def upsert_articles(items: list[dict]) -> None:
    with connect() as conn:
        conn.executemany(
            """INSERT INTO articles (id, title, summary, url, image_url, source, category, provider, published_at, fetched_at)
               VALUES (:id, :title, :summary, :url, :image_url, :source, :category, :provider, :published_at, :fetched_at)
               ON CONFLICT(id) DO UPDATE SET
                   title = excluded.title, summary = excluded.summary, source = excluded.source,
                   image_url = COALESCE(excluded.image_url, articles.image_url),
                   fetched_at = excluded.fetched_at""",
            [{**i, "fetched_at": now()} for i in items],
        )


def swiped_ids() -> set[str]:
    with connect() as conn:
        return {r[0] for r in conn.execute("SELECT article_id FROM swipes")}


def record_swipe(article_id: str, action: str) -> bool:
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone():
            return False
        conn.execute(
            """INSERT INTO swipes (article_id, action, created_at) VALUES (?, ?, ?)
               ON CONFLICT(article_id) DO UPDATE SET action = excluded.action, created_at = excluded.created_at""",
            (article_id, action, now()),
        )
        return True


def saved_articles() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT a.*, s.created_at AS saved_at FROM swipes s
               JOIN articles a ON a.id = s.article_id
               WHERE s.action = 'save' ORDER BY s.created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def remove_swipe(article_id: str) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM swipes WHERE article_id = ?", (article_id,)).rowcount > 0


def clear_skips() -> int:
    with connect() as conn:
        return conn.execute("DELETE FROM swipes WHERE action = 'skip'").rowcount


def stats() -> dict:
    with connect() as conn:
        row = conn.execute(
            """SELECT SUM(action = 'save') AS saved, SUM(action = 'skip') AS skipped FROM swipes"""
        ).fetchone()
        return {"saved": row["saved"] or 0, "skipped": row["skipped"] or 0}


def recent_unswiped(category: str, since_iso: str) -> list[dict]:
    """Stories in a category published since a UTC time that the user hasn't swiped."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT a.* FROM articles a
               LEFT JOIN swipes s ON s.article_id = a.id
               WHERE s.article_id IS NULL AND a.category = ?
                 AND COALESCE(a.published_at, a.fetched_at) >= ?""",
            (category, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]


def get_article(article_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        return dict(row) if row else None


def get_summary(article_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM summaries WHERE article_id = ?", (article_id,)).fetchone()
        if not row:
            return None
        return {"points": json.loads(row["points"]), "method": row["method"], "read_minutes": row["read_minutes"]}


def put_summary(article_id: str, data: dict) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO summaries (article_id, points, method, read_minutes, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (article_id, json.dumps(data["points"]), data["method"], data.get("read_minutes"), now()),
        )


def set_image(article_id: str, image_url: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE articles SET image_url = ? WHERE id = ? AND image_url IS NULL", (image_url, article_id))
