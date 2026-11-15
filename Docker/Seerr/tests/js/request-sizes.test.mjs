import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, tick, SIDEBAR } from "./fixture.mjs";

const SIZES = { 62: { size: 45327147274 }, 63: { size: 41234770435 }, 54: { size: 22865343090, files: 5, of: 6 }, 57: { queued: 5378662671 } };
const STATE = { canWait: [], sizes: SIZES, updated: new Date().toISOString() };
const LIST_URL = "/api/v1/request?take=10&skip=0&filter=all&mediaType=all&sort=added&sortDirection=desc";
// Seerr's /request answer: id, type, tmdb id (the rows below in the same order)
const REQS = [[57, "movie", 1480613], [62, "movie", 120], [54, "tv", 213375], [41, "tv", 224377], [63, "movie", 121]];
const listJson = (reqs, total = reqs.length) => ({ pageInfo: { pages: Math.ceil(total / 10), pageSize: 10, results: total, page: 1 }, results: reqs.map(([id, type, tmdb]) => ({ id, type, media: { tmdbId: tmdb } })) });

// RequestList + RequestItem as Seerr renders them (src/components/RequestList), reduced to what the script looks at
const item = ([id, type, tmdb], profile = true) => `
<div class="py-2"><div class="relative flex w-full flex-col justify-between overflow-hidden rounded-xl bg-gray-800 py-2 text-gray-400 shadow-md ring-1 ring-gray-700 xl:h-28 xl:flex-row">
  <div class="relative flex w-full flex-col justify-between overflow-hidden sm:flex-row">
    <div class="relative z-10 flex w-full items-center overflow-hidden pl-4 pr-4 sm:pr-0 xl:w-7/12 2xl:w-2/3"><a href="/${type}/${tmdb}" class="relative h-auto w-12"><img alt=""></a>
      <div class="flex flex-col justify-center overflow-hidden pl-2 xl:pl-4"><div class="pt-0.5 text-xs">2001</div><a href="/${type}/${tmdb}" class="mr-2 min-w-0 truncate text-lg font-bold">Title ${id}</a>
      ${type === "tv" ? '<div class="card-field"><span class="card-field-name">Season</span><div class="hide-scrollbar flex"><span class="mr-2">1</span></div></div>' : ""}</div></div>
    <div class="z-10 ml-4 mt-4 flex w-full flex-col justify-center gap-1 overflow-hidden pr-4 text-sm sm:ml-2 sm:mt-0 xl:flex-1 xl:pr-0">
      <div class="card-field"><span class="card-field-name">Status</span><span>Available</span></div>
      <div class="card-field"><span class="card-field-name">Requested</span><span class="flex truncate text-sm text-gray-300">1 hour ago</span></div>
      ${profile ? '<div class="card-field"><span class="card-field-name">Profile</span><span class="flex truncate text-sm text-gray-300">4K</span></div>' : ""}
    </div></div>
  <div class="z-10 mt-4 flex w-full flex-col justify-center space-y-2 pl-4 pr-4 xl:mt-0 xl:w-96 xl:items-end xl:pl-0"><button>Delete Request</button></div>
</div></div>`;
const placeholder = () => '<div class="py-2"><div class="h-64 w-full animate-pulse rounded-xl bg-gray-800 xl:h-28"></div></div>';
const sel = (id, opts, value, cls = "rounded-r-only") => `<select id="${id}" name="${id}" class="${cls}">${opts.map((o) => `<option value="${o}"${o === value ? " selected" : ""}>${o}</option>`).join("")}</select>`;
const page = (rows) => `${SIDEBAR}
<div class="max-w-8xl mx-auto px-4">
  <div class="mb-4 flex flex-col justify-between lg:flex-row lg:items-end"><div class="header"><h1>Requests</h1></div>
    <div class="mt-2 flex flex-grow flex-col sm:flex-row lg:flex-grow-0">
      <div class="mb-2 flex flex-grow sm:mb-0 sm:mr-2 lg:flex-grow-0"><span class="inline-flex"><svg></svg></span>${sel("mediaType", ["all", "movie", "tv"], "all")}</div>
      <div class="mb-2 flex flex-grow sm:mb-0 sm:mr-2 lg:flex-grow-0"><span class="inline-flex"><svg></svg></span>${sel("filter", ["all", "pending"], "all")}</div>
      <div class="mb-2 flex flex-grow sm:mb-0 lg:flex-grow-0"><span class="inline-flex"><svg></svg></span>${sel("sort", ["added", "modified"], "added", "rounded-none border-r-0")}<button class="z-40 mr-2 rounded-l-none border" id="dir"><svg></svg></button></div>
    </div></div>
  <div id="rows-start" hidden></div>
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
        this.status = r ? 200 : 404;
        this.responseText = JSON.stringify(r ?? {});
        setTimeout(() => { this.onload && this.onload(); this.listeners.forEach((f) => f.call(this)); }, 0);
      }
    };
  };
}

const open = [];
afterEach(() => { while (open.length) open.pop().close(); });
const settle = async () => { for (let i = 0; i < 4; i++) { await new Promise((r) => setTimeout(r, 5)); await tick(); } };
// Seerr's own list call through XHR (as SWR/axios do it)
const seerrList = (w, url = LIST_URL) => { const x = new w.XMLHttpRequest(); x.open("GET", url); x.send(); };

async function requests({ rows = REQS, list = listJson(REQS), state = STATE, path = "/requests", storage = {}, before } = {}) {
  const calls = [];
  const routes = { "/_indexer-bar/request-state.json": state, [LIST_URL]: list };
  const w = load("request-sizes.js", page(rows.map((r) => (typeof r === "string" ? r : item(r))).join("")), (win) => {
    fakeXHR(routes, calls)(win);
    for (const [k, v] of Object.entries(storage)) win.localStorage.setItem(k, v);
    if (before) before(win);
  }, "https://requests.example.com" + path);
  open.push(w);
  w.calls = calls;
  w.routes = routes;
  seerrList(w);
  await settle();
  return w;
}

const lines = (w) => [...w.document.querySelectorAll(".max-w-8xl > div.py-2")].map((r) => { const l = r.querySelector("[data-hg-size]"); return l ? l.textContent : null; });
const shownOrder = (w) => [...w.document.querySelectorAll(".max-w-8xl > div.py-2")].map((r, i) => [Number(r.style.order || 0), r.querySelector("a.text-lg") ? r.querySelector("a.text-lg").textContent : `row${i}`]).sort((a, b) => a[0] - b[0]).map((x) => x[1]);
const choose = (w, value) => { const s = w.document.querySelector("[data-hg-size-sort] select"); s.value = value; s.dispatchEvent(new w.Event("change", { bubbles: true })); };

test("every row gets a Size line after Profile, styled like it", async () => {
  const w = await requests();
  assert.deepEqual(lines(w), ["Size≈ 5.4 GB · in queue", "Size45.3 GB", "Size22.9 GB · 5 of 6 episodes · ⌀ 4.6 GB/ep", "Size—", "Size41.2 GB"]);
  const line = w.document.querySelectorAll(".max-w-8xl > div.py-2")[1].querySelector("[data-hg-size]");
  assert.equal(line.className, "card-field");
  assert.equal(line.previousElementSibling.textContent, "Profile4K");
  assert.equal(line.nextElementSibling, null);
  assert.equal(line.lastChild.className, "flex truncate text-sm text-gray-300");
  assert.ok(line.parentElement.parentElement.parentElement.hasAttribute("data-hg-size-tall"));
});

test("German: Grösse, Swiss number format, episodes and queue in German", async () => {
  const w = await requests({ before: (win) => { win.hgNightLabel = { lang: () => "de" }; } });
  assert.deepEqual(lines(w), ["Grösse≈ 5.4 GB · in der Warteschlange", "Grösse45.3 GB", "Grösse22.9 GB · 5 von 6 Episoden · ⌀ 4.6 GB/Ep.", "Grösse—", "Grösse41.2 GB"]);
  assert.deepEqual([...w.document.querySelectorAll("[data-hg-size-sort] option")].map((o) => o.textContent), ["Aus", "Grösste gesamt", "Kleinste gesamt", "Grösste pro Datei", "Kleinste pro Datei", "Grösste pro Stunde", "Kleinste pro Stunde"]);
});

test("a title whose queue state is problem reads '· problem' instead of 'in queue', also in German", async () => {
  const state = { ...STATE, queue: { "movie/1480613": { state: "problem", message: "", code: "needs-import", release: "X" }, "movie/120": { state: "problem", message: "" } } };
  const w = await requests({ state });
  assert.deepEqual(lines(w), ["Size≈ 5.4 GB · problem", "Size45.3 GB", "Size22.9 GB · 5 of 6 episodes · ⌀ 4.6 GB/ep", "Size—", "Size41.2 GB"]);
  const de = await requests({ state, before: (win) => { win.hgNightLabel = { lang: () => "de" }; } });
  assert.equal(lines(de)[0], "Grösse≈ 5.4 GB · Problem");
});

test("title page: queued with a problem state reads '· problem'", async () => {
  const q = await title("/movie/1480613", { queue: { "movie/1480613": { state: "problem", message: "" } } });
  assert.equal(q.document.querySelector("[data-hg-size-fact]").textContent, "Size≈ 5.4 GB · problem");
});

test("sizes in MB, GB and TB", async () => {
  const w = await requests();
  const s = w.hgRequestSizes;
  assert.deepEqual([s.bytes(812e6, "en"), s.bytes(1.234e12, "en"), s.bytes(368887525657, "de")], ["812 MB", "1.23 TB", "368.9 GB"]);
});

test("the size control sits right after Seerr's sort, off by default", async () => {
  const w = await requests();
  const box = w.document.querySelector("[data-hg-size-sort]");
  assert.equal(box.previousElementSibling, w.document.getElementById("sort").parentElement);
  assert.equal(box.className, "mb-2 flex flex-grow sm:mb-0 lg:flex-grow-0");
  assert.ok(box.parentElement.hasAttribute("data-hg-size-controls"));
  assert.equal(box.querySelector("select").className, "rounded-r-only");
  assert.deepEqual([...box.querySelectorAll("option")].map((o) => [o.value, o.textContent]), [["", "Off"], ["desc", "Largest total"], ["asc", "Smallest total"],
    ["file-desc", "Largest per file"], ["file-asc", "Smallest per file"], ["hour-desc", "Largest per hour"], ["hour-asc", "Smallest per hour"]]);
  assert.equal(box.querySelector("select").getAttribute("aria-label"), "Sort by size");
  assert.equal(w.document.querySelector("[data-hg-size-sorted]"), null);
});

test("largest first and smallest first reorder with CSS order only, titles without a size last", async () => {
  const w = await requests();
  const before = [...w.document.querySelectorAll(".max-w-8xl > div.py-2")];
  choose(w, "desc");
  await settle();
  assert.deepEqual(shownOrder(w), ["Title 62", "Title 63", "Title 54", "Title 57", "Title 41"]);
  assert.deepEqual([...w.document.querySelectorAll(".max-w-8xl > div.py-2")], before);
  const box = w.document.querySelector(".max-w-8xl");
  assert.equal(box.getAttribute("data-hg-size-sorted"), "desc");
  assert.ok(w.document.getElementById("sort").parentElement.hasAttribute("data-hg-size-overridden"));
  choose(w, "asc");
  await settle();
  assert.deepEqual(shownOrder(w), ["Title 57", "Title 54", "Title 63", "Title 62", "Title 41"]);
  choose(w, "");
  await settle();
  assert.equal(box.hasAttribute("data-hg-size-sorted"), false);
  assert.deepEqual([...w.document.querySelectorAll(".max-w-8xl > div.py-2")].map((r) => r.style.order), ["", "", "", "", ""]);
  assert.equal(w.localStorage.getItem("hg-size-sort"), null);
});

test("picking a Seerr sort or its direction brings Seerr's order back", async () => {
  for (const act of [(w) => { const s = w.document.getElementById("sort"); s.value = "modified"; s.dispatchEvent(new w.Event("change", { bubbles: true })); }, (w) => w.document.getElementById("dir").querySelector("svg").dispatchEvent(new w.MouseEvent("click", { bubbles: true }))]) {
    const w = await requests();
    choose(w, "desc");
    await settle();
    act(w);
    await settle();
    assert.equal(w.document.querySelector("[data-hg-size-sorted]"), null);
    assert.equal(w.document.querySelector("[data-hg-size-sort] select").value, "");
  }
});

test("sorting with more requests than shown switches Seerr to 100 per page, off restores the page size", async () => {
  const w = await requests({ list: listJson(REQS, 44) });
  const seen = [];
  w.document.getElementById("pageSize").addEventListener("change", (e) => seen.push(e.target.value));
  choose(w, "desc");
  await settle();
  assert.deepEqual(seen, ["100"]);
  assert.equal(w.document.querySelector("[data-hg-size-sort] select").getAttribute("title"), "Sorted within this page (5 of 44)");
  choose(w, "");
  await settle();
  assert.deepEqual(seen, ["100", "10"]);
});

test("a Seerr sort while sorting by size: the page size goes back once Seerr has loaded its new list", async () => {
  const w = await requests({ list: listJson(REQS, 44) });
  const seen = [];
  w.document.getElementById("pageSize").addEventListener("change", (e) => seen.push(e.target.value));
  choose(w, "desc");
  await settle();
  const s = w.document.getElementById("sort");
  s.value = "modified";
  s.dispatchEvent(new w.Event("change", { bubbles: true }));
  await settle();
  assert.deepEqual(seen, ["100"]);
  seerrList(w);
  await settle();
  assert.deepEqual(seen, ["100", "10"]);
});

test("the chosen order stays after a reload", async () => {
  const w = await requests({ storage: { "hg-size-sort": "asc" } });
  assert.equal(w.document.querySelector("[data-hg-size-sort] select").value, "asc");
  assert.deepEqual(shownOrder(w), ["Title 57", "Title 54", "Title 63", "Title 62", "Title 41"]);
});

test("a row Seerr renders later gets its line before the next paint (no timer)", async () => {
  const w = await requests({ rows: [REQS[0], REQS[1], placeholder(), REQS[3], REQS[4]] });
  assert.deepEqual(lines(w), ["Size≈ 5.4 GB · in queue", "Size45.3 GB", null, "Size—", "Size41.2 GB"]);
  const row = w.document.querySelectorAll(".max-w-8xl > div.py-2")[2];
  row.innerHTML = item(REQS[2]).trim().replace(/^<div class="py-2">|<\/div>$/g, "");
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(row.querySelector("[data-hg-size]").textContent, "Size22.9 GB · 5 of 6 episodes · ⌀ 4.6 GB/ep");
});

test("rows still loading are sorted (and sized for the taller row) by the newest list, so the right rows load first", async () => {
  const w = await requests({ rows: REQS.map(() => placeholder()), storage: { "hg-size-sort": "desc" } });
  const rows = [...w.document.querySelectorAll(".max-w-8xl > div.py-2")];
  assert.deepEqual(rows.map((r) => r.style.order), ["4", "1", "3", "5", "2"]);
  assert.ok(rows.every((r) => r.firstElementChild.hasAttribute("data-hg-size-tall")));
  assert.deepEqual(lines(w), [null, null, null, null, null]);
});

test("a list that does not match the rows or no sizes = Seerr unchanged", async () => {
  const other = await requests({ list: listJson([[1, "movie", 5], ...REQS.slice(1)]) });
  assert.deepEqual(lines(other), [null, null, null, null, null]);
  const none = await requests({ state: null });
  assert.deepEqual(lines(none), [null, null, null, null, null]);
  none.document.querySelector("[data-hg-size-sort] select").value = "desc";
  assert.equal(none.document.querySelector("[data-hg-size-sorted]"), null);
});

test("Seerr's list call made before the script ran is read again from the resource timing", async () => {
  const calls = [];
  const w = load("request-sizes.js", page(REQS.map((r) => item(r)).join("")), (win) => {
    fakeXHR({ "/_indexer-bar/request-state.json": STATE, [LIST_URL]: listJson(REQS) }, calls)(win);
    win.performance.getEntriesByType = (t) => (t === "resource" ? [{ name: "https://requests.example.com/api/v1/movie/1" }, { name: "https://requests.example.com" + LIST_URL }] : []);
    const X = win.XMLHttpRequest;
    win.XMLHttpRequest = class extends X { open(m, url) { super.open(m, url.replace("https://requests.example.com", "")); } };
  }, "https://requests.example.com/requests");
  open.push(w);
  await settle();
  assert.deepEqual(lines(w), ["Size≈ 5.4 GB · in queue", "Size45.3 GB", "Size22.9 GB · 5 of 6 episodes · ⌀ 4.6 GB/ep", "Size—", "Size41.2 GB"]);
});

test("other pages with a sort select (Issues) are left alone", async () => {
  const w = load("request-sizes.js", `${SIDEBAR}<div class="max-w-8xl"><div class="mb-4"><div><div>${sel("sort", ["added"], "added")}</div></div></div></div>`, fakeXHR({}, []), "https://requests.example.com/issues");
  open.push(w);
  await settle();
  assert.equal(w.document.querySelector("[data-hg-size-sort]"), null);
});

// Title page facts panel (src/components/MovieDetails, TvDetails "media-facts")
const MEDIA = {
  "movie/120": { size: 45327147274, quality: "2160p HDR10" },
  "movie/1480613": { queued: 5378662671 },
  "tv/84773": { size: 122003416606, files: 13, of: 16, seasons: { 1: { size: 82003416606, files: 8, of: 8 }, 2: { size: 40000000000, files: 5, of: 8 } } },
};
const facts = (status = "Status") => `<div class="media-page"><div class="media-overview-right"><div class="media-facts">
  <div class="media-ratings"><a class="media-rating"><span>91%</span></a></div>
  <div class="media-fact"><span>Original Title</span><span class="media-fact-value">Orig</span></div>
  <div class="media-fact"><span>${status}</span><span class="media-fact-value">Released</span></div>
  <div class="media-fact"><span>Release Dates</span><span class="media-fact-value">19 Dec 2001</span></div>
