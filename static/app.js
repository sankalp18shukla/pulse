/* Pulse front end — plain JavaScript, no build step.
 *
 * Swiping (either way) moves to the next story and records it as skipped.
 * Bookmarking is a separate toggle: it saves the current story without moving on.
 * Tapping a card opens a summary built from the article's own sentences.
 * The day picker shows stories published on a chosen day.
 */
const $ = (s) => document.querySelector(s);
const deck = $("#deck");
const sheet = $("#sheet");

const TZ = -new Date().getTimezoneOffset(); // minutes ahead of UTC, e.g. 330 for IST

const state = {
  categories: [],
  category: "top",
  day: isoDay(new Date()),
  today: isoDay(new Date()),
  oldest: null,
  dayCounts: {},
  queue: [],    // articles still to see; queue[0] is the top card
  history: [],  // [{ article, recorded }] for undo; recorded = a skip was written
  busy: false,
  sheetArticle: null,
  savedDirty: false,
};

const SWIPE_THRESHOLD = 110;
const BM_ICON = `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M7 4h10v16l-5-3.5L7 20z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>`;
const DOC_ICON = `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M5 6h14M5 12h14M5 18h9" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>`;

/* ---------- helpers ---------- */
function isoDay(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function parseDay(s) {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}
function shiftDay(s, n) {
  const d = parseDay(s);
  d.setDate(d.getDate() + n);
  return isoDay(d);
}
function dayWords(s) {
  if (s === state.today) return "Today";
  if (s === shiftDay(state.today, -1)) return "Yesterday";
  return parseDay(s).toLocaleDateString(undefined, { weekday: "long" });
}
function dayLong(s) {
  return parseDay(s).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

function toast(msg) {
  const t = $("#toast");
  (sheet.open ? sheet : document.body).append(t); // stay visible above the open sheet
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 1800);
}

function timeAgo(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function label() {
  return state.categories.find((c) => c.id === state.category)?.label || "";
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function setSavedCount(n) {
  if (n == null) return;
  $("#savedCount").textContent = n;
  $("#savedSub").textContent = n ? `${n} ${n === 1 ? "story" : "stories"} you bookmarked.` : "Stories you bookmarked.";
}

// Server writes run one after another, so a bookmark toggle followed by a swipe
// can't land in the wrong order.
let chain = Promise.resolve();
function api(method, url, body) {
  const run = async () => {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.saved != null) setSavedCount(data.saved);
    return { ok: res.ok, data };
  };
  const p = chain.then(run, run);
  chain = p.catch(() => {});
  return p;
}

/* ---------- day picker ---------- */
function renderDayNav() {
  $("#dayRel").textContent = dayWords(state.day);
  const sameYear = parseDay(state.day).getFullYear() === parseDay(state.today).getFullYear();
  $("#dayDate").textContent = parseDay(state.day).toLocaleDateString(undefined,
    { day: "numeric", month: "long", ...(sameYear ? {} : { year: "numeric" }) });
  $("#dayPrev").disabled = !!state.oldest && state.day <= state.oldest;
  $("#dayNext").disabled = state.day >= state.today;
  const input = $("#dayInput");
  input.value = state.day;
  input.max = state.today;
  if (state.oldest) input.min = state.oldest;
}

function setDay(day) {
  if (!day) return;
  if (day > state.today) day = state.today;
  if (state.oldest && day < state.oldest) day = state.oldest;
  if (day === state.day) return;
  state.day = day;
  renderDayNav();
  loadFeed(false);
}

/* ---------- cards ---------- */
function fallbackMedia(media, a) {
  media.classList.add("noimg");
  media.append(el("span", "initial", (a.source || "N")[0]));
}

function buildImage(a, media) {
  if (a.image_url) {
    const img = el("img");
    img.src = a.image_url;
    img.alt = "";
    img.referrerPolicy = "no-referrer";
    img.onerror = () => { img.remove(); fallbackMedia(media, a); };
    media.append(img);
  } else {
    fallbackMedia(media, a);
  }
}

function buildCard(a) {
  const card = el("article", "card");
  card.dataset.id = a.id;
  card.setAttribute("aria-label", a.title);

  const media = el("div", "card-media");
  buildImage(a, media);

  const body = el("div", "card-body");
  body.append(el("h2", "", a.title));
  const meta = el("div", "card-meta");
  meta.append(el("time", "", timeAgo(a.published_at)));
  const hint = el("span", "card-hint");
  hint.innerHTML = DOC_ICON;
  hint.append("Summary");
  meta.append(hint);
  body.append(meta);

  const bm = el("button", "card-bm");
  bm.innerHTML = BM_ICON;
  bm.addEventListener("pointerdown", (e) => e.stopPropagation()); // don't start a drag
  bm.addEventListener("click", (e) => { e.stopPropagation(); toggleBookmark(a); });

  card.append(media, el("span", "card-source", a.source || "News"), bm, body, el("span", "card-fade"));
  return card;
}

// Show the whole headline: if a long one would push past the card, step its size down.
function fitTitle(card) {
  const h2 = card.querySelector(".card-body h2");
  const body = card.querySelector(".card-body");
  if (!h2 || !card.clientHeight) return;
  h2.style.fontSize = "";
  let size = parseFloat(getComputedStyle(h2).fontSize);
  const room = card.clientHeight - 56; // keep the source pill and bookmark clear
  while (body.offsetHeight > room && size > 12) {
    size -= 0.5;
    h2.style.fontSize = `${size}px`;
  }
}
document.fonts?.ready.then(() => deck.querySelectorAll(".card").forEach(fitTitle));
let fitTimer;
window.addEventListener("resize", () => {
  clearTimeout(fitTimer);
  fitTimer = setTimeout(() => deck.querySelectorAll(".card").forEach(fitTitle), 120);
});

function syncBookmark() {
  const a = state.queue[0];
  const on = !!a?.bookmarked;
  const btn = $("#saveBtn");
  btn.setAttribute("aria-pressed", String(on));
  btn.setAttribute("aria-label", on ? "Remove bookmark (B)" : "Bookmark story (B)");
  btn.title = on ? "Remove bookmark (B)" : "Bookmark (B)";
  if (a) {
    const cb = deck.querySelector(`.card[data-id="${a.id}"]:not(.leaving) .card-bm`);
    if (cb) {
      cb.setAttribute("aria-pressed", String(on));
      cb.setAttribute("aria-label", on ? "Remove bookmark" : "Bookmark this story");
      cb.title = on ? "Remove bookmark" : "Bookmark";
    }
  }
  syncSheetBookmark();
}

function setControls(enabled) {
  for (const id of ["skipBtn", "saveBtn", "openBtn"]) $("#" + id).disabled = !enabled;
  $("#undoBtn").disabled = !state.history.length;
}

function renderDeck() {
  deck.querySelectorAll(".state, .skeleton").forEach((n) => n.remove());
  const visible = state.queue.slice(0, 3);
  const keep = new Set(visible.map((a) => a.id));
  deck.querySelectorAll(".card:not(.leaving)").forEach((c) => { if (!keep.has(c.dataset.id)) c.remove(); });

  visible.forEach((a, depth) => {
    let card = deck.querySelector(`.card[data-id="${a.id}"]:not(.leaving)`);
    if (!card) {
      card = buildCard(a);
      card.dataset.depth = "2";
      card.style.opacity = "0";
      deck.append(card);
      card.getBoundingClientRect(); // register start state so it animates in
      card.style.opacity = "";
      fitTitle(card);
    }
    card.dataset.depth = String(depth);
    card.style.zIndex = String(10 - depth);
    card.classList.toggle("top", depth === 0);
    card.querySelector(".card-bm").tabIndex = depth === 0 ? 0 : -1;
    if (depth === 0 && !card._drag) attachDrag(card);
    if (!a.image_url) fetchSummary(a);
  });

  if (!state.queue.length) showEmpty();
  $("#remaining").textContent = state.queue.length
    ? `${state.queue.length} ${state.queue.length === 1 ? "story" : "stories"} left in ${label()}`
    : "";
  $("#dayCount").textContent = state.queue.length ? `· ${state.queue.length} left` : "";
  setControls(state.queue.length > 0);
  syncBookmark();
}

function showEmpty(kind = "done") {
  setControls(false);
  const box = el("div", "state");
  const inner = el("div");
  const row = el("div", "row");
  const isToday = state.day === state.today;
  if (kind === "error") {
    inner.append(el("h2", "", "Couldn't reach the news sources"), el("p", "", "Check your internet connection, then try again."));
    const b = el("button", "btn solid", "Try again");
    b.onclick = () => loadFeed(true);
    row.append(b);
  } else {
    const when = isToday ? "today" : dayLong(state.day);
    inner.append(el("h2", "", `Nothing left in ${label()} for ${when}`),
      el("p", "", isToday
        ? "You've seen everything so far. Check for new stories, try another category, or look at an earlier day."
        : "You've seen these, or Pulse didn't collect stories from that day. Try another day or category."));
    if (isToday) {
      const r = el("button", "btn solid", "Check for new stories");
      r.onclick = () => loadFeed(true);
      row.append(r);
    }
    const prev = shiftDay(state.day, -1);
    if (!state.oldest || prev >= state.oldest) {
      const p = el("button", isToday ? "btn" : "btn solid", `Go to ${prev === shiftDay(state.today, -1) ? "yesterday" : dayLong(prev)}`);
      p.onclick = () => setDay(prev);
      row.append(p);
    }
    if (!isToday) {
      const t = el("button", "btn", "Back to today");
      t.onclick = () => setDay(state.today);
      row.append(t);
    }
    const s = el("button", "btn", "Show skipped stories");
    s.onclick = async () => { await api("DELETE", "/api/skips"); state.history = []; loadFeed(false); };
    row.append(s);
  }
  inner.append(row);
  box.append(inner);
  deck.append(box);
}

/* ---------- drag to swipe ---------- */
function attachDrag(card) {
  card._drag = true;
  let startX = 0, startY = 0, dx = 0, dy = 0, dragging = false, startT = 0;

  card.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !card.classList.contains("top")) return;
    dragging = true;
    startX = e.clientX; startY = e.clientY; dx = dy = 0; startT = performance.now();
    card.setPointerCapture(e.pointerId);
    card.classList.add("dragging");
  });

  card.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    dx = e.clientX - startX;
    dy = e.clientY - startY;
    card.style.transform = `translate(${dx}px, ${dy * 0.35}px) rotate(${dx * 0.05}deg)`;
  });

  const end = (e) => {
    if (!dragging) return;
    dragging = false;
    card.classList.remove("dragging");
    if (e.type === "pointercancel") { card.style.transform = ""; return; } // the page scrolled instead
    const velocity = Math.abs(dx) / Math.max(1, performance.now() - startT);
    if (Math.abs(dx) > SWIPE_THRESHOLD || (velocity > 0.6 && Math.abs(dx) > 40)) {
      next(dx > 0 ? 1 : -1, dy * 0.35);
    } else {
      card.style.transform = "";
      if (Math.abs(dx) < 4 && Math.abs(dy) < 4) openSheet(state.queue[0]); // a tap shows the summary
    }
  };
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}

