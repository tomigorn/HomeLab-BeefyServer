import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, tick } from "./fixture.mjs";

const BOYS_ID = "6504de6a-8b7c-4a6a-afc7-9f1da33bbde4";
const item = (downloadId, status, title, episode) => ({ downloadId, status, title, size: 1000, sizeLeft: status === "downloading" ? 400 : 1000, timeLeft: null, estimatedCompletionTime: null, externalId: 1, mediaType: episode ? "tv" : "movie", ...(episode && { episode }) });
const BOYS_ITEM = item(BOYS_ID.toUpperCase(), "paused", "The.Boys.2019.S02.German.DL.2160p-GRP", { seasonNumber: 2, episodeNumber: 1 });
const FIGHT_ITEM = item("SABnzbd_nzo_run", "downloading", "Fight.Club.1999.1080p-GRP");
const MANUAL_ITEM = item("SABnzbd_nzo_manual", "paused", "Manual.Pause.2001.1080p-GRP");

const details = (type, id, name, poster, items, items4k = []) => ({ id, [type === "movie" ? "title" : "name"]: name, posterPath: poster, mediaInfo: { tmdbId: id, mediaType: type, status: 3, downloadStatus: items, downloadStatus4k: items4k } });
const ROUTES = {
  "/api/v1/tv/76479": details("tv", 76479, "The Boys", "/boys.jpg", [BOYS_ITEM]),
  "/api/v1/movie/550": details("movie", 550, "Fight Club", "/fight.jpg", [FIGHT_ITEM]),
  "/api/v1/movie/551": details("movie", 551, "Manual Pause", "/manual.jpg", [MANUAL_ITEM]),
  "/api/v1/tv/2": details("tv", 2, "Mixed Show", "/mixed.jpg", [item("night1", "paused", "Mixed.S01E01", { seasonNumber: 1, episodeNumber: 1 }), item("run2", "downloading", "Mixed.S01E02", { seasonNumber: 1, episodeNumber: 2 })]),
  "/api/v1/tv/3": details("tv", 3, "Four K", "/4k.jpg", [item("night3", "paused", "FourK.S01E01", { seasonNumber: 1, episodeNumber: 1 })], [item("run3", "downloading", "FourK.S01E01.2160p", { seasonNumber: 1, episodeNumber: 1 })]),
};
const fresh = (waiting) => ({ waiting, low_tariff: false, updated: new Date().toISOString() });
const WAITING = fresh([BOYS_ID, "night1", "night3"]);

// Answers GETs from `routes` (night-waiting.json from `waiting`), fires onload and load listeners like a browser.
function fakeXHR(routes, waiting, calls = []) {
  return function (w) {
    w.XMLHttpRequest = class {
      constructor() { this.listeners = []; }
      addEventListener(type, fn) { if (type === "load") this.listeners.push(fn); }
      open(method, url) { this.method = method; this.url = url; }
      send() {
        calls.push(this.url);
        const r = this.url === "/_indexer-bar/night-waiting.json" ? waiting.value : routes[this.url];
        this.status = r ? 200 : 404;
        this.responseText = JSON.stringify(r ?? {});
        setTimeout(() => { this.onload && this.onload(); this.listeners.forEach((f) => f.call(this)); }, 0);
      }
    };
  };
}
const settle = async () => { for (let i = 0; i < 6; i++) { await new Promise((r) => setTimeout(r, 200)); await tick(); } };

// Seerr's StatusBadge while a download is in progress (src/components/StatusBadge), with the manager link.
const badge = (key, text = "Processing", extra = '<span class="ml-1">S2</span>') => `
<a href="/${key}?manage=1" class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full whitespace-nowrap transition cursor-pointer !no-underline bg-indigo-500/80 border border-indigo-500 !text-indigo-100 hover:bg-indigo-500 relative !bg-gray-700/80 !px-0 hover:!bg-gray-700 overflow-hidden">
  <div class="absolute left-0 top-0 z-10 flex h-full bg-indigo-500/80 transition-all duration-200 ease-in-out" style="width: 0%;"></div>
  <div class="relative z-20 flex items-center px-2"><span>${text}</span>${extra}<svg class="ml-1 h-3 w-3" viewBox="-2 -2 42 42"><g><circle cx="18" cy="18" r="18"></circle><path d="m36 18"><animateTransform attributeName="transform"></animateTransform></path></g></svg></div>
</a>`;
const header = (inner) => `<div class="media-header"><div class="media-title"><div class="media-status">${inner}</div><h1>Title</h1></div></div>`;
const requestCard = (key, name) => `
<div class="relative flex w-72 overflow-hidden rounded-xl" data-testid="request-card"><div class="relative z-10 flex min-w-0 flex-1 flex-col pr-4" data-testid="request-card-title">
  <a href="/${key}" class="overflow-hidden text-base font-bold text-white">${name}</a>
  <div class="mt-2 flex items-center text-sm sm:mt-1"><span class="mr-2 hidden font-bold sm:block">Status</span>${badge(key)}</div>
</div></div>`;
const requestItem = (key, name) => `
<div class="relative flex w-full flex-col justify-between overflow-hidden rounded-xl bg-gray-800"><div class="relative flex w-full flex-col justify-between overflow-hidden sm:flex-row">
  <div class="relative z-10 flex w-full items-center overflow-hidden pl-4"><a href="/${key}" class="relative h-auto w-12"><img alt="" src="/poster.jpg"></a><div><a href="/${key}" class="mr-2 min-w-0 truncate text-lg font-bold">${name}</a></div></div>
  <div class="z-10 mt-4 flex w-full flex-col"><div class="card-field"><span class="card-field-name">Status</span>${badge(key)}</div></div>
</div></div>`;
const titleCard = (poster) => `
<div class="w-36"><div class="relative transform-gpu cursor-default overflow-hidden rounded-xl bg-gray-800" style="padding-bottom: 150%;" role="link" tabindex="0">
  <div class="absolute inset-0 h-full w-full overflow-hidden"><img alt="" src="/imageproxy/tmdb/t/p/w300_and_h450_face${poster}" style="width: 100%;">
  <div class="absolute left-0 right-0 flex items-center justify-between p-2"><div class="pointer-events-none z-40 self-start rounded-full border"><div>Series</div></div>
    <div class="flex flex-col items-center gap-1"><div class="pointer-events-none z-40 flex"><div class="relative inline-flex whitespace-nowrap rounded-full border-gray-700 text-xs font-semibold leading-5 ring-gray-700 "><div class="rounded-full shadow-md w-4 sm:w-5 border p-0 bg-indigo-500/80 border-indigo-400 ring-indigo-400 text-indigo-100"><svg viewBox="-2 -2 42 42"><g><path d="m36 18"><animateTransform attributeName="transform"></animateTransform></path></g></svg></div></div></div></div>
  </div></div>
</div></div>`;
// DownloadBlock (Manage panel, badge tooltip)
const downloadBlock = (name, status, pct = 0) => `
<div class="p-4"><div class="mb-2 w-56 truncate text-sm sm:w-80 md:w-full">${name}</div>
  <div class="relative mb-2 h-6 min-w-0 overflow-hidden rounded-full bg-gray-700"><div class="h-8 bg-indigo-600 transition-all duration-200 ease-in-out" style="width: ${pct}%;"></div><div class="absolute inset-0 flex h-6 w-full items-center justify-center text-xs"><span>${pct}%</span></div></div>
  <div class="flex items-center justify-between text-xs"><span><span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full whitespace-nowrap cursor-default bg-indigo-500/80 border border-indigo-500 !text-indigo-100 capitalize">${status}</span></span><span></span></div>
</div>`;

const shown = (w, el) => w.getComputedStyle(el).display !== "none";
const visibleText = (w, root) => [...root.querySelectorAll("*")].filter((e) => !e.children.length && shown(w, e) && [...ancestors(e)].every((a) => shown(w, a))).map((e) => e.textContent.trim()).filter(Boolean).join("|");
function* ancestors(e) { for (let a = e.parentElement; a; a = a.parentElement) yield a; }
const visibleTip = (w) => { const t = w.document.getElementById("hg-flag-tip"); return t && shown(w, t) ? t.textContent : null; };
const open = [];
afterEach(() => { while (open.length) open.pop().close(); });
const night = (body, xhr, path) => { const w = load("night-label.js", body, xhr, "https://requests.example.com" + path); open.push(w); return w; };
const page = (body, path, waiting = WAITING, calls) => night(body, fakeXHR(ROUTES, { value: waiting }, calls), path);

test("title page: a night-paused download shows the night label with the hint, the progress overlay is hidden", async () => {
  const w = page(header(badge("tv/76479")), "/tv/76479");
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(visibleText(w, b), "🌙 Tonight");
  assert.match(b.getAttribute("data-hg-hint"), /22:05 or on Sunday; untick 'Can wait' next time/);
  assert.equal(shown(w, b.querySelector(".z-10")), false);
});

test("released by Download now: the label goes at once and stays gone while the night list still names it", async () => {
  const w = page(header(badge("tv/76479")), "/tv/76479");
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(visibleText(w, b), "🌙 Tonight");
  w.hgNightLabel.release([BOYS_ID]);
  await settle();
  assert.equal(visibleText(w, b), "Processing|S2");
  w.hgNightLabel.refresh();
  await settle();
  assert.equal(visibleText(w, b), "Processing|S2");
});

