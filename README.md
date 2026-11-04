# Pulse: current affairs as swipeable cards

Pulse pulls the latest news from publisher RSS feeds and, optionally, the GNews API. It shows one story at a time as a card.

- **Swipe** either way (or press ← / →) to move to the next story.
- **Bookmark** (the icon on the card, the black button, or **B**) saves a story without moving on.
- **Tap a card** (or press Enter) to read a short summary without leaving Pulse.
- **Undo** (Backspace) brings back the last card.
- **Pick a day** with the arrows above the categories to see stories from earlier days.

Bookmarked stories are listed under **Saved**. Skipped stories don't come back unless you ask.

### Summaries (no AI, no API)

When you open a story, Pulse fetches the publisher's page, finds the article body
(the block of the page holding most of the long paragraphs, so promos and related-story
links are left out) and keeps the 4 sentences that score highest. A sentence scores well
when it uses the article's most frequent words, shares words with the headline and comes
early in the story. Sentences are shown word for word, in their original order. If a site
blocks the request, the feed's own description is shown instead. Summaries are cached in
the database. See `app/summary.py`.

### Earlier days

Pulse keeps every story it fetches, so each day you use it adds to its history. Going back
shows stories published on that day (in your time zone), up to 14 days. Days before you
started using Pulse will only have the few older items the feeds still carry.

## Run it

You need Python 3.10 or newer. No API key is required.

**Windows:** double-click `run.bat`.
**Mac / Linux:** `./run.sh`

Then open **http://localhost:8100**.

For more stories with images, get a free key from https://gnews.io/ and add it to `.env` as `GNEWS_API_KEY=...`. The free tier allows 100 requests a day, and Pulse caches results for 10 minutes to stay under that.

## Sources

Categories: Top stories, India, World, Business, Tech, Science, Sports, Health.
Publishers include BBC News, The Hindu, Times of India, Indian Express, Economic Times, Al Jazeera, TechCrunch and ESPNcricinfo. Edit `app/sources.py` to add or remove feeds. A feed that's down is skipped, and the rest still load.

Stories from all sources are cleaned (HTML stripped, summaries trimmed), de-duplicated by URL and by headline, sorted newest first, and interleaved so one publisher doesn't fill the deck.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/categories` | Category list |
| GET | `/api/feed?category=tech&day=2026-11-03&tz=330&refresh=1` | Unswiped stories in a category published on a day (`tz` = minutes ahead of UTC) |
| GET | `/api/summary/{article_id}` | Key sentences from the article, plus read time |
| POST | `/api/swipe` | `{article_id, action: "save" \| "skip"}` (save = bookmark) |
| GET | `/api/saved` | Saved stories |
| DELETE | `/api/saved/{article_id}` | Remove a swipe (used for undo and unsave) |
| DELETE | `/api/skips` | Bring back all skipped stories |
| GET | `/api/stats` | Saved / skipped counts |

## Database

```
articles (id PK = sha1(url), title, summary, url, image_url, source, category,
          provider, published_at, fetched_at)
swipes   (id PK, article_id FK UNIQUE → articles.id, action CHECK save|skip, created_at)
summaries (article_id PK FK → articles.id, points JSON, method article|feed, read_minutes, created_at)
```
One article has zero or one swipe (1 : 0..1).