function openFull(a = state.queue[0]) {
  if (a) window.open(a.url, "_blank", "noopener");
}

function next(dir = -1, dy = 0) {
  const article = state.queue[0];
  if (!article || state.busy) return;
  const card = deck.querySelector(`.card[data-id="${article.id}"]:not(.leaving)`);
  if (card) {
    card.classList.add("leaving");
    card.classList.remove("top");
    card.style.transition = "transform .42s cubic-bezier(.3,.6,.3,1), opacity .42s";
    card.style.transformOrigin = "50% 100%";
    card.style.transform = `translate(${dir * (window.innerWidth * 0.6 + 200)}px, ${dy - 30}px) rotate(${dir * 22}deg)`;
    card.style.opacity = "0";
    setTimeout(() => card.remove(), 440);
  }
  state.queue.shift();
  // A bookmarked story is already saved, so it needs no skip.
  const recorded = !article.bookmarked;
  state.history.push({ article, recorded });
  if (state.history.length > 30) state.history.shift();
  bumpCount(-1);
  renderDeck();

  if (recorded) {
    api("POST", "/api/swipe", { article_id: article.id, action: "skip" })
      .catch(() => toast("Couldn't record that. Is the server running?"));
  }
}

function bumpCount(n) {
  if (state.dayCounts[state.day] != null) {
    state.dayCounts[state.day] = Math.max(0, state.dayCounts[state.day] + n);
    renderDayNav();
  }
}