test("German UI gets the German label", async () => {
  const w = page(header(badge("tv/76479", "Verarbeiten")), "/tv/76479");
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(visibleText(w, b), "🌙 Heute Nacht");
  assert.match(b.getAttribute("data-hg-hint"), /22:05 oder am Sonntag/);
});

test("a running download and a paused item outside the night list stay as Seerr shows them", async () => {
  for (const [key, path] of [["movie/550", "/movie/550"], ["movie/551", "/movie/551"]]) {
    const w = page(header(badge(key, "Processing", "")), path);
    await settle();
    const b = w.document.querySelector(".media-status a");
    assert.equal(visibleText(w, b), "Processing", key);
    assert.equal(b.hasAttribute("title"), false);
    }
});

test("night list stale or missing: nothing is relabelled", async () => {
  const stale = { waiting: [BOYS_ID], updated: new Date(Date.now() - 20 * 60 * 1000).toISOString() };
  for (const waiting of [stale, null]) {
    const w = page(header(badge("tv/76479")), "/tv/76479", waiting);
    await settle();
    assert.equal(visibleText(w, w.document.querySelector(".media-status a")), "Processing|S2");
    }
});

test("nothing waiting: no title is fetched", async () => {
  const calls = [];
  const w = page(header(badge("tv/76479")), "/tv/76479", fresh([]), calls);
  await settle();
  assert.deepEqual(calls.filter((u) => u.startsWith("/api/")), []);
});

test("request cards and the Requests list: each card by its own title", async () => {
  const w = page(requestCard("tv/76479", "The Boys") + requestCard("movie/550", "Fight Club") + requestItem("tv/76479", "The Boys") + requestItem("movie/550", "Fight Club"), "/requests");
  await settle();
  const badges = [...w.document.querySelectorAll('a[href$="?manage=1"]')].map((b) => visibleText(w, b));
  assert.deepEqual(badges, ["🌙 Tonight", "Processing|S2", "🌙 Tonight", "Processing|S2"]);
});

test("a series with one night item and one running download, and a running 4K download, stay normal", async () => {
  const w = page(requestCard("tv/2", "Mixed Show") + header(badge("tv/3") + badge("tv/3", "4K Processing")), "/tv/3");
  await settle();
  const [mixed, fourk, fourk4k] = [...w.document.querySelectorAll('a[href$="?manage=1"]')].map((b) => visibleText(w, b));
  assert.equal(mixed, "Processing|S2");
  assert.equal(fourk, "🌙 Tonight");
  assert.equal(fourk4k, "4K Processing|S2");
});

test("Discover title cards: the spinner of a night-paused title becomes a moon (data from Seerr's own list response)", async () => {
  const routes = { "/api/v1/discover/tv": { results: [{ id: 76479, mediaType: "tv", name: "The Boys", posterPath: "/boys.jpg", mediaInfo: ROUTES["/api/v1/tv/76479"].mediaInfo }, { id: 550, mediaType: "movie", title: "Fight Club", posterPath: "/fight.jpg", mediaInfo: ROUTES["/api/v1/movie/550"].mediaInfo }] } };
  const w = night(titleCard("/boys.jpg") + titleCard("/fight.jpg"), fakeXHR(routes, { value: WAITING }), "/discover/tv");
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/discover/tv");
  x.send();
  await settle();
  const [boys, fight] = w.document.querySelectorAll('[role="link"]');
  assert.equal(visibleText(w, boys.querySelector(".pointer-events-none.z-40.flex")), "🌙");
  assert.ok(fight.querySelector("[data-hg-spin]"));
  assert.equal(fight.querySelectorAll("[data-hg-night-own]").length, 1);
  const css = w.document.getElementById("hg-night-style").textContent;
  assert.ok(boys.querySelector("[data-hg-night-moon]"));
  assert.ok(fight.querySelector("[data-hg-night-moon]"));
  assert.match(css, /\[data-hg-night-moon\]\{[^}]*width:1\.625rem[^}]*!important/);
  assert.match(css, /\[data-hg-night-moon\]\{[^}]*height:1\.625rem[^}]*!important/);
  assert.match(css, /\[data-hg-night-moon\] > \[data-hg-night-own\]\{[^}]*font-size:\.95rem/);
});

test("Manage panel and badge tooltip: the download block shows the label, the bar is greyed, running ones stay", async () => {
  const body = header(badge("tv/76479")) + `<div class="slideover">${downloadBlock(BOYS_ITEM.title, "paused")}${downloadBlock(FIGHT_ITEM.title, "downloading", 60)}</div><div role="tooltip">${downloadBlock("The Boys: Season 2 Episode 1", "paused")}</div>`;
  const w = page(body, "/tv/76479?manage=1");
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/movie/550");
  x.send();
  await settle();
  const [boys, fight, tip] = w.document.querySelectorAll(".p-4");
  for (const b of [boys, tip]) {
    assert.equal(visibleText(w, b.querySelector(".capitalize").parentElement), "Waiting for the night 🌙");
    assert.equal(shown(w, b.querySelector(".h-8 + div span")), false);
    assert.ok(b.querySelector(".h-8").hasAttribute("data-hg-night-grey"));
  }
  assert.equal(visibleText(w, fight), `${FIGHT_ITEM.title}|60%|downloading`);
});

test("the night starts while the page is open: the label goes away again", async () => {
  const waiting = { value: WAITING };
  const w = night(header(badge("tv/76479")), fakeXHR(ROUTES, waiting), "/tv/76479");
  await settle();
  assert.equal(visibleText(w, w.document.querySelector(".media-status a")), "🌙 Tonight");
  waiting.value = { ...fresh([]), low_tariff: true };
  w.hgNightLabel.refresh();
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(visibleText(w, b), "Processing|S2");
  assert.ok(shown(w, b.querySelector(".z-10")));
  assert.equal(b.hasAttribute("title"), false);
});

test("a badge rendered later is relabelled (MutationObserver), and only once", async () => {
  const w = page("<main></main>", "/tv/76479");
  await settle();
  w.document.querySelector("main").insertAdjacentHTML("beforeend", header(badge("tv/76479")));
  await settle();
  w.document.body.insertAdjacentHTML("beforeend", "<p>re-render</p>");
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(b.querySelectorAll("[data-hg-night-own]").length, 1);
  assert.equal(visibleText(w, b), "🌙 Tonight");
});

test("unknown markup: nothing breaks, nothing is added", async () => {
  const w = page('<div class="media-status"><span>Processing</span></div><div class="p-4">x</div>', "/tv/76479");
  await settle();
  assert.equal(w.document.querySelector("[data-hg-night-own]"), null);
});

test("Requests page: the request list carries no title names, so the tooltip still finds 'The Boys: Season 2 Episode 1'", async () => {
  const routes = { ...ROUTES, "/api/v1/request?take=10": { results: [{ id: 38, type: "tv", media: ROUTES["/api/v1/tv/76479"].mediaInfo }] } };
  const w = night(requestCard("tv/76479", "The Boys") + `<div role="tooltip">${downloadBlock("The Boys: Season 2 Episode 1", "paused")}</div>`, fakeXHR(routes, { value: WAITING }), "/requests");
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/request?take=10");
  x.send();
  await settle();
  assert.equal(visibleText(w, w.document.querySelector('[role="tooltip"] .capitalize').parentElement), "Waiting for the night 🌙");
});

// request-state.json (night-downloads.py): open requests that are "Can wait" and those not released yet
const RS_URL = "/_indexer-bar/request-state.json";
const reqState = (canWait, unreleased = {}, age = 0) => ({ canWait, unreleased, updated: new Date(Date.now() - age).toISOString() });
const RS_ROUTES = {
  ...ROUTES,
  "/api/v1/movie/700": details("movie", 700, "Tied Up", "/tied.jpg", []),
  "/api/v1/movie/701": details("movie", 701, "The Cat in the Hat", "/cat.jpg", []),
  "/api/v1/movie/702": details("movie", 702, "Unreleased Now", "/now.jpg", []),
  "/api/v1/movie/704": details("movie", 704, "More Than Honey", "/honey.jpg", []),
  "/api/v1/movie/703": { ...details("movie", 703, "Done", "/done.jpg", []), mediaInfo: { tmdbId: 703, mediaType: "movie", status: 5, downloadStatus: [], downloadStatus4k: [] } },
};
const RS = reqState(["movie/700", "movie/701", "movie/703", "movie/550", "tv/76479"], { "movie/701": "2027-02-02", "movie/702": null });
const rsPage = (body, path, rs = RS, waiting = fresh([]), calls) => night(body, fakeXHR({ ...RS_ROUTES, [RS_URL]: rs }, { value: waiting }, calls), path);
// StatusBadge without a download: "Requested" (Processing status), Pending as plain text
const plainBadge = (key, text = "Requested") => `<a href="/${key}?manage=1" class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full whitespace-nowrap transition cursor-pointer !no-underline bg-indigo-500/80 border border-indigo-500 !text-indigo-100 hover:bg-indigo-500 false overflow-hidden"><div class="relative z-20 flex items-center false"><span>${text}</span></div></a>`;
const pendingBadge = (key, text = "Pending") => `<a href="/${key}?manage=1" class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full whitespace-nowrap transition cursor-pointer !no-underline bg-yellow-500/80 border-yellow-500 border !text-yellow-100 hover:bg-yellow-500">${text}</a>`;
const flagTip = (b) => b.getAttribute("data-hg-hint");
const clockCard = (poster, colour = "indigo") => titleCard(poster).replace(/<svg[\s\S]*?<\/svg>/, '<svg viewBox="0 0 24 24" class="clock"><path d="M12 2"></path></svg>').replace(/indigo/g, colour);

