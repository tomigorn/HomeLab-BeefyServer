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
  assert.ok(shown(w, fight.querySelector("svg")));
  assert.equal(fight.querySelector("[data-hg-night-own]"), null);
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
