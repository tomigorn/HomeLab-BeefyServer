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
const open = [];
afterEach(() => { while (open.length) open.pop().close(); });
const night = (body, xhr, path) => { const w = load("night-label.js", body, xhr, "https://requests.example.com" + path); open.push(w); return w; };
const page = (body, path, waiting = WAITING, calls) => night(body, fakeXHR(ROUTES, { value: waiting }, calls), path);

test("title page: a night-paused download shows the night label with the hint, the progress overlay is hidden", async () => {
  const w = page(header(badge("tv/76479")), "/tv/76479");
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(visibleText(w, b), "Waiting for the night 🌙");
  assert.match(b.getAttribute("title"), /22:05 or on Sunday; untick 'Can wait' next time/);
  assert.equal(shown(w, b.querySelector(".z-10")), false);
});

test("released by Download now: the label goes at once and stays gone while the night list still names it", async () => {
  const w = page(header(badge("tv/76479")), "/tv/76479");
  await settle();
  const b = w.document.querySelector(".media-status a");
  assert.equal(visibleText(w, b), "Waiting for the night 🌙");
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
  assert.equal(visibleText(w, b), "Wartet auf die Nacht 🌙");
  assert.match(b.getAttribute("title"), /22:05 oder am Sonntag/);
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
  assert.deepEqual(badges, ["Waiting for the night 🌙", "Processing|S2", "Waiting for the night 🌙", "Processing|S2"]);
});

test("a series with one night item and one running download, and a running 4K download, stay normal", async () => {
  const w = page(requestCard("tv/2", "Mixed Show") + header(badge("tv/3") + badge("tv/3", "4K Processing")), "/tv/3");
  await settle();
  const [mixed, fourk, fourk4k] = [...w.document.querySelectorAll('a[href$="?manage=1"]')].map((b) => visibleText(w, b));
  assert.equal(mixed, "Processing|S2");
  assert.equal(fourk, "Waiting for the night 🌙");
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
  assert.equal(visibleText(w, w.document.querySelector(".media-status a")), "Waiting for the night 🌙");
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
  assert.equal(visibleText(w, b), "Waiting for the night 🌙");
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
const flagTip = (b) => b.getAttribute("title");
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
  assert.equal(visibleText(w, w.document.querySelector(".media-status a")), "Waiting for the night 🌙");
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
  assert.match(cat.querySelector("[data-hg-night]").getAttribute("title"), /^Waiting for the night · Not released yet — expected 2 Feb 2027$/);
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
  assert.equal(chip.getAttribute("title"), MERGED);
  assert.equal(chip.getAttribute("aria-label"), MERGED);
  assert.equal(badgeEl.getAttribute("title"), MERGED);
  assert.equal(badgeEl.querySelector("[data-hg-flag-own]").hasAttribute("title"), false);
  chip.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
  const tip = w.document.getElementById("hg-flag-tip");
  assert.ok(tip && shown(w, tip));
  assert.equal(tip.textContent, MERGED);
  assert.equal(card, 0);
  chip.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
  assert.equal(card, 1);
  assert.equal(shown(w, tip), false);
  badgeEl.querySelector("span").dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
  assert.equal(link, 0);
  assert.ok(shown(w, tip));
  assert.equal(tip.textContent, MERGED);
  badgeEl.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
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
  const hint = (b) => b.querySelector("[data-hg-night]").getAttribute("title");
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
  assert.equal(b.querySelector("[data-hg-night]").getAttribute("title"), "In Jellyfin verfügbar");
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
const badgeIcons = (b) => [...b.querySelector("[data-hg-night-own]").children].map((c) => c.hasAttribute("data-hg-half") ? "◐" : c.hasAttribute("data-hg-spin") ? "◌" : c.textContent).join("") || b.querySelector("[data-hg-night-own]").textContent;

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
      assert.equal(own.getAttribute("title"), r[lang].replace("DATE_DE", date), r.name);
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