test("Can wait title with nothing grabbed: the Requested badge gets a 🌙 with the night hint", async () => {
  const w = rsPage(header(plainBadge("movie/700")), "/movie/700");
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(visibleText(w, b), "Requested|🌙");
  assert.equal(flagTip(b), "Waiting for the night — downloads at night (cheaper power)");
});

test("Can wait and not released yet: 🌙 and 📅 with the date; not released alone: only 📅", async () => {
  const w = rsPage(header(plainBadge("movie/701")) + requestCard("movie/702", "Unreleased Now").replace(badge("movie/702"), plainBadge("movie/702")), "/movie/701");
  await settle();
  const [cat, now] = w.document.querySelectorAll('a[href$="?manage=1"]');
  assert.equal(visibleText(w, cat), "Requested|🌙|📅");
  assert.equal(flagTip(cat), "Waiting for the night · Not released yet — expected 2 Feb 2027");
  assert.equal(visibleText(w, now), "Requested|📅");
  assert.equal(flagTip(now), "Not released yet");
});

test("Pending approval badge gets the 🌙 too", async () => {
  const w = rsPage(header(pendingBadge("movie/700")), "/movie/700");
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(b.textContent, "Pending🌙");
  assert.equal(flagTip(b), "Waiting for the night — downloads at night (cheaper power)");
});

test("no 🌙 while Seerr shows a running download, once available, for titles not Can wait, or with a stale or missing file", async () => {
  const w = rsPage(header(badge("movie/550", "Processing", "")) + requestCard("movie/703", "Done").replace(badge("movie/703"), plainBadge("movie/703", "Available")) + requestCard("movie/1", "Other").replace(badge("movie/1"), plainBadge("movie/1")), "/movie/550");
  await settle();
  assert.deepEqual([...w.document.querySelectorAll('a[href$="?manage=1"]')].map((b) => visibleText(w, b)), ["Processing", "Available", "Requested"]);
  for (const rs of [reqState(["movie/700"], {}, 20 * 60 * 1000), null]) {
    const w2 = rsPage(header(plainBadge("movie/700")), "/movie/700", rs);
    await settle();
    assert.equal(visibleText(w2, w2.document.querySelector(".media-status a")), "Requested");
  }
});

test("night-paused download of a Can wait title: the night label, no second moon", async () => {
  const w = rsPage(header(badge("tv/76479")), "/tv/76479", RS, WAITING);
  await settle();
  assert.equal(visibleText(w, w.document.querySelector(".media-status a")), "🌙 Tonight");
});

test("German UI: German hints and date", async () => {
  const w = rsPage(header(plainBadge("movie/701", "Angefragt")), "/movie/701");
  await settle();
  assert.equal(flagTip(w.document.querySelector(".media-status a")), "Wartet auf die Nacht · Noch nicht erschienen — erwartet 2. Feb. 2027");
});

test("the request is downloaded or switched to Download now while the page is open: the 🌙 goes", async () => {
  const rs = { value: RS };
  const routes = { ...RS_ROUTES };
  Object.defineProperty(routes, RS_URL, { get: () => rs.value, enumerable: true });
  const w = night(header(plainBadge("movie/700")), fakeXHR(routes, { value: fresh([]) }), "/movie/700");
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(visibleText(w, b), "Requested|🌙");
  rs.value = reqState([]);
  w.hgNightLabel.refresh();
  await settle();
  assert.equal(visibleText(w, b), "Requested");
  assert.equal(b.querySelectorAll("[data-hg-flag-own]").length, 0);
  assert.equal(b.hasAttribute("title"), false);
  assert.equal(b.getAttribute("data-hg-hint"), "Requested — approved, waiting for the search", "no icon of ours left, the plain state hint stays");
});

test("Discover cards: 🌙 in the 26px circle, 🌙📅 side by side in a pill, 📅 alone, nothing for others; re-renders add nothing twice", async () => {
  const list = (id, poster, status = 3, items = []) => ({ id, mediaType: "movie", title: String(id), posterPath: poster, mediaInfo: { tmdbId: id, mediaType: "movie", status, downloadStatus: items, downloadStatus4k: [] } });
  const routes = { ...RS_ROUTES, [RS_URL]: RS, "/api/v1/discover/movies": { results: [list(700, "/tied.jpg"), list(701, "/cat.jpg"), list(702, "/now.jpg"), list(550, "/fight.jpg", 3, [FIGHT_ITEM]), list(9, "/other.jpg")] } };
  const w = night(clockCard("/tied.jpg") + clockCard("/cat.jpg") + clockCard("/now.jpg") + titleCard("/fight.jpg") + clockCard("/other.jpg"), fakeXHR(routes, { value: fresh([]) }), "/discover/movies");
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/discover/movies");
  x.send();
  await settle();
  w.document.body.insertAdjacentHTML("beforeend", "<p>re-render</p>");
  await settle();
  const [tied, cat, now, fight, other] = [...w.document.querySelectorAll('[role="link"]')].map((c) => c.querySelector(".pointer-events-none.z-40.flex"));
  assert.equal(visibleText(w, tied), "🌙");
  assert.ok(tied.querySelector("[data-hg-night-moon]"));
  assert.equal(visibleText(w, cat), "🌙📅");
  assert.ok(cat.querySelector("[data-hg-night-pill]"));
  assert.equal(cat.querySelector("[data-hg-night-moon]"), null);
  assert.match(cat.querySelector("[data-hg-night]").getAttribute("data-hg-hint"), /^Waiting for the night · Not released yet — expected 2 Feb 2027$/);
  assert.equal(visibleText(w, now), "📅");
  assert.equal(fight.querySelectorAll("[data-hg-night-own]").length, 1);
  assert.ok(fight.querySelector("[data-hg-spin]"));
  assert.equal(other.querySelector("[data-hg-night-own]"), null);
  assert.equal(tied.querySelectorAll("[data-hg-night-own]").length, 1);
  assert.match(w.document.getElementById("hg-night-style").textContent, /\[data-hg-night-pill\]\{[^}]*height:1\.625rem[^}]*!important/);
});

const COMPASS_EN = "No release found yet — searched again in tonight's scheduled search (04:17)";
const RS_C = { ...reqState(["movie/704", "movie/700"], { "movie/700": "2027-02-02" }), noRelease: { "movie/704": "2026-10-28T10:00:23Z", "movie/702": "2026-10-28T10:00:23Z" } };

test("searched, no release found: 🧭 beside the 🌙 (and alone for a title that is not Can wait), order 🌙🧭📅", async () => {
  const w = rsPage(header(plainBadge("movie/704")) + requestCard("movie/702", "Now").replace(badge("movie/702"), plainBadge("movie/702")) + requestCard("movie/700", "Tied").replace(badge("movie/700"), plainBadge("movie/700")), "/movie/704", RS_C);
  await settle();
  const [honey, now, tied] = w.document.querySelectorAll('a[href$="?manage=1"]');
  assert.equal(visibleText(w, honey), "Requested|🌙|🧭");
  assert.equal(flagTip(honey), "Waiting for the night · " + COMPASS_EN + ", downloads at night when found");
  assert.equal(flagTip(now), COMPASS_EN);
  assert.equal(visibleText(w, now), "Requested|🧭");
  assert.equal(visibleText(w, tied), "Requested|🌙|📅");
});

test("German 🧭 hint", async () => {
  const w = rsPage(header(plainBadge("movie/702", "Angefragt")), "/movie/702", RS_C);
  await settle();
  assert.equal(flagTip(w.document.querySelector(".media-status a")), "Noch kein Release gefunden — die geplante Suche heute Nacht (04:17) sucht weiter");
});

test("one merged hint on the whole badge and card pill; a tap shows it, a second tap goes through", async () => {
  const routes = { ...RS_ROUTES, [RS_URL]: RS_C, "/api/v1/discover/movies": { results: [{ id: 704, mediaType: "movie", title: "Honey", posterPath: "/honey.jpg", mediaInfo: { tmdbId: 704, mediaType: "movie", status: 3, downloadStatus: [], downloadStatus4k: [] } }] } };
  const w = night(clockCard("/honey.jpg") + header(plainBadge("movie/704")), fakeXHR(routes, { value: fresh([]) }), "/movie/704");
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/discover/movies");
  x.send();
  await settle();
  const MERGED = "Waiting for the night · " + COMPASS_EN + ", downloads at night when found";
  let card = 0, link = 0;
  w.document.querySelector('[role="link"]').addEventListener("click", () => card++);
  const badgeEl = w.document.querySelector(".media-status a");
  badgeEl.addEventListener("click", (e) => { if (!e.defaultPrevented) link++; });
  const chip = w.document.querySelector('[role="link"] [data-hg-night]');
  assert.equal(chip.textContent, "🌙🧭");
  assert.equal(chip.getAttribute("data-hg-hint"), MERGED);
  assert.equal(chip.querySelector("[data-hg-night-own]").getAttribute("aria-label"), MERGED);
  assert.equal(chip.hasAttribute("role"), false);
  assert.equal(badgeEl.getAttribute("data-hg-hint"), MERGED);
  assert.equal(badgeEl.querySelector("[data-hg-flag-own]").hasAttribute("title"), false);
  const tap = (el) => { const d = new w.MouseEvent("pointerdown", { bubbles: true }); Object.defineProperty(d, "pointerType", { value: "touch" }); el.dispatchEvent(d); el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true })); };
  tap(chip);
  const tip = w.document.getElementById("hg-flag-tip");
  assert.ok(tip && shown(w, tip));
  assert.equal(tip.textContent, MERGED);
  assert.equal(card, 0);
  tap(chip);
  assert.equal(card, 1);
  assert.equal(shown(w, tip), false);
  tap(badgeEl.querySelector("span"));
  assert.equal(link, 0);
  assert.ok(shown(w, tip));
  assert.equal(tip.textContent, MERGED);
  tap(badgeEl);
  assert.equal(link, 1);
  w.document.body.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert.equal(shown(w, tip), false);
  assert.match(w.document.getElementById("hg-night-style").textContent, /\[data-hg-night\]\[data-hg-night-icons\]\{[^}]*pointer-events:auto/);
  assert.equal(w.hgNightLabel.hintText([["🌙", "x"], ["📅", "Not released yet — expected 2 Feb 2027"]]), "Waiting for the night · Not released yet — expected 2 Feb 2027");
});

