import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { load, tick, SIDEBAR } from "./fixture.mjs";

const ORIGINS = { requests: {
  57: { source: "jellyfin", name: "Jane Doe", seerrUser: 7, person: 7, service: false },
  62: { source: "tv", name: "John Doe", seerrUser: 8, person: null, service: true },
  54: { source: "moonfin", name: "Jane Doe", seerrUser: 7, person: 7, service: false },
} };
const LIST_URL = "/api/v1/request?take=10&skip=0&filter=all&mediaType=all&sort=added&sortDirection=desc";
// id, type, tmdb id, requester (another user's request too: viewers with "View Requests" see every request)
const REQS = [[57, "movie", 1480613, [7, "Jane Doe"]], [62, "movie", 120, [8, "Jellyfin popular"]], [54, "tv", 213375, [5, "Max Muster"]], [41, "tv", 224377, [5, "Max Muster"]]];
const listJson = (reqs) => ({ pageInfo: { pages: 1, pageSize: 10, results: reqs.length, page: 1 }, results: reqs.map(([id, type, tmdb]) => ({ id, type, media: { tmdbId: tmdb } })) });
const STATE = { canWait: [], sizes: { 57: { queued: 5378662671 }, 62: { size: 45327147274 }, 54: { size: 22865343090, files: 5, of: 6 }, 41: { size: 1e9 } }, updated: new Date().toISOString() };

// RequestItem as Seerr renders it for a viewer with View Requests (src/components/RequestList/RequestItem), reduced
const item = ([id, type, tmdb, [uid, uname]], label = "Requested") => `
<div class="py-2"><div class="relative flex w-full flex-col justify-between overflow-hidden rounded-xl bg-gray-800 py-2 text-gray-400 shadow-md ring-1 ring-gray-700 xl:h-28 xl:flex-row">
  <div class="relative flex w-full flex-col justify-between overflow-hidden sm:flex-row">
    <div class="relative z-10 flex w-full items-center overflow-hidden pl-4 pr-4 sm:pr-0 xl:w-7/12 2xl:w-2/3"><a href="/${type}/${tmdb}" class="relative h-auto w-12"><img alt=""></a>
      <div class="flex flex-col justify-center overflow-hidden pl-2 xl:pl-4"><div class="pt-0.5 text-xs">2001</div><a href="/${type}/${tmdb}" class="mr-2 min-w-0 truncate text-lg font-bold">Title ${id}</a></div></div>
    <div class="z-10 ml-4 mt-4 flex w-full flex-col justify-center gap-1 overflow-hidden pr-4 text-sm sm:ml-2 sm:mt-0 xl:flex-1 xl:pr-0">
      <div class="card-field"><span class="card-field-name">Status</span><span>Available</span></div>
      <div class="card-field"><span class="card-field-name">${label}</span><span class="flex truncate text-sm text-gray-300">1 hour ago by <a href="/users/${uid}" class="group flex items-center truncate"><span class="avatar-sm ml-1.5"><img alt=""></span><span class="truncate text-sm group-hover:underline">${uname}</span></a></span></div>
      <div class="card-field"><span class="card-field-name">Profile</span><span class="flex truncate text-sm text-gray-300">4K</span></div>
    </div></div>
  <div class="z-10 mt-4 flex w-full flex-col justify-center space-y-2 pl-4 pr-4 xl:mt-0 xl:w-96 xl:items-end xl:pl-0"><button>Delete Request</button></div>
</div></div>`;
const sel = (id, opts, value, cls = "rounded-r-only") => `<select id="${id}" name="${id}" class="${cls}">${opts.map((o) => `<option value="${o}"${o === value ? " selected" : ""}>${o}</option>`).join("")}</select>`;
const page = (rows, sidebar = SIDEBAR) => `${sidebar}
<div class="max-w-8xl mx-auto px-4">
  <div class="mb-4 flex flex-col justify-between lg:flex-row lg:items-end"><div class="header"><h1>Requests</h1></div>
    <div class="mt-2 flex flex-grow flex-col sm:flex-row lg:flex-grow-0">
      <div class="mb-2 flex flex-grow sm:mb-0 sm:mr-2 lg:flex-grow-0"><span class="inline-flex"><svg></svg></span>${sel("mediaType", ["all", "movie", "tv"], "all")}</div>
      <div class="mb-2 flex flex-grow sm:mb-0 lg:flex-grow-0"><span class="inline-flex"><svg></svg></span>${sel("sort", ["added", "modified"], "added", "rounded-none border-r-0")}<button class="z-40 mr-2 rounded-l-none border"><svg></svg></button></div>
    </div></div>
  ${rows}
  <div class="actions"><nav aria-label="Pagination"><span>Display ${sel("pageSize", ["5", "10", "25", "50", "100"], "10", "short inline")} results per page</span></nav></div>
</div>`;

function fakeXHR(routes, calls) {
  return function (w) {
    w.XMLHttpRequest = class {
      constructor() { this.listeners = []; }
      addEventListener(type, fn) { if (type === "load") this.listeners.push(fn); }
      open(method, url) { this.method = method; this.url = url; }
      send() {
        calls.push(this.url);
        const r = typeof routes[this.url] === "function" ? routes[this.url]() : routes[this.url];
        this.status = r ? 200 : 403;
        this.responseText = JSON.stringify(r ?? {});
        setTimeout(() => { this.onload && this.onload(); this.listeners.forEach((f) => f.call(this)); }, 0);
      }
    };
  };
}