async function toggleBookmark(a = state.queue[0]) {
  if (!a || state.busy) return;
  a.bookmarked = !a.bookmarked;
  syncBookmark();
  toast(a.bookmarked ? "Bookmarked" : "Bookmark removed");
  try {
    const { ok } = a.bookmarked
      ? await api("POST", "/api/swipe", { article_id: a.id, action: "save" })
      : await api("DELETE", `/api/saved/${a.id}`);
    if (!ok) throw new Error();
    state.savedDirty = true;
  } catch {
    a.bookmarked = !a.bookmarked;
    syncBookmark();
    toast("Couldn't update the bookmark. Is the server running?");
  }
}

function undo() {
  const last = state.history.pop();
  if (!last) return;
  if (last.recorded) api("DELETE", `/api/saved/${last.article.id}`).catch(() => {});
  state.queue.unshift(last.article);
  bumpCount(1);
  renderDeck();
  toast("Brought back");
}

/* ---------- summary sheet ---------- */
const summaryCache = new Map(); // article id -> Promise of summary data (or null)

function fetchSummary(a) {
  if (!summaryCache.has(a.id)) {
    const p = fetch(`/api/summary/${a.id}`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((d) => {
        if (!d?.points?.length) summaryCache.delete(a.id); // allow a retry later
        if (d?.image_url && !a.image_url) { a.image_url = d.image_url; showCardImage(a); }
        return d;
      });
    summaryCache.set(a.id, p);
  }
  return summaryCache.get(a.id);
}

// Feeds like TechCrunch's carry no picture; the article page's preview image fills in.
function showCardImage(a) {
  const media = deck.querySelector(`.card[data-id="${a.id}"] .card-media`);
  if (!media || media.querySelector("img")) return;
  const img = el("img");
  img.alt = ""; img.referrerPolicy = "no-referrer";
  img.style.opacity = "0"; img.style.transition = "opacity .4s";
  img.onload = () => { media.classList.remove("noimg"); media.querySelector(".initial")?.remove(); img.style.opacity = "1"; };
  img.onerror = () => img.remove();
  img.style.position = "absolute"; img.style.inset = "0";
  img.src = a.image_url;
  media.append(img);
}

function syncSheetBookmark() {
  const a = state.sheetArticle;
  if (!a) return;
  const b = $("#sheetBm");
  b.setAttribute("aria-pressed", String(!!a.bookmarked));
  b.querySelector("span").textContent = a.bookmarked ? "Bookmarked" : "Bookmark";
}

function setSheetImage(a) {
  const media = $("#sheetMedia");
  const img = el("img");
  img.src = a.image_url; img.alt = ""; img.referrerPolicy = "no-referrer";
  img.onerror = () => { img.remove(); media.hidden = true; };
  media.append(img);
  media.hidden = false;
}

async function openSheet(a) {
  if (!a) return;
  state.sheetArticle = a;
  const media = $("#sheetMedia");
  media.innerHTML = "";
  media.hidden = true;
  if (a.image_url) setSheetImage(a);
  $("#sheetMeta").textContent = [a.source || "News", timeAgo(a.published_at)].filter(Boolean).join(" · ");
  $("#sheetTitle").textContent = a.title;
  $("#sheetOpen").href = a.url;
  const list = $("#sheetPoints");
  list.innerHTML = "";
  for (let i = 0; i < 3; i++) list.append(el("li", "loading"));
  $("#sheetNote").textContent = "Reading the article…";
  syncSheetBookmark();
  if (!sheet.open) sheet.showModal();
  $("#sheetClose").focus();

  const data = await fetchSummary(a);
  if (state.sheetArticle !== a) return; // another story was opened meanwhile
  if (a.image_url && !media.querySelector("img")) setSheetImage(a);

  list.innerHTML = "";
  const points = data?.points?.length ? data.points : (a.summary ? [a.summary] : []);
  if (!points.length) {
    list.append(el("li", "", "This publisher didn't share a summary. Open the full story to read it."));
  }
  for (const p of points) list.append(el("li", "", p));
  const meta = $("#sheetMeta");
  if (data?.read_minutes) meta.textContent += ` · ${data.read_minutes} min read`;
  $("#sheetNote").textContent = data?.method === "article"
    ? "Key sentences picked from the article itself, in the order they appear."
    : "From the publisher's own description of the story.";
}

function closeSheet() {
  if (sheet.open) sheet.close();
}

sheet.addEventListener("close", () => {
  state.sheetArticle = null;
  if (state.savedDirty && !$("#savedView").hidden) renderSaved();
});
sheet.addEventListener("click", (e) => { if (e.target === sheet) closeSheet(); }); // backdrop
$("#sheetClose").onclick = closeSheet;

// Phones: pull the sheet down by its top edge to close it.
(function sheetDrag() {
  const handle = $("#sheetHandle");
  const panel = sheet.querySelector(".sheet-panel");
  let y0 = null, dy = 0;
  handle.addEventListener("pointerdown", (e) => {
    y0 = e.clientY; dy = 0;
    handle.setPointerCapture(e.pointerId);
    panel.style.transition = "none";
  });
  handle.addEventListener("pointermove", (e) => {
    if (y0 == null) return;
    dy = Math.max(0, e.clientY - y0);
    panel.style.transform = `translateY(${dy}px)`;
  });
  const end = () => {
    if (y0 == null) return;
    y0 = null;
    panel.style.transition = "transform .25s cubic-bezier(.2,.8,.2,1)";
    if (dy > 90) {
      panel.style.transform = "translateY(100%)";
      setTimeout(() => { closeSheet(); panel.style.transform = ""; panel.style.transition = ""; }, 220);
    } else {
      panel.style.transform = "";
    }
  };
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
})();
$("#sheetBm").onclick = () => toggleBookmark(state.sheetArticle);

/* ---------- data ---------- */
async function loadFeed(refresh = false) {
  state.busy = true;
  deck.innerHTML = "";
  deck.append(el("div", "skeleton"));
  setControls(false);
  $("#remaining").textContent = `Loading ${label()}…`;
  const btn = $("#refreshTop");
  if (refresh) btn.classList.add("spin");
  const want = `${state.category}|${state.day}`;
  try {
    const q = new URLSearchParams({ category: state.category, day: state.day, tz: TZ, refresh: refresh ? 1 : 0 });
    const res = await fetch(`/api/feed?${q}`);
    const data = await res.json();
    if (want !== `${state.category}|${state.day}`) return; // user moved on while this loaded
    state.today = data.today || state.today;
    state.oldest = data.oldest || state.oldest;
    state.dayCounts = data.days || {};
    state.dayCounts[data.day] = (data.articles || []).length;
    state.queue = data.articles || [];
    state.history = [];
    deck.innerHTML = "";
    renderDayNav();
    if (!data.sources_ok) { showEmpty("error"); $("#remaining").textContent = ""; return; }
    renderDeck();
  } catch {
    deck.innerHTML = "";
    showEmpty("error");
    $("#remaining").textContent = "";
  } finally {
    state.busy = false;
    btn.classList.remove("spin");
  }
}

function selectCategory(id) {
  state.category = id;
  try { localStorage.setItem("pulse.category", id); } catch {}
  document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.id === id)));
  document.querySelector(`.chip[data-id="${id}"]`)?.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
  loadFeed(false);
}