test("icons(key) for other scripts: the title's icons with their hints", async () => {
  const w = rsPage(header(plainBadge("movie/704")), "/movie/704", RS_C);
  await settle();
  assert.equal(JSON.stringify(w.hgNightLabel.icons("movie/704")), JSON.stringify([["🌙", "Waiting for the night — downloads at night (cheaper power)"], ["🧭", COMPASS_EN]]));
  assert.equal(JSON.stringify(w.hgNightLabel.icons("movie/9")), "[]");
});

const nativeCard = (poster, kind) => titleCard(poster)
  .replace(/<svg[\s\S]*?<\/svg>/, kind === "partial" ? '<svg viewBox="0 0 24 24"><path d="M5.25 12a.75.75 0 0 1 .75-.75h12a.75.75 0 0 1 0 1.5H6a.75.75 0 0 1-.75-.75Z"></path></svg>' : kind === "available" ? '<svg viewBox="0 0 24 24"><path d="M19.916 4.626a.75.75 0 0 1 .208 1.04"></path></svg>' : "$&")
  .replace(/bg-indigo-500\/80 border-indigo-400 ring-indigo-400 text-indigo-100/, kind === "downloading" ? "$&" : "bg-green-500/80 border-green-400 ring-green-400 text-green-100");

test("Discover cards: native downloading / available / partly available badges become our purple circle (spinner ring, ✅, half-filled circle) with hints", async () => {
  const dl = { ...FIGHT_ITEM, size: 1000, sizeLeft: 600 };
  const mk = (id, poster, status, items = []) => ({ id, mediaType: "movie", title: String(id), posterPath: poster, mediaInfo: { tmdbId: id, mediaType: "movie", status, downloadStatus: items, downloadStatus4k: [] } });
  const routes = { ...RS_ROUTES, [RS_URL]: RS, "/api/v1/discover/movies": { results: [mk(550, "/fight.jpg", 3, [dl]), mk(801, "/avail.jpg", 5), mk(802, "/part.jpg", 4), mk(700, "/tied.jpg", 4)] } };
  const w = night(nativeCard("/fight.jpg", "downloading") + nativeCard("/avail.jpg", "available") + nativeCard("/part.jpg", "partial") + nativeCard("/tied.jpg", "partial"), fakeXHR(routes, { value: fresh([]) }), "/discover/movies");
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/discover/movies");
  x.send();
  await settle();
  w.document.body.insertAdjacentHTML("beforeend", "<p>re-render</p>");
  await settle();
  const [fight, avail, part, tied] = [...w.document.querySelectorAll('[role="link"]')].map((c) => c.querySelector(".pointer-events-none.z-40.flex"));
  const hint = (b) => b.querySelector("[data-hg-night]").getAttribute("data-hg-hint");
  assert.ok(fight.querySelector("[data-hg-night-moon]"));
  assert.ok(fight.querySelector("[data-hg-spin] svg"));
  assert.equal(visibleText(w, fight), "");
  assert.equal(hint(fight), "Downloading now — 40 %");
  assert.equal(fight.querySelectorAll("[data-hg-night-own]").length, 1);
  assert.equal(visibleText(w, avail), "✅");
  assert.equal(hint(avail), "Available in Jellyfin");
  assert.ok(avail.querySelector("[data-hg-night-moon][data-hg-night-solid]"));
  assert.equal(visibleText(w, part), "");
  assert.ok(part.querySelector("[data-hg-half]"));
  assert.ok(part.querySelector("[data-hg-night-moon][data-hg-night-solid]"));
  assert.equal(hint(part), "Partly available");
  assert.equal(visibleText(w, tied), "🌙");
  assert.ok(tied.querySelector("[data-hg-half]"));
  assert.equal(tied.querySelector("[data-hg-night-own]").firstElementChild.hasAttribute("data-hg-half"), true);
  assert.ok(tied.querySelector("[data-hg-night-pill]"));
  assert.equal(hint(tied), "Partly available · Waiting for the night — the rest downloads at night");
  const css = w.document.getElementById("hg-night-style").textContent;
  assert.match(css, /@keyframes hg-spin/);
  assert.match(css, /\[data-hg-half\]\{[^}]*border-radius:50%[^}]*linear-gradient\(90deg,#fff 50%,transparent 50%\)/);
  assert.match(css, /prefers-reduced-motion[^{]*\{[^}]*\[data-hg-spin\][^}]*animation:\s*none/);
  assert.match(css, /\[data-hg-night-solid\]\{[^}]*background/);
});

test("native available badge: German hint", async () => {
  const w = night(nativeCard("/avail.jpg", "available"), fakeXHR(RS_ROUTES, { value: fresh([]) }), "/discover/movies");
  w.document.documentElement.lang = "de";
  const tv = w.document.createElement("a");
  tv.setAttribute("href", "/discover/tv");
  tv.textContent = "Serien";
  w.document.body.appendChild(tv);
  w.hgNightLabel.apply(w.document, w.location);
  const b = w.document.querySelector(".pointer-events-none.z-40.flex");
  assert.equal(b.querySelector("[data-hg-night]").getAttribute("data-hg-hint"), "In Jellyfin verfügbar");
  assert.ok(b.querySelector("svg"));
});

// every badge the owner approved (Claude-Mockups seerr-all-badges): singles and combinations, EN and DE hints
const BADGE_ROWS = [
  { id: 901, name: "spinner", card: "downloading", status: 3, dl: true, icons: "◌", en: "Downloading now — 40 %", de: "Lädt gerade herunter — 40 %" },
  { id: 902, name: "available", card: "available", status: 5, icons: "✅", en: "Available in Jellyfin", de: "In Jellyfin verfügbar" },
  { id: 903, name: "partly", card: "partial", status: 4, icons: "◐", en: "Partly available", de: "Teilweise verfügbar" },
  { id: 904, name: "can wait", card: "clock", status: 3, moon: true, icons: "🌙", en: "Waiting for the night — downloads at night (cheaper power)", de: "Wartet auf die Nacht — lädt nachts herunter (günstigerer Strom)" },
  { id: 905, name: "nothing found", card: "clock", status: 3, compass: true, icons: "🧭", en: "No release found yet — searched again in tonight's scheduled search (04:17)", de: "Noch kein Release gefunden — die geplante Suche heute Nacht (04:17) sucht weiter" },
  { id: 906, name: "not released", card: "clock", status: 3, cal: "2026-07-17", icons: "📅", en: "Not released yet — expected 17 Jul 2026", de: "Noch nicht erschienen — erwartet DATE_DE" },
  { id: 907, name: "can wait + nothing found", card: "clock", status: 3, moon: true, compass: true, icons: "🌙🧭", en: "Waiting for the night · No release found yet — searched again in tonight's scheduled search (04:17), downloads at night when found", de: "Wartet auf die Nacht · Noch kein Release gefunden — die geplante Suche heute Nacht (04:17) sucht weiter, lädt nachts, sobald gefunden" },
  { id: 908, name: "can wait + not released", card: "clock", status: 3, moon: true, cal: "2026-07-17", icons: "🌙📅", en: "Waiting for the night · Not released yet — expected 17 Jul 2026", de: "Wartet auf die Nacht · Noch nicht erschienen — erwartet DATE_DE" },
  { id: 909, name: "partly + can wait", card: "partial", status: 4, moon: true, icons: "◐🌙", en: "Partly available · Waiting for the night — the rest downloads at night", de: "Teilweise verfügbar · Wartet auf die Nacht — der Rest lädt nachts herunter" },
  { id: 910, name: "partly + can wait + nothing found", card: "partial", status: 4, moon: true, compass: true, icons: "◐🌙🧭", en: "Partly available · Waiting for the night · No release found yet — searched again tonight (04:17)", de: "Teilweise verfügbar · Wartet auf die Nacht · Noch kein Release gefunden — die Suche heute Nacht (04:17) sucht weiter" },
  { id: 911, name: "partly + not aired", card: "partial", status: 4, cal: "2026-11-11", icons: "◐📅", en: "Partly available · Not released yet — next episode 11 Nov", de: "Teilweise verfügbar · Noch nicht erschienen — nächste Folge DATE_DE" },
  { id: 912, name: "partly + can wait + not aired", card: "partial", status: 4, moon: true, cal: "2026-11-11", icons: "◐🌙📅", en: "Partly available · Waiting for the night · Not released yet — next episode 11 Nov", de: "Teilweise verfügbar · Wartet auf die Nacht · Noch nicht erschienen — nächste Folge DATE_DE" },
  { id: 913, name: "partly + downloading", card: "partial", status: 4, dl: true, icons: "◐◌", en: "Partly available · Downloading now — 40 %", de: "Teilweise verfügbar · Lädt gerade herunter — 40 %" },
];
const badgeCard = (r) => (r.card === "clock" ? clockCard(`/b${r.id}.jpg`) : nativeCard(`/b${r.id}.jpg`, r.card === "partial" ? "partial" : r.card));
const badgeIcons = (b) => [...b.querySelector("[data-hg-night-own]").children].map((c) => c.hasAttribute("data-hg-half") ? "◐" : c.hasAttribute("data-hg-spin") ? "◌" : c.hasAttribute("data-hg-search") ? (c.getAttribute("data-hg-search") === "live" ? "🔍" : "🔎") : c.textContent).join("") || b.querySelector("[data-hg-night-own]").textContent;

for (const lang of ["en", "de"]) {
  test(`all badges (${lang}): every approved single and combination renders its icons and hint`, async () => {
    const dl = { ...FIGHT_ITEM, size: 1000, sizeLeft: 600 };
    const mk = (r) => ({ id: r.id, mediaType: "tv", name: String(r.id), posterPath: `/b${r.id}.jpg`, mediaInfo: { tmdbId: r.id, mediaType: "tv", status: r.status, downloadStatus: r.dl ? [dl] : [], downloadStatus4k: [] } });
    const rs = {
      canWait: BADGE_ROWS.filter((r) => r.moon).map((r) => `tv/${r.id}`),
      unreleased: Object.fromEntries(BADGE_ROWS.filter((r) => r.cal).map((r) => [`tv/${r.id}`, r.cal])),
      noRelease: Object.fromEntries(BADGE_ROWS.filter((r) => r.compass).map((r) => [`tv/${r.id}`, "2026-10-28T10:00:23Z"])),
      updated: new Date().toISOString(),
    };
    const routes = { ...RS_ROUTES, [RS_URL]: rs, "/api/v1/discover/tv": { results: BADGE_ROWS.map(mk) } };
    const w = night(BADGE_ROWS.map(badgeCard).join(""), fakeXHR(routes, { value: fresh([]) }), "/discover/tv");
    w.document.body.insertAdjacentHTML("beforeend", `<a href="/discover/tv">${lang === "de" ? "Serien" : "Series"}</a>`);
    const x = new w.XMLHttpRequest();
    x.open("GET", "/api/v1/discover/tv");
    x.send();
    await settle();
    w.document.body.insertAdjacentHTML("beforeend", "<p>re-render</p>");
    await settle();
    const cards = [...w.document.querySelectorAll('[role="link"]')].map((c) => c.querySelector(".pointer-events-none.z-40.flex"));
    BADGE_ROWS.forEach((r, i) => {
      const own = cards[i].querySelector("[data-hg-night]");
      const date = lang === "de" ? new Date(Date.UTC(...(r.cal || "2000-01-01").split("-").map((v, k) => k === 1 ? +v - 1 : +v))).toLocaleDateString("de-CH", { day: "numeric", month: "short", ...(r.cal && r.cal.startsWith("2026-07") ? { year: "numeric" } : {}), timeZone: "UTC" }) : "";
      assert.equal(badgeIcons(cards[i]), r.icons, r.name);
      assert.equal(own.getAttribute("data-hg-hint"), r[lang].replace("DATE_DE", date), r.name);
      assert.equal(!!cards[i].querySelector("[data-hg-spin]"), r.icons.includes("◌"), r.name);
      assert.equal(!!cards[i].querySelector("[data-hg-half]"), r.icons.includes("◐"), r.name);
    });
  });
}

test("a download paused for the night shows 🌙, not the spinner, also next to ◐", async () => {
  const paused = { ...FIGHT_ITEM, status: "paused", downloadId: "SABnzbd_nzo_night", size: 1000, sizeLeft: 1000 };
  const m = { id: 920, mediaType: "tv", name: "920", posterPath: "/b920.jpg", mediaInfo: { tmdbId: 920, mediaType: "tv", status: 4, downloadStatus: [paused], downloadStatus4k: [] } };
  const routes = { ...RS_ROUTES, [RS_URL]: { ...reqState([]), noRelease: {} }, "/api/v1/discover/tv": { results: [m] } };
  const w = night(nativeCard("/b920.jpg", "partial"), fakeXHR(routes, { value: fresh(["SABnzbd_nzo_night"]) }), "/discover/tv");
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/discover/tv");
  x.send();
  await settle();
  const b = w.document.querySelector(".pointer-events-none.z-40.flex");
  assert.equal(badgeIcons(b), "◐🌙");
  assert.ok(!b.querySelector("[data-hg-spin]"));
});

// live request state: running search 🔍 (pulsing), just requested 🔎, waiting for approval ⏳, problem ⚠️, importing, imported
const hhmm = (iso, locale = "en-GB") => new Date(iso).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
const STARTED = new Date(Date.now() - 3 * 60 * 1000).toISOString();
const live = (extra = {}) => ({ ...reqState([]), noRelease: {}, searching: {}, fresh: {}, pending: [], queue: {}, imported: [], ...extra });
const searchIcon = (b) => b.querySelector("[data-hg-search]");
const cardOf = (w, i = 0) => [...w.document.querySelectorAll('[role="link"]')].map((c) => c.querySelector(".pointer-events-none.z-40.flex"))[i];
const discover = (cards, results, rs, waiting = fresh([])) => {
  const routes = { ...RS_ROUTES, "/api/v1/discover/movies": { results } };
  Object.defineProperty(routes, RS_URL, { get: () => rs.value, enumerable: true });
  const w = night(cards, fakeXHR(routes, { value: waiting }), "/discover/movies");
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/discover/movies");
  x.send();
  return w;
};
const listed = (id, status = 3, items = [], requests) => ({ id, mediaType: "movie", title: String(id), posterPath: `/l${id}.jpg`, mediaInfo: { tmdbId: id, mediaType: "movie", status, downloadStatus: items, downloadStatus4k: [], ...(requests && { requests }) } });

test("search running: the Requested badge gets the pulsing magnifier with its start time; with Can wait 🔍🌙", async () => {
  const w = rsPage(header(plainBadge("movie/700")) + requestCard("movie/702", "Now").replace(badge("movie/702"), plainBadge("movie/702")), "/movie/700", live({ canWait: ["movie/700"], searching: { "movie/700": { status: "started", started: STARTED }, "movie/702": { status: "queued", ahead: 2 } } }));
  await settle();
  const [tied, now] = w.document.querySelectorAll('a[href$="?manage=1"]');
  assert.equal(searchIcon(tied).getAttribute("data-hg-search"), "live");
  assert.ok(searchIcon(tied).querySelector("svg"));
  assert.equal(visibleText(w, tied), "Requested|🌙");
  assert.equal(flagTip(tied), `Searching indexers now (started ${hhmm(STARTED)}) — usually a few minutes · Waiting for the night`);
  assert.equal(flagTip(now), "Waiting for other searches (2 ahead)");
  const css = w.document.getElementById("hg-night-style").textContent;
  assert.match(css, /@keyframes hg-pulse/);
  assert.match(css, /prefers-reduced-motion[^{]*\{[^}]*\[data-hg-search="live"\][^}]*animation:\s*none/);
});

test("German search hints", async () => {
  const w = rsPage(header(plainBadge("movie/700", "Angefragt")) + requestCard("movie/702", "Now").replace(badge("movie/702"), plainBadge("movie/702", "Angefragt")), "/movie/700", live({ searching: { "movie/700": { status: "started", started: STARTED }, "movie/702": { status: "queued", ahead: 1 } } }));
  await settle();
  const [tied, now] = w.document.querySelectorAll('a[href$="?manage=1"]');
  assert.equal(flagTip(tied), `Sucht gerade in den Indexern (seit ${hhmm(STARTED, "de-CH")}) — dauert meist ein paar Minuten`);
  assert.equal(flagTip(now), "Wartet auf andere Suchen (1 davor)");
});

test("just requested: a still magnifier 'search starts shortly', also from Seerr's own request before the file knows it", async () => {
  const created = new Date(Date.now() - 20 * 1000).toISOString();
  const old = { value: { ...live({ fresh: { "movie/700": created, "movie/701": null } }), updated: new Date(Date.now() - 60 * 1000).toISOString() } };
  const routes = { ...RS_ROUTES, "/api/v1/movie/702": { ...details("movie", 702, "Unreleased Now", "/now.jpg", []), mediaInfo: { ...details("movie", 702, "x", "/now.jpg", []).mediaInfo, requests: [{ id: 5, status: 2, createdAt: created }] } } };
  Object.defineProperty(routes, RS_URL, { get: () => old.value, enumerable: true });
  const w = night(header(plainBadge("movie/700")) + requestCard("movie/701", "Cat").replace(badge("movie/701"), plainBadge("movie/701")) + requestCard("movie/702", "Now").replace(badge("movie/702"), plainBadge("movie/702")), fakeXHR(routes, { value: fresh([]) }), "/movie/700");
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/movie/702");
  x.send();
  await settle();
  const [tied, cat, now] = w.document.querySelectorAll('a[href$="?manage=1"]');
  assert.equal(searchIcon(tied).getAttribute("data-hg-search"), "wait");
  assert.equal(flagTip(tied), "Requested — search starts shortly");
  assert.equal(flagTip(cat), "Requested — not searched yet; tonight's scheduled search (04:17) tries");
  assert.equal(flagTip(now), "Requested — search starts shortly");
});

test("waiting for approval ⏳, problem ⚠️ with the Radarr message, Seerr could not send it", async () => {
  const w = rsPage(header(pendingBadge("movie/700")) + requestCard("movie/702", "Now").replace(badge("movie/702"), plainBadge("movie/702")) + requestCard("movie/701", "Cat").replace(badge("movie/701"), plainBadge("movie/701", "Failed")), "/movie/700",
    live({ pending: ["movie/700"], queue: { "movie/702": { state: "problem", message: "No files found are eligible for import in …" }, "movie/701": { state: "problem", message: "", code: "seerr" } } }));
  await settle();
  const [tied, now, cat] = w.document.querySelectorAll('a[href$="?manage=1"]');
  assert.equal(tied.textContent, "Pending⏳");
  assert.equal(flagTip(tied), "Waiting for approval");
  assert.equal(visibleText(w, now), "Requested|⚠️");
  assert.equal(flagTip(now), "Problem — see Requests/Activity: No files found are eligible for import in …");
  assert.equal(flagTip(cat), "Problem — Seerr could not hand the request to Radarr/Sonarr");
});

test("cards: importing keeps the spinner with 'Importing…', a download Seerr does not show yet spins, a file already there is ✅", async () => {
  const rs = { value: live({ queue: { "movie/700": { state: "importing" }, "movie/701": { state: "downloading" } }, imported: ["movie/702"] }) };
  const w = discover(clockCard("/l700.jpg") + clockCard("/l701.jpg") + clockCard("/l702.jpg"), [listed(700), listed(701), listed(702)], rs);
  await settle();
  const [imp, dl, done] = [0, 1, 2].map((i) => cardOf(w, i));
  assert.ok(imp.querySelector("[data-hg-spin]"));
  assert.equal(imp.querySelector("[data-hg-night]").getAttribute("data-hg-hint"), "Importing…");
  assert.ok(dl.querySelector("[data-hg-spin]"));
  assert.equal(dl.querySelector("[data-hg-night]").getAttribute("data-hg-hint"), "Downloading now");
  assert.equal(badgeIcons(done), "✅");
  assert.equal(done.querySelector("[data-hg-night]").getAttribute("data-hg-hint"), "Downloaded — in Jellyfin shortly");
});

test("polls every 15 s only while a search badge shows, re-renders in place, then moves on to the next state", async () => {
  const rs = { value: live({ canWait: ["movie/700"], searching: { "movie/700": { status: "started", started: STARTED } } }) };
  const calls = [];
  const routes = { ...RS_ROUTES, "/api/v1/discover/movies": { results: [listed(700)] } };
  Object.defineProperty(routes, RS_URL, { get: () => rs.value, enumerable: true });
  const w = night(clockCard("/l700.jpg"), fakeXHR(routes, { value: fresh([]) }, calls), "/discover/movies");
  w.hgNightLabel.state.poll = 300;
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/discover/movies");
  x.send();
  await settle();
  const card = cardOf(w);
  assert.equal(badgeIcons(card), "🔍🌙");
  assert.equal(searchIcon(card).getAttribute("data-hg-search"), "live");
  const el = card.querySelector("[data-hg-night-own]");
  rs.value = { ...rs.value, updated: new Date().toISOString() };
  await settle();
  assert.equal(card.querySelector("[data-hg-night-own]"), el, "unchanged state: no re-render");
  rs.value = live({ canWait: ["movie/700"], noRelease: { "movie/700": new Date().toISOString() } });
  await settle();
  assert.equal(badgeIcons(card), "🌙🧭");
  const n = calls.filter((u) => u === RS_URL).length;
  await settle();
  assert.equal(calls.filter((u) => u === RS_URL).length, n, "no search badge: back to the slow refresh");
});

// every reachable state of a requested title that is not available has a badge (owner rule: no gaps)
const STATES = [
  ["just requested", { fresh: { k: "NOW" } }, "🔎"],
  ["requested, not searched", { fresh: { k: null } }, "🔎"],
  ["waiting for approval", { pending: ["k"] }, "⏳"],
  ["search queued", { searching: { k: { status: "queued", ahead: 1 } } }, "🔍"],
  ["search running", { searching: { k: { status: "started", started: "NOW" } } }, "🔍"],
  ["search running, Can wait", { canWait: ["k"], searching: { k: { status: "started", started: "NOW" } } }, "🔍🌙"],
  ["found, paused for the night", { canWait: ["k"], queue: { k: { state: "paused" } } }, "🌙"],
  ["downloading", { queue: { k: { state: "downloading" } } }, "◌"],
  ["importing", { queue: { k: { state: "importing" } } }, "◌"],
  ["imported", { imported: ["k"] }, "✅"],
  ["nothing found", { noRelease: { k: "NOW" } }, "🧭"],
  ["nothing found, Can wait", { canWait: ["k"], noRelease: { k: "NOW" } }, "🌙🧭"],
  ["not released", { unreleased: { k: "2027-02-02" } }, "📅"],
  ["not released, Can wait", { canWait: ["k"], unreleased: { k: "2027-02-02" } }, "🌙📅"],
  ["problem", { queue: { k: { state: "problem", message: "Import blocked" } } }, "⚠️"],
  ["Seerr failed", { queue: { k: { state: "problem", message: "", code: "seerr" } } }, "⚠️"],
  ["complete so far", { soFar: ["k"], unreleased: { k: "2027-02-02" } }, "✅📅"],
];
const forKey = (o, key) => JSON.parse(JSON.stringify(o).replace(/"k"/g, JSON.stringify(key)).replace(/"NOW"/g, JSON.stringify(new Date().toISOString())));

test("every reachable state maps to a badge, on cards and on the Requested pill", async () => {
  const ids = STATES.map((_, i) => 950 + i);
  const merged = STATES.reduce((acc, [, s], i) => {
    const o = forKey(s, `movie/${ids[i]}`);
    for (const [k, v] of Object.entries(o)) acc[k] = Array.isArray(v) ? [...(acc[k] || []), ...v] : { ...(acc[k] || {}), ...v };
    return acc;
  }, live());
  const rs = { value: merged };
  const w = discover(ids.map((id) => clockCard(`/l${id}.jpg`)).join("") + ids.map((id) => requestCard(`movie/${id}`, String(id)).replace(badge(`movie/${id}`), plainBadge(`movie/${id}`))).join(""), ids.map((id) => listed(id)), rs);
  await settle();
  const pills = [...w.document.querySelectorAll('a[href$="?manage=1"]')];
  STATES.forEach(([name, , icons], i) => {
    const card = cardOf(w, i);
    assert.equal(badgeIcons(card), icons, name);
    assert.ok(card.querySelector("[data-hg-night]").getAttribute("data-hg-hint"), name);
    assert.ok(pills[i].querySelector("[data-hg-flag-own]"), `pill: ${name}`);
    assert.ok(pills[i].getAttribute("data-hg-hint"), `pill hint: ${name}`);
  });
});


// pills: Seerr's own spinner/progress = nothing of ours (its popup says it); Partially Available = not green, ◐ first, never ✅
const partPill = (key, text = "Partially Available") => `<a href="/${key}?manage=1" class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full whitespace-nowrap transition cursor-pointer !no-underline bg-green-500/80 border border-green-500 !text-green-100 hover:bg-green-500 false overflow-hidden"><div class="relative z-20 flex items-center false"><span>${text}</span></div></a>`;
const pillIcons = (b) => [...b.querySelectorAll("[data-hg-flag-own]")].map((e) => e.querySelector("[data-hg-half]") ? "◐" : e.querySelector("[data-hg-spin]") ? "◌" : e.querySelector("[data-hg-search]") ? "🔍" : e.textContent).join("");

test("a pill with Seerr's own download spinner gets no second spinner; the title page pill a hint, request cards none", async () => {
  const w = rsPage(header(badge("movie/550", "Processing", "")) + requestCard("movie/550", "Fight"), "/movie/550", live({ queue: { "movie/550": { state: "downloading" } } }));
  await settle();
  for (const b of w.document.querySelectorAll('a[href$="?manage=1"]')) {
    assert.equal(b.querySelector("[data-hg-flag-own]"), null);
    assert.equal(b.hasAttribute("data-hg-hint"), !!b.closest(".media-status"));
    assert.equal(b.querySelectorAll("svg").length, 1);
  }
});

test("Partially Available pill: restyled away from green, ◐ first with the missing part's state, never ✅", async () => {
  const w = rsPage(requestCard("tv/76479", "Boys").replace(badge("tv/76479"), partPill("tv/76479")) + requestCard("tv/2", "Mixed").replace(badge("tv/2"), partPill("tv/2")) + requestCard("tv/3", "Solo").replace(badge("tv/3"), partPill("tv/3", "Teilweise verfügbar")), "/requests",
    live({ canWait: ["tv/76479"], unreleased: { "tv/76479": "2026-11-11" }, searching: { "tv/2": { status: "queued", ahead: 0 } }, imported: ["tv/3"] }));
  await settle();
  const [boys, mixed, solo] = w.document.querySelectorAll('a[href$="?manage=1"]');
  for (const b of [boys, mixed, solo]) assert.ok(b.hasAttribute("data-hg-part"));
  assert.match(w.document.getElementById("hg-night-style").textContent, /a\[data-hg-part\][^{]*\{[^}]*background-color:[^}]*!important/);
  assert.equal(pillIcons(boys), "◐🌙📅");
  assert.equal(boys.getAttribute("data-hg-hint"), "Partly available · Waiting for the night · Not released yet — next episode 11 Nov");
  assert.equal(pillIcons(mixed), "◐🔍");
  assert.equal(mixed.getAttribute("data-hg-hint"), "Partly available · Waiting for other searches (0 ahead)");
  assert.equal(pillIcons(solo), "◐");
  assert.equal(solo.getAttribute("data-hg-hint"), "Teilweise verfügbar");
});

test("hints are static: a pill's hint shows once above it (never inside Seerr's cursor-following popup), a card's below it; no move handlers, Escape closes", async () => {
  const types = [];
  const routes = { ...RS_ROUTES, [RS_URL]: RS, "/api/v1/discover/movies": { results: [{ id: 700, mediaType: "movie", title: "Tied", posterPath: "/tied.jpg", mediaInfo: { tmdbId: 700, mediaType: "movie", status: 3, downloadStatus: [], downloadStatus4k: [] } }] } };
  const w = night(clockCard("/tied.jpg") + header(plainBadge("movie/700")), (win) => {
    fakeXHR(routes, { value: fresh([]) })(win);
    const add = win.EventTarget.prototype.addEventListener;
    win.EventTarget.prototype.addEventListener = function (t, ...rest) { types.push(t); return add.call(this, t, ...rest); };
  }, "/movie/700");
  const x = new w.XMLHttpRequest();
  x.open("GET", "/api/v1/discover/movies");
  x.send();
  await settle();
  const ev = (t, type = "mouse") => { const e = new w.MouseEvent(t, { bubbles: t === "pointerdown" || t === "click", cancelable: true }); Object.defineProperty(e, "pointerType", { value: type }); return e; };
  const pill = w.document.querySelector(".media-status a");
  assert.equal(pill.hasAttribute("title"), false);
  pill.dispatchEvent(ev("pointerenter"));
  assert.equal(visibleTip(w), "Waiting for the night — downloads at night (cheaper power)");
  w.document.body.insertAdjacentHTML("beforeend", '<div class="z-50 text-sm absolute font-normal bg-gray-800">Manage Movie</div>');
  await tick();
  assert.equal(w.document.body.lastElementChild.textContent, "Manage Movie", "Seerr's popup stays as it is");
  pill.dispatchEvent(ev("pointerleave"));
  assert.equal(visibleTip(w), null);
  const chip = w.document.querySelector('[role="link"] [data-hg-night]');
  chip.dispatchEvent(ev("pointerenter"));
  const tip = w.document.getElementById("hg-flag-tip");
  assert.ok(tip && shown(w, tip));
  assert.equal(tip.textContent, "Waiting for the night — downloads at night (cheaper power)");
  assert.equal(chip.getAttribute("aria-describedby"), "hg-flag-tip");
  assert.ok(tip.style.top);
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));
  assert.equal(shown(w, tip), false);
  assert.equal(chip.hasAttribute("aria-describedby"), false);
  chip.dispatchEvent(ev("pointerdown"));
  const click = ev("click");
  chip.dispatchEvent(click);
  assert.equal(click.defaultPrevented, false, "a mouse click goes straight through");
  assert.ok(!types.some((t) => /move/.test(t)), types.join(","));
});