const open = [];
afterEach(() => { while (open.length) open.pop().close(); });
const settle = async () => { for (let i = 0; i < 6; i++) { await new Promise((r) => setTimeout(r, 5)); await tick(); } };
const ORIGIN_JS = readFileSync(new URL("../../indexer-bar/www/request-origin.js", import.meta.url), "utf8");

async function boot(body, path, routes, before) {
  const calls = [];
  const w = load("request-sizes.js", body, (win) => { fakeXHR(routes, calls)(win); if (before) before(win); }, "https://requests.example.com" + path);
  w.eval(ORIGIN_JS);
  open.push(w);
  w.calls = calls;
  return w;
}

async function requests({ origins = ORIGINS, label, before, sidebar } = {}) {
  const routes = { "/_indexer-bar/request-state.json": STATE, [LIST_URL]: listJson(REQS), "/_download-now/origins": origins };
  const w = await boot(page(REQS.map((r) => item(r, label)).join(""), sidebar), "/requests", routes, before);
  const x = new w.XMLHttpRequest(); x.open("GET", LIST_URL); x.send();
  await settle();
  return w;
}

const pills = (w) => [...w.document.querySelectorAll(".max-w-8xl > div.py-2")].map((r) => { const p = r.querySelector("[data-hg-origin]"); return p ? p.textContent : null; });
const sizes = (w) => [...w.document.querySelectorAll(".max-w-8xl > div.py-2")].map((r) => { const l = r.querySelector("[data-hg-size]"); return l ? l.lastChild.textContent : null; });

test("Requests: a pill per Jellyfin-made request, the person for service-user ones, direct Seerr requests unmarked", async () => {
  const w = await requests();
  assert.deepEqual(pills(w), ["via Jellyfin", "via TV · John Doe", "via Moonfin", null]);
  const p = w.document.querySelector("[data-hg-origin]");
  assert.equal(p.parentElement.querySelector(".card-field-name").textContent, "Requested");
  assert.equal(p.getAttribute("data-hg-origin"), "jellyfin");
  assert.match(w.document.querySelectorAll("[data-hg-origin]")[1].getAttribute("title"), /service user Jellyfin popular; requested by John Doe/);
});

test("Requests: every visible row gets its size, also other users' requests", async () => {
  const w = await requests();
  assert.deepEqual(sizes(w), ["≈ 5.4 GB · in queue", "45.3 GB", "22.9 GB · 5 of 6 episodes", "1.0 GB"]);
});

test("Requests: German labels; hint through the night label's hint box", async () => {
  const hints = [];
  const w = await requests({ label: "Angefragt", before: (win) => { win.hgNightLabel = { lang: () => "de", hint: (el, text) => hints.push(text) }; } });
  assert.deepEqual(pills(w), ["über Jellyfin", "über TV · John Doe", "über Moonfin", null]);
  assert.ok(hints.includes("Mit 🌙/⚡ auf Jellyfins Beliebt-Seite angefragt"));
});

test("Requests: no origin data (no session, backend down) = Seerr unchanged; sizes stay", async () => {
  const w = await requests({ origins: null });
  assert.deepEqual(pills(w), [null, null, null, null]);
  assert.equal(sizes(w)[1], "45.3 GB");
});

test("Requests: runs are idempotent (one pill per row after many runs)", async () => {
  const w = await requests();
  for (let i = 0; i < 5; i++) w.hgRequestOrigin.run();
  await settle();
  assert.equal(w.document.querySelectorAll("[data-hg-origin]").length, 3);
});

const facts = () => `<div class="media-page"><div class="media-overview-right"><div class="media-facts">
  <div class="media-fact"><span>Original Title</span><span class="media-fact-value">Orig</span></div>
  <div class="media-fact"><span>Status</span><span class="media-fact-value">Released</span></div>
  <div class="media-fact"><span>Release Dates</span><span class="media-fact-value">19 Dec 2001</span></div>
</div></div></div>`;
const factRows = (w) => [...w.document.querySelectorAll(".media-fact")].map((f) => [...f.children].map((c) => c.textContent.trim()).join(" "));

async function titlePage(path, requestsOfTitle, origins = ORIGINS) {
  const routes = { "/_indexer-bar/request-state.json": { ...STATE, media: { "movie/120": { size: 45327147274 } } }, "/_download-now/origins": origins,
    ["/api/v1" + path.replace(/\/$/, "")]: { mediaInfo: { requests: requestsOfTitle.map((id) => ({ id })) } } };
  const w = await boot(SIDEBAR + facts(), path, routes);
  await settle();
  return w;
}

test("title page: a Requested via row after Size, with the person", async () => {
  const w = await titlePage("/movie/120", [62]);
  assert.deepEqual(factRows(w), ["Original Title Orig", "Status Released", "Size 45.3 GB", "Requested via TV (John Doe)", "Release Dates 19 Dec 2001"]);
  assert.equal(w.document.querySelector("[data-hg-origin-fact]").className, "media-fact");
});

test("title page: several requests listed once each; only direct Seerr requests = no row", async () => {
  const w = await titlePage("/tv/213375", [41, 54, 57]);
  assert.equal(w.document.querySelector("[data-hg-origin-fact]").textContent, "Requested viaMoonfin (Jane Doe), Jellyfin (Jane Doe)");
  const none = await titlePage("/tv/224377", [41]);
  assert.equal(none.document.querySelector("[data-hg-origin-fact]"), null);
});

test("other pages are left alone (no data call)", async () => {
  const w = await boot(SIDEBAR + facts(), "/discover/movies", { "/_download-now/origins": ORIGINS });
  await settle();
  assert.equal(w.calls.includes("/_download-now/origins"), false);
  assert.equal(w.document.querySelector("[data-hg-origin-fact]"), null);
});