</div></div></div>`;
async function title(path, { body = facts(), media = MEDIA, queue = undefined, titleJson = null, before } = {}) {
  const calls = [];
  const routes = { "/_indexer-bar/request-state.json": { ...STATE, media, queue } };
  if (titleJson) routes["/api/v1" + path] = titleJson;
  const w = load("request-sizes.js", SIDEBAR + body, (win) => { fakeXHR(routes, calls)(win); if (before) before(win); }, "https://requests.example.com" + path);
  open.push(w);
  if (titleJson) { const x = new w.XMLHttpRequest(); x.open("GET", "/api/v1" + path); x.send(); }
  await settle();
  return w;
}
const factRows = (w) => [...w.document.querySelectorAll(".media-fact")].map((f) => [...f.children].map((c) => c.textContent.trim()).join(" "));

test("title page: a Size row right after Status, styled like Seerr's facts", async () => {
  const w = await title("/movie/120");
  assert.deepEqual(factRows(w), ["Original Title Orig", "Status Released", "Size 45.3 GB · 2160p HDR10", "Release Dates 19 Dec 2001"]);
  const row = w.document.querySelector("[data-hg-size-fact]");
  assert.equal(row.className, "media-fact");
  assert.equal(row.lastChild.className, "media-fact-value");
});

test("title page series: total, episodes, per-season hint; German", async () => {
  const hints = [];
  const w = await title("/tv/84773", { before: (win) => { win.hgNightLabel = { lang: () => "de", hint: (el, text) => hints.push(text) }; } });
  assert.equal(w.document.querySelector("[data-hg-size-fact]").textContent, "Grösse122.0 GB · 13 von 16 Episoden");
  assert.deepEqual(hints, ["Staffel 1: 82.0 GB · 8 von 8 Episoden\nStaffel 2: 40.0 GB · 5 von 8 Episoden"]);
});

test("title page: queued shows the expected size, nothing hides the row unless requested (then —)", async () => {
  const q = await title("/movie/1480613");
  assert.equal(q.document.querySelector("[data-hg-size-fact]").textContent, "Size≈ 5.4 GB · in queue");
  const none = await title("/movie/999");
  assert.equal(none.document.querySelector("[data-hg-size-fact]"), null);
  const asked = await title("/movie/998", { titleJson: { id: 998, mediaInfo: { requests: [{ id: 1 }] } } });
  assert.equal(asked.document.querySelector("[data-hg-size-fact]").textContent, "Size—");
});

test("title page: the row is in place in the same task Seerr renders the panel (no timer)", async () => {
  const w = await title("/movie/120", { body: '<div id="root"></div>' });
  w.document.getElementById("root").innerHTML = facts();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(w.document.querySelector("[data-hg-size-fact]").textContent, "Size45.3 GB · 2160p HDR10");
});

test("title page: another title in the same panel updates the row, no Status row = Seerr unchanged", async () => {
  const w = await title("/movie/120");
  w.history.pushState({}, "", "/movie/1480613");
  w.hgRequestSizes.titleFact(w.document);
  assert.equal(w.document.querySelectorAll("[data-hg-size-fact]").length, 1);
  assert.equal(w.document.querySelector("[data-hg-size-fact]").textContent, "Size≈ 5.4 GB · in queue");
  const other = await title("/movie/120", { body: facts("Estado") });
  assert.equal(other.document.querySelector("[data-hg-size-fact]"), null);
});

// Layout: the facts column ends before the buttons, text-only values truncate with an ellipsis and keep the full text as a tooltip
test("facts column: marked for the gap before the buttons, text-only values get an ellipsis and the full text as title", async () => {
  const by = (r) => item(r).replace("1 hour ago", '1 hour ago by <a href="/users/1" class="group flex items-center truncate"><span class="truncate">Jane</span></a>');
  const w = await requests({ rows: [by(REQS[0]), ...REQS.slice(1)] });
  const rows = [...w.document.querySelectorAll(".max-w-8xl > div.py-2")];
  rows.forEach((r) => assert.ok(r.querySelector("[data-hg-facts] > .card-field")));
  const col = rows[0].querySelector("[data-hg-facts]");
  const value = (name) => [...col.querySelectorAll(".card-field")].find((f) => f.firstElementChild.textContent === name).children[1];
  assert.ok(value("Profile").hasAttribute("data-hg-ellipsis"));
  assert.equal(value("Profile").getAttribute("title"), "4K");
  assert.equal(value("Requested").hasAttribute("data-hg-ellipsis"), false);
  assert.equal(value("Size").getAttribute("title"), "≈ 5.4 GB · in queue");
  const css = w.document.getElementById("hg-size-style").textContent;
  assert.match(css, /@media \(min-width:1280px\)\{[^}]*\[data-hg-facts\]\{padding-right:1rem !important\}/);
  assert.match(css, /\[data-hg-ellipsis\]\{display:block;/);
  assert.match(css, /@media \(max-width:639px\)\{\[data-hg-facts\]\{width:auto !important\}\}/);
});

test("facts column fix also without size data", async () => {
  const w = await requests({ state: null });
  assert.equal(w.document.querySelectorAll("[data-hg-facts]").length, 5);
});

test("no action buttons (normal user): the empty buttons column is hidden, so the facts use the full width; it comes back with a button", async () => {
  const user = (r) => item(r).replace("<button>Delete Request</button>", "");
  const w = await requests({ rows: [user(REQS[0]), ...REQS.slice(1)] });
  const cols = [...w.document.querySelectorAll(".max-w-8xl > div.py-2")].map((r) => r.firstElementChild.lastElementChild);
  assert.deepEqual(cols.map((c) => c.hasAttribute("data-hg-actions-empty")), [true, false, false, false, false]);
  assert.match(w.document.getElementById("hg-size-style").textContent, /\[data-hg-actions-empty\]\{display:none !important\}/);
  cols[0].innerHTML = "<button>Cancel Request</button>";
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(cols[0].hasAttribute("data-hg-actions-empty"), false);
});

// Episodes, average/ep, GB/h and warnings
const RICH = {
  62: { size: 45327147274, runtime: 178 },
  63: { size: 80e9, runtime: 120, remux: true },
  54: { size: 22865343090, files: 5, of: 6, episodes: { 1: 6 }, runtime: 45 },
  41: { size: 50e9, files: 2, of: 36, episodes: { 1: 10, 2: 12, 3: 14 }, runtime: 60, remux: true },
  57: { queued: 5378662671 },
};
const pills = (w) => [...w.document.querySelectorAll(".max-w-8xl > div.py-2")].map((r) => [...r.querySelectorAll("[data-hg-size] [data-hg-size-warn], [data-hg-size] > [data-hg-size-warn]")].map((p) => p.textContent));
const episodes = (w) => [...w.document.querySelectorAll(".max-w-8xl > div.py-2")].map((r) => { const e = r.querySelector("[data-hg-episodes]"); return e ? e.textContent : null; });

test("series: the episodes of the requested seasons beside the season badges, per season on hover", async () => {
  const hints = [];
  const w = await requests({ state: { ...STATE, sizes: RICH }, before: (win) => { win.hgNightLabel = { hint: (el, text) => hints.push(text) }; } });
  assert.deepEqual(episodes(w), [null, null, "· 6 episodes", "· 36 episodes", null]);
  const e = w.document.querySelectorAll(".max-w-8xl > div.py-2")[3].querySelector("[data-hg-episodes]");
  assert.equal(e.parentElement.firstElementChild.textContent, "Season");
  assert.equal(e.previousElementSibling.className, "hide-scrollbar flex");
  assert.equal(e.nextElementSibling, null);
  assert.ok(hints.includes("Season 1: 10 episodes\nSeason 2: 12 episodes\nSeason 3: 14 episodes"));
});

test("episodes in German, one episode singular", async () => {
  const sizes = { ...RICH, 54: { ...RICH[54], of: 1, files: 1, episodes: { 1: 1 } } };
  const de = await requests({ state: { ...STATE, sizes }, before: (win) => { win.hgNightLabel = { lang: () => "de" }; } });
  assert.deepEqual(episodes(de), [null, null, "· 1 Episode", "· 36 Episoden", null]);
  assert.equal(de.document.querySelectorAll(".max-w-8xl > div.py-2")[3].querySelector("[data-hg-episodes]").getAttribute("title"), "Staffel 1: 10 Episoden\nStaffel 2: 12 Episoden\nStaffel 3: 14 Episoden");
  const en = await requests({ state: { ...STATE, sizes } });
  assert.equal(episodes(en)[2], "· 1 episode");
});

test("Size line: average/ep, GB per hour, warnings for remux and very big", async () => {
  const w = await requests({ state: { ...STATE, sizes: RICH } });
  assert.deepEqual(lines(w), [
    "Size≈ 5.4 GB · in queue",
    "Size45.3 GB · 15.3 GB/h",
    "Size22.9 GB · 5 of 6 episodes · ⌀ 4.6 GB/ep · 6.1 GB/h",
    "Size50.0 GB · 2 of 36 episodes · ⌀ 25.0 GB/ep · 25.0 GB/h⚠️ Remux⚠️ very big",
    "Size80.0 GB · 40.0 GB/h⚠️ Remux⚠️ very big",
  ]);
  const line = w.document.querySelectorAll(".max-w-8xl > div.py-2")[4].querySelector("[data-hg-size]");
  const warn = line.querySelectorAll("[data-hg-size-warn]");
  assert.deepEqual([...warn].map((p) => [p.className, p.getAttribute("data-hg-size-warn")]), [["hg-size-warn", "remux"], ["hg-size-warn", "big"]]);
  assert.equal(line.children[1].textContent, "80.0 GB · 40.0 GB/h");
  assert.match(w.document.getElementById("hg-size-style").textContent, /\.hg-size-warn\{[^}]*flex-shrink:0/);
});

test("very big: over 30 GB/h for films, over 20 GB/h for episodes; German", async () => {
  const sizes = { 62: { size: 30e9, runtime: 60 }, 63: { size: 31e9, runtime: 60 }, 54: { size: 20e9, files: 1, of: 1, runtime: 60 }, 41: { size: 21e9, files: 1, of: 1, runtime: 60 }, 57: { queued: 9e10 } };
  const w = await requests({ state: { ...STATE, sizes } });
  assert.deepEqual(pills(w), [[], [], [], ["⚠️ very big"], ["⚠️ very big"]]);
  const de = await requests({ state: { ...STATE, sizes }, before: (win) => { win.hgNightLabel = { lang: () => "de" }; } });
  assert.deepEqual(pills(de), [[], [], [], ["⚠️ sehr gross"], ["⚠️ sehr gross"]]);
  assert.equal(lines(de)[3], "Grösse21.0 GB · ⌀ 21.0 GB/Ep. · 21.0 GB/h⚠️ sehr gross");
});

test("no runtime: no GB/h and no very big; queued: no GB/h, no warnings", async () => {
  const sizes = { 62: { size: 99e9 }, 54: { size: 9e9, files: 3, of: 3 }, 57: { queued: 9e10, runtime: 10, remux: true } };
  const w = await requests({ state: { ...STATE, sizes } });
  assert.deepEqual(lines(w), ["Size≈ 90.0 GB · in queue", "Size99.0 GB", "Size9.0 GB · ⌀ 3.0 GB/ep", "Size—", "Size—"]);
  assert.deepEqual(pills(w), [[], [], [], [], []]);
});

test("a row whose warnings went away loses its pills", async () => {
  const w = await requests({ state: { ...STATE, sizes: RICH } });
  w.hgRequestSizes.state.sizes = { ...RICH, 63: { size: 1e9, runtime: 120 } };
  w.hgRequestSizes.apply(w.document);
  assert.equal(lines(w)[4], "Size1.0 GB · 500 MB/h");
});

test("title page: GB/h and warnings in the Size row", async () => {
  const media = { "movie/120": { size: 45327147274, quality: "2160p HDR10", runtime: 178, remux: true }, "tv/84773": { size: 122003416606, files: 13, of: 16, runtime: 24, seasons: {} } };
  const m = await title("/movie/120", { media });
  const row = m.document.querySelector("[data-hg-size-fact]");
  assert.equal(row.textContent, "Size45.3 GB · 2160p HDR10 · 15.3 GB/h⚠️ Remux");
  assert.equal(row.lastChild.className, "media-fact-value");
  assert.deepEqual([...row.querySelectorAll("[data-hg-size-warn]")].map((p) => p.getAttribute("data-hg-size-warn")), ["remux"]);
  const t = await title("/tv/84773", { media, before: (win) => { win.hgNightLabel = { lang: () => "de", hint: () => {} }; } });
  assert.equal(t.document.querySelector("[data-hg-size-fact]").textContent, "Grösse122.0 GB · 13 von 16 Episoden · 23.5 GB/h⚠️ sehr gross");
});

test("per file (a film is one file) and per hour sort, titles without the value last", async () => {
  const w = await requests({ state: { ...STATE, sizes: RICH } });
  choose(w, "desc");
  await settle();
  assert.deepEqual(shownOrder(w), ["Title 63", "Title 41", "Title 62", "Title 54", "Title 57"]);
  choose(w, "file-desc");
  await settle();
  assert.deepEqual(shownOrder(w), ["Title 63", "Title 62", "Title 41", "Title 54", "Title 57"]);
  choose(w, "file-asc");
  await settle();
  assert.deepEqual(shownOrder(w), ["Title 54", "Title 41", "Title 62", "Title 63", "Title 57"]);
  choose(w, "hour-desc");
  await settle();
  assert.deepEqual(shownOrder(w), ["Title 63", "Title 41", "Title 62", "Title 54", "Title 57"]);
  choose(w, "hour-asc");
  await settle();
  assert.deepEqual(shownOrder(w), ["Title 54", "Title 62", "Title 41", "Title 63", "Title 57"]);
  assert.equal(w.localStorage.getItem("hg-size-sort"), "hour-asc");
});

test("the per-file and per-hour choices in German, and a stored one is kept", async () => {
  const de = await requests({ storage: { "hg-size-sort": "file-desc" }, before: (win) => { win.hgNightLabel = { lang: () => "de" }; } });
  const sel = de.document.querySelector("[data-hg-size-sort] select");
  assert.deepEqual([...sel.options].map((o) => o.textContent), ["Aus", "Grösste gesamt", "Kleinste gesamt", "Grösste pro Datei", "Kleinste pro Datei", "Grösste pro Stunde", "Kleinste pro Stunde"]);
  assert.equal(sel.value, "file-desc");
});