test("night-paused popup: patched before the next paint, the bar keeps its width and does not animate", async () => {
  const w = page(header(badge("tv/76479")), "/tv/76479");
  await settle();
  w.document.body.insertAdjacentHTML("beforeend", `<div class="z-50 absolute">${downloadBlock(BOYS_ITEM.title, "paused")}</div>`);
  await tick();
  const block = w.document.body.lastElementChild.querySelector(".p-4");
  assert.equal(visibleText(w, block.querySelector(".capitalize").parentElement), "Waiting for the night 🌙");
  const css = w.document.getElementById("hg-night-style").textContent;
  assert.match(css, /\[data-hg-night-grey\]\{[^}]*transition:none !important/);
  assert.doesNotMatch(css, /\[data-hg-night-grey\]\{[^}]*width/);
  assert.equal(w.document.querySelector(".media-status a [data-hg-night-own]").textContent, "🌙 Tonight");
});

test("caches stay small: thousands of remembered posters are pruned, cards still find theirs", async () => {
  const w = rsPage(clockCard("/tied.jpg"), "/discover/movies");
  await settle();
  const many = { results: Array.from({ length: 3000 }, (_, i) => ({ id: 5000 + i, mediaType: "movie", title: String(i), posterPath: `/m${i}.jpg`, mediaInfo: { tmdbId: 5000 + i, mediaType: "movie", status: 3, downloadStatus: [], downloadStatus4k: [] } })) };
  w.hgNightLabel.remember(many);
  w.hgNightLabel.remember({ results: [{ id: 700, mediaType: "movie", title: "Tied", posterPath: "/tied.jpg", mediaInfo: { tmdbId: 700, mediaType: "movie", status: 3, downloadStatus: [], downloadStatus4k: [] } }] });
  assert.ok(Object.keys(w.hgNightLabel.state.posters).length <= 1500);
  assert.ok(Object.keys(w.hgNightLabel.state.media).length <= 1500);
  w.hgNightLabel.apply(w.document, w.location);
  assert.equal(visibleText(w, cardOf(w)), "🌙");
});