/* ---------- views: Discover / Saved ---------- */
function showView(view) {
  const saved = view === "saved";
  $("#tabDiscover").setAttribute("aria-selected", String(!saved));
  $("#tabSaved").setAttribute("aria-selected", String(saved));
  $("#discoverView").hidden = saved;
  $("#savedView").hidden = !saved;
  if (saved) renderSaved();
}

async function renderSaved() {
  state.savedDirty = false;
  const list = $("#savedList");
  let items = [];
  try { items = await (await fetch("/api/saved")).json(); } catch {}
  list.innerHTML = "";
  setSavedCount(items.length);
  if (!items.length) {
    list.append(el("li", "saved-empty", "Nothing bookmarked yet. Tap the bookmark on a story to keep it here."));
    return;
  }
  for (const a of items) {
    a.bookmarked = true;
    const li = el("li", "saved-item");
    const open = el("button", "saved-open");
    open.setAttribute("aria-label", `Summary: ${a.title}`);
    const img = el("img", "thumb");
    img.alt = "";
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    if (a.image_url) img.src = a.image_url;
    img.onerror = () => img.removeAttribute("src");
    const text = el("div");
    text.append(el("strong", "", a.title), el("small", "", `${a.source || "News"} · saved ${timeAgo(a.saved_at)}`));
    open.append(img, text);
    open.onclick = () => openSheet(a);
    const rm = el("button", "rm");
    rm.setAttribute("aria-label", "Remove bookmark");
    rm.title = "Remove bookmark";
    rm.innerHTML = BM_ICON;
    rm.onclick = async () => {
      await api("DELETE", `/api/saved/${a.id}`);
      toast("Bookmark removed");
      renderSaved();
    };
    li.append(open, rm);
    list.append(li);
  }
}

