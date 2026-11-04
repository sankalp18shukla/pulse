"""Extractive summaries without AI or third-party APIs.

Fetches the publisher's article page, pulls the body paragraphs out of the HTML,
splits them into sentences and keeps the few that score highest. A sentence
scores well when it uses the article's most frequent words, overlaps with the
headline and sits early in the story (news puts the facts first). The chosen
sentences are returned in their original order, word for word.

If the page can't be fetched or parsed, the feed's own summary is used instead.
"""
import html
import math
import re
from collections import Counter
from html.parser import HTMLParser

import httpx

PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-IN,en;q=0.9",
}
MAX_BYTES = 3_000_000
POINTS = 4

SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form",
             "figure", "figcaption", "button", "svg", "select", "template"}
BOILERPLATE = re.compile(
    r"(cookie|subscribe|sign up|sign in|newsletter|all rights reserved|©|copyright|"
    r"also read|read more|follow us|click here|download the app|whatsapp|telegram|"
    r"advertisement|terms of use|privacy policy|share this|recommended|"
    r"this article|this story|our journalism|photo credit|image credit|image source|"
    r"getty images|file photo|representative image|\| photo|log ?in|log ?out|logged|"
    r"credentials|premium|prime member|membership|subscription|unlock|paywall|"
    r"right stocks?|stock (score|ideas)|market bulls|enjoy all)", re.I)