test("shared language for the other scripts", async () => {
  const w = rsPage('<a href="/discover/tv">Serien</a>', "/");
  await settle();
  assert.equal(w.hgNightLabel.lang(), "de");
});

// series complete so far (every aired requested episode here, only unaired left): ✅ + 📅 + 🌙, the pill reads "Available so far", stays green
test("complete so far: the Partially Available pill becomes 'Available so far' (green) with ✅📅🌙; ended series ✅ alone", async () => {
  const w = rsPage(requestCard("tv/76479", "Dungeon").replace(badge("tv/76479"), partPill("tv/76479")) + requestCard("tv/2", "Ended").replace(badge("tv/2"), partPill("tv/2")), "/requests",
    live({ canWait: ["tv/76479"], unreleased: { "tv/76479": "2026-11-11" }, soFar: ["tv/76479", "tv/2"] }));
  await settle();
  const [dungeon, ended] = w.document.querySelectorAll('a[href$="?manage=1"]');
  assert.equal(dungeon.querySelector("span").textContent, "Available so far");
  assert.equal(dungeon.hasAttribute("data-hg-part"), false);
  assert.equal(pillIcons(dungeon), "✅📅🌙");
  assert.equal(dungeon.getAttribute("data-hg-hint"), "Everything released so far is in Jellyfin · Next episode expected 11 Nov · downloads at night");
  assert.equal(pillIcons(ended), "✅");
  assert.equal(ended.getAttribute("data-hg-hint"), "Everything released so far is in Jellyfin");
});