/* ---------- wiring ---------- */
$("#skipBtn").onclick = () => next(-1);
$("#saveBtn").onclick = () => toggleBookmark();
$("#openBtn").onclick = () => openFull();
$("#undoBtn").onclick = undo;
$("#refreshTop").onclick = () => { showView("discover"); state.today = isoDay(new Date()); loadFeed(true); };
$("#tabDiscover").onclick = () => showView("discover");
$("#tabSaved").onclick = () => showView("saved");
$("#dayPrev").onclick = () => setDay(shiftDay(state.day, -1));
$("#dayNext").onclick = () => setDay(shiftDay(state.day, 1));
$("#dayInput").addEventListener("change", (e) => setDay(e.target.value));
$("#dayInput").addEventListener("click", (e) => { try { e.target.showPicker(); } catch {} });

document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, select, textarea")) return;
  const k = e.key.toLowerCase();
  if (sheet.open) {
    if (k === "b") { e.preventDefault(); toggleBookmark(state.sheetArticle); }
    return; // Esc is handled by the dialog
  }
  if ($("#discoverView").hidden) return;
  if (e.key === "ArrowRight") { e.preventDefault(); next(1); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); next(-1); }
  else if (k === "b") { e.preventDefault(); toggleBookmark(); }
  else if ((e.key === "Enter" || e.key === " ") && e.target === document.body) { e.preventDefault(); openSheet(state.queue[0]); }
  else if (e.key === "Backspace" || k === "z") { e.preventDefault(); undo(); }
});

(async function boot() {
  renderDayNav();
  try {
    state.categories = await (await fetch("/api/categories")).json();
  } catch {
    state.categories = [{ id: "top", label: "Top stories" }];
  }
  const chips = $("#chips");
  for (const c of state.categories) {
    const b = el("button", "chip", c.label);
    b.dataset.id = c.id;
    b.setAttribute("aria-pressed", "false");
    b.onclick = () => selectCategory(c.id);
    chips.append(b);
  }
  let saved = "top";
  try { saved = localStorage.getItem("pulse.category") || "top"; } catch {}
  if (!state.categories.some((c) => c.id === saved)) saved = state.categories[0].id;
  fetch("/api/stats").then((r) => r.json()).then((s) => setSavedCount(s.saved)).catch(() => {});
  selectCategory(saved);
})();