ENDS_LIKE_SENTENCE = re.compile(r"[.!?…][\"”’)\]]*$")
STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had
has have having he her here hers herself him himself his how i if in into is it its itself just me
more most my myself no nor not now of off on once only or other our ours ourselves out over own
said says same she should so some such than that the their theirs them themselves then there these
they this those through to too under until up very was we were what when where which while who whom
why will with would you your yours yourself yourselves new one two year years per cent percent mr ms
""".split())
ABBREV = re.compile(r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Gen|Col|Lt|Capt|Sgt|Rep|Sen|Gov|Govt|No|Nos|vs|etc|Inc|Ltd|Co|Corp|Rs|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.", re.I)
INITIALS = re.compile(r"\b((?:[A-Z]\.){2,})")
SENT_SPLIT = re.compile(r"(?<=[.!?…])[\"”’)]*\s+(?=[\"“‘(]?[A-Z0-9])")
WORD = re.compile(r"[a-z][a-z'’-]+")


VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
             "source", "track", "wbr", "param"}


class _Paragraphs(HTMLParser):
    """Collects <p> text with the chain of elements it sits in, so the article body
    can be told apart from related-story teasers and sidebars."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []  # (tag, node id)
        self.next_id = 0
        self.skip = 0
        self.p_depth = None
        self.buf: list[str] = []
        self.paras: list[tuple[str, tuple[int, ...]]] = []
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            a = dict(attrs)
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("og:description", "description", "twitter:description", "og:image", "twitter:image") and a.get("content"):
                self.meta.setdefault(key, a["content"])
            return
        if tag == "br" and self.p_depth is not None:
            self.buf.append(" ")
        if tag in VOID_TAGS:
            return
        if tag == "p" and self.p_depth is not None:  # an unclosed <p> ends at the next one
            self._close_p()
        self.next_id += 1
        self.stack.append((tag, self.next_id))
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag == "p" and not self.skip:
            self.p_depth = len(self.stack)
            self.buf = []

    def handle_endtag(self, tag):
        if not any(t == tag for t, _ in self.stack):
            return
        while self.stack:
            t, _ = self.stack[-1]
            if self.p_depth is not None and len(self.stack) == self.p_depth:
                self._close_p()
            self.stack.pop()
            if t in SKIP_TAGS and self.skip:
                self.skip -= 1
            if t == tag:
                break

    def _close_p(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if text:
            ancestors = tuple(nid for _, nid in self.stack[: self.p_depth - 1])
            self.paras.append((text, ancestors))
        self.p_depth = None
        self.buf = []

    def handle_data(self, data):
        if self.p_depth is not None and not self.skip:
            self.buf.append(data)

    def article_paragraphs(self) -> list[str]:
        """Paragraphs inside the article body. Related-story teasers, promos and
        sidebars sit outside it."""
        good = [(t, anc) for t, anc in self.paras if _is_body(t)]
        if not good:
            return []
        # Each paragraph votes for every container it sits in: long paragraphs (article
        # prose) count for it, short ones (teasers, promo blurbs) count against.
        # The article body is the best-scoring container around the longest paragraph.
        value: Counter = Counter()
        for t, anc in good:
            for nid in anc:
                value[nid] += len(t) - 110
        anchor = max(good, key=lambda g: len(g[0]))[1]
        if not anchor:
            return [t for t, _ in good]
        best = max(reversed(anchor), key=lambda nid: value[nid])
        return [t for t, anc in good if best in anc]


def _is_body(p: str) -> bool:
    return len(p) >= 70 and p.count(" ") >= 10 and not BOILERPLATE.search(p)


def _body_paragraphs(paras: list[str]) -> list[str]:
    out, seen = [], set()
    for n, p in enumerate(paras):
        # Short one-sentence lines after the opening are usually promos for other stories.
        if n >= 2 and len(p) < 110 and not re.search(r"[.!?][\"”’]?\s+\S", p):
            continue
        key = p[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out[:60]


def _sentences(paras: list[str]) -> list[str]:
    out = []
    for p in paras:
        # Protect abbreviations and initials (U.S., Dr.) from the splitter.
        guarded = INITIALS.sub(lambda m: m.group(1).replace(".", "\x00"), p)
        guarded = ABBREV.sub(lambda m: m.group(0)[:-1] + "\x00", guarded)
        for s in SENT_SPLIT.split(guarded):
            s = s.replace("\x00", ".").strip()
            if 45 <= len(s) <= 320 and s.count(" ") >= 6 and ENDS_LIKE_SENTENCE.search(s) \
                    and not BOILERPLATE.search(s):
                out.append(s)
    return out


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 2]


def pick_sentences(sentences: list[str], title: str, n: int = POINTS) -> list[str]:
    if len(sentences) <= n:
        return sentences
    freq = Counter(w for s in sentences for w in set(_words(s)))
    top = max(freq.values()) if freq else 1
    title_words = set(_words(title))
    total = len(sentences)
    scored = []
    for i, s in enumerate(sentences):
        words = _words(s)
        if not words:
            continue
        content = sum(freq[w] / top for w in words) / math.sqrt(len(words))
        headline = len(title_words & set(words)) / (len(title_words) or 1)
        position = 1.0 - (i / total) ** 0.5  # the lead matters most in news writing
        scored.append((content + 1.4 * headline + 1.1 * position, i))
    chosen, used = [], []
    for _, i in sorted(scored, reverse=True):
        ws = set(_words(sentences[i]))
        # Skip near-repeats of a sentence already chosen.
        if any(len(ws & u) / (len(ws | u) or 1) > 0.5 for u in used):
            continue
        chosen.append(i)
        used.append(ws)
        if len(chosen) == n:
            break
    return [sentences[i] for i in sorted(chosen)]


async def summarize(url: str, title: str, feed_summary: str | None) -> dict:
    paras, meta = [], {}
    if url.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(headers=PAGE_HEADERS, follow_redirects=True, timeout=9) as client:
                r = await client.get(url)
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and "html" in ctype:
                parser = _Paragraphs()
                parser.feed(r.text[:MAX_BYTES])
                parser.close()
                paras, meta = _body_paragraphs(parser.article_paragraphs()), parser.meta
        except (httpx.HTTPError, ValueError):
            pass

    image = meta.get("og:image") or meta.get("twitter:image")
    image = image if image and image.startswith(("http://", "https://")) else None
    sentences = _sentences(paras)
    words = sum(p.count(" ") + 1 for p in paras)
    if len(sentences) >= 2:
        return {
            "points": pick_sentences(sentences, title),
            "method": "article",
            "read_minutes": max(1, round(words / 230)),
            "image_url": image,
        }

    # Fall back to what the feed and the page's own description say.
    fallback = []
    for text in (feed_summary, meta.get("og:description"), meta.get("description")):
        text = html.unescape((text or "").strip())
        if text and not any(text[:60] in f or f[:60] in text for f in fallback):
            fallback.append(text)
    points = _sentences(fallback) or fallback
    return {"points": points[:POINTS], "method": "feed", "read_minutes": None, "image_url": image}