test("complete so far: German pill and card ✅📅🌙 instead of ◐", async () => {
  const rs = { value: live({ canWait: ["movie/702"], unreleased: { "movie/702": "2026-11-11" }, soFar: ["movie/702"] }) };
  const w = discover(nativeCard("/l702.jpg", "partial") + header(partPill("movie/702", "Teilweise verfügbar")), [listed(702, 4)], rs);
  w.document.body.insertAdjacentHTML("beforeend", '<a href="/discover/tv">Serien</a>');
  await settle();
  const card = cardOf(w);
  assert.equal(badgeIcons(card), "✅📅🌙");
  assert.ok(card.querySelector("[data-hg-night-solid]"));
  assert.equal(card.querySelector("[data-hg-night]").getAttribute("data-hg-hint"), `Alles bisher Erschienene ist in Jellyfin · Nächste Folge erwartet ${new Date(Date.UTC(2026, 10, 11)).toLocaleDateString("de-CH", { day: "numeric", month: "short", timeZone: "UTC" })} · lädt nachts`);
});

// a user without Manage Requests: Seerr gives a pill without a download neither a link nor a popup, only the downloading pill has one
const linklessPill = (text = "Requested") => `<span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full whitespace-nowrap cursor-default bg-indigo-500/80 border border-indigo-500 !text-indigo-100 false overflow-hidden"><div class="relative z-20 flex items-center false"><span>${text}</span></div></span>`;
const ownCard = (key, name, pill) => `
<div class="relative flex w-72 overflow-hidden rounded-xl" data-testid="request-card"><div class="relative z-10 flex min-w-0 flex-1 flex-col pr-4" data-testid="request-card-title">
  <a href="/${key}" class="overflow-hidden text-base font-bold text-white">${name}</a>
  <div class="mt-2 flex items-center text-sm sm:mt-1"><span class="mr-2 hidden font-bold sm:block">Status</span>${pill}</div>
</div></div>`;
const hover = (w, el) => { const e = new w.MouseEvent("pointerenter", { cancelable: true }); Object.defineProperty(e, "pointerType", { value: "mouse" }); el.dispatchEvent(e); };
const unhover = (w, el) => { const e = new w.MouseEvent("pointerleave", { cancelable: true }); Object.defineProperty(e, "pointerType", { value: "mouse" }); el.dispatchEvent(e); };

test("every request card's pill shows its own hint in our static box, with or without a Seerr popup", async () => {
  const rs = { value: live({ canWait: ["tv/76479", "movie/700"], noRelease: { "movie/700": "2026-10-28T10:00:23Z" }, queue: { "tv/76479": { state: "paused" } }, imported: ["movie/120"] }) };
  const routes = { ...RS_ROUTES, "/api/v1/movie/120": details("movie", 120, "Fellowship", "/fellowship.jpg", []) };
  Object.defineProperty(routes, RS_URL, { get: () => rs.value, enumerable: true });
  const w = night(requestCard("tv/76479", "The Boys") + ownCard("movie/120", "Fellowship", linklessPill()) + ownCard("movie/700", "Tied Up", linklessPill()), fakeXHR(routes, { value: WAITING }), "/");
  await settle();
  const pills = [...w.document.querySelectorAll('[data-testid="request-card"] .inline-flex.rounded-full')];
  assert.deepEqual(pills.map((p) => visibleText(w, p)), ["🌙 Tonight", "Requested|✅", "Requested|🌙|🧭"]);

  hover(w, pills[0]);
  w.document.body.insertAdjacentHTML("beforeend", `<div class="z-50 text-sm absolute font-normal bg-gray-800">${downloadBlock(BOYS_ITEM.title, "paused")}</div>`);
  await tick();
  const popup = w.document.body.lastElementChild;
  assert.match(visibleTip(w), /^Waiting for the night 🌙 — Starts automatically at 22:05/);
  assert.equal(popup.querySelector("[data-hg-pop-hint]"), null, "nothing of ours inside Seerr's moving popup");
  unhover(w, pills[0]);
  popup.remove();

  hover(w, pills[1]);
  assert.equal(visibleTip(w), "Downloaded — in Jellyfin shortly");
  assert.equal(pills[1].getAttribute("aria-describedby"), "hg-flag-tip");
  unhover(w, pills[1]);
  assert.equal(visibleTip(w), null);

  hover(w, pills[2]);
  assert.equal(visibleTip(w), "Waiting for the night · No release found yet — searched again in tonight's scheduled search (04:17), downloads at night when found");
  unhover(w, pills[2]);

});

test("downloaded while Seerr still says Requested: ✅ 'in Jellyfin shortly', gone once Seerr says Available", async () => {
  const rs = { value: live({ imported: ["movie/120"] }) };
  const routes = { ...RS_ROUTES, "/api/v1/movie/120": details("movie", 120, "Fellowship", "/fellowship.jpg", []) };
  Object.defineProperty(routes, RS_URL, { get: () => rs.value, enumerable: true });
  const w = night(ownCard("movie/120", "Fellowship", linklessPill()), fakeXHR(routes, { value: fresh([]) }), "/");
  await settle();
  const pill = () => w.document.querySelector('[data-testid="request-card"] .inline-flex.rounded-full');
  assert.equal(visibleText(w, pill()), "Requested|✅");
  assert.equal(pill().getAttribute("data-hg-hint"), "Downloaded — in Jellyfin shortly");
  routes["/api/v1/movie/120"].mediaInfo.status = 5;
  rs.value = live();
  pill().outerHTML = linklessPill("Available").replace("bg-indigo-500/80 border border-indigo-500 !text-indigo-100", "bg-green-500/80 border border-green-500 !text-green-100");
  w.hgNightLabel.refresh();
  await settle();
  assert.equal(visibleText(w, pill()), "Available");
  assert.equal(pill().hasAttribute("data-hg-hint"), false);
});

test("login page: no data is fetched (the JSON needs a Seerr session)", async () => {
  const calls = [];
  night("", fakeXHR({}, { value: fresh([]) }, calls), "/login");
  await settle();
  assert.deepEqual(calls.filter((u) => u.startsWith("/_indexer-bar/")), []);
});

const at60 = (html) => html.replace("width: 0%;", "width: 60%;");
const TITLE_STATES = [
  { name: "requested", html: () => plainBadge("movie/900"), icons: "", en: "Requested — approved, waiting for the search" },
  { name: "pending", html: () => pendingBadge("movie/900"), icons: "⏳", en: "Waiting for approval" },
  { name: "processing, no download", html: () => plainBadge("movie/900", "Processing"), icons: "", en: "Processing — handed to Radarr/Sonarr" },
  { name: "processing, downloading", html: () => at60(badge("movie/550", "Processing", "")), icons: "", en: "Downloading now — 60 %" },
  { name: "available", html: () => plainBadge("movie/900", "Available"), icons: "✅", en: "Available in Jellyfin" },
  { name: "partially available", html: () => partPill("movie/900"), icons: "◐", en: "Partly available" },
  { name: "declined", html: () => plainBadge("movie/900", "Declined"), icons: "", en: "Declined — an admin refused the request" },
  { name: "unknown", html: () => plainBadge("movie/900", "Unknown"), icons: "", en: "Unknown — Seerr has no state for it" },
  { name: "deleted", html: () => plainBadge("movie/900", "Deleted"), icons: "", en: "Deleted — removed from Jellyfin" },
];

for (const s of TITLE_STATES) {
  test(`title page pill "${s.name}": hint${s.icons ? " and symbol " + s.icons : ""}`, async () => {
    const key = s.name === "processing, downloading" ? "/movie/550" : "/movie/900";
    const w = rsPage(header(s.html()), key, live());
    await settle();
    const b = w.document.querySelector(".media-status a");
    assert.equal(flagTip(b), s.en);
    assert.equal(pillIcons(b), s.icons);
    assert.equal(b.hasAttribute("title"), false);
  });
}

test("title page pill hint: mouse hover shows it above the pill once, touch needs a first tap, no flicker", async () => {
  const w = rsPage(header(at60(badge("movie/550", "Processing", ""))), "/movie/550", live());
  await settle();
  const ev = (t, type) => { const e = new w.MouseEvent(t, { bubbles: true, cancelable: true }); Object.defineProperty(e, "pointerType", { value: type }); return e; };
  const pill = w.document.querySelector(".media-status a");
  pill.dispatchEvent(ev("pointerenter", "mouse"));
  assert.equal(visibleTip(w), "Downloading now — 60 %");
  pill.dispatchEvent(ev("pointerleave", "mouse"));
  assert.equal(visibleTip(w), null);
  const tap = ev("click", "touch");
  pill.dispatchEvent(ev("pointerdown", "touch"));
  pill.dispatchEvent(tap);
  assert.equal(visibleTip(w), "Downloading now — 60 %");
  assert.equal(tap.defaultPrevented, true, "the first tap only shows the hint");
  const before = pill.getAttribute("data-hg-hint");
  await settle();
  assert.equal(pill.getAttribute("data-hg-hint"), before);
  pill.dispatchEvent(ev("pointerdown", "touch"));
  const second = ev("click", "touch");
  pill.dispatchEvent(second);
  assert.equal(second.defaultPrevented, false, "the second tap goes through");
});

test("title page pill hints in German", async () => {
  for (const [html, de] of [[at60(badge("movie/550", "Verarbeiten", "")), "Lädt gerade herunter — 60 %"], [plainBadge("movie/900", "Verfügbar"), "In Jellyfin verfügbar"], [plainBadge("movie/900", "Angefragt"), "Angefragt — freigegeben, wartet auf die Suche"], [plainBadge("movie/900", "Abgelehnt"), "Abgelehnt — ein Admin hat die Anfrage abgelehnt"]]) {
    const w = rsPage(header(html), html.includes("550") ? "/movie/550" : "/movie/900", live());
    await settle();
    assert.equal(flagTip(w.document.querySelector(".media-status a")), de);
  }
});

test("title page pill: the hint follows the state (spinner ends, hint goes to Available)", async () => {
  const w = rsPage(header(badge("movie/900", "Processing", "")), "/movie/900", live());
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(flagTip(b), "Downloading now");
  w.document.querySelector(".media-status").innerHTML = plainBadge("movie/900", "Available");
  await settle();
  const a = w.document.querySelector(".media-status a");
  assert.equal(flagTip(a), "Available in Jellyfin");
  assert.equal(pillIcons(a), "✅");
});

test("touch tap: the browser focuses the link before the click, the first tap still only shows the hint", async () => {
  const w = rsPage(header(plainBadge("movie/900")), "/movie/900", live());
  await settle();
  const ev = (t, type) => { const e = new w.MouseEvent(t, { bubbles: true, cancelable: true }); Object.defineProperty(e, "pointerType", { value: type }); return e; };
  const pill = w.document.querySelector(".media-status a");
  pill.dispatchEvent(ev("pointerdown", "touch"));
  pill.dispatchEvent(new w.FocusEvent("focus"));
  const tap = ev("click", "touch");
  pill.dispatchEvent(tap);
  assert.equal(tap.defaultPrevented, true);
  assert.equal(visibleTip(w), "Requested — approved, waiting for the search");
});
