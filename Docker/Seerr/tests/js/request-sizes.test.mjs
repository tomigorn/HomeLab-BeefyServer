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
  assert.deepEqual(lines(w), ["Size≈ 5.4 GB · in queue", "Size45.3 GB", "Size22.9 GB · 5 of 6 episodes", "Size—", "Size41.2 GB"]);
  const line = w.document.querySelectorAll(".max-w-8xl > div.py-2")[1].querySelector("[data-hg-size]");
  assert.equal(line.className, "card-field");
  assert.equal(line.previousElementSibling.textContent, "Profile4K");
  assert.equal(line.nextElementSibling, null);
  assert.equal(line.lastChild.className, "flex truncate text-sm text-gray-300");
  assert.ok(line.parentElement.parentElement.parentElement.hasAttribute("data-hg-size-tall"));
});

test("German: Grösse, Swiss number format, episodes and queue in German", async () => {
  const w = await requests({ before: (win) => { win.hgNightLabel = { lang: () => "de" }; } });
  assert.deepEqual(lines(w), ["Grösse≈ 5.4 GB · in der Warteschlange", "Grösse45.3 GB", "Grösse22.9 GB · 5 von 6 Episoden", "Grösse—", "Grösse41.2 GB"]);
  assert.deepEqual([...w.document.querySelectorAll("[data-hg-size-sort] option")].map((o) => o.textContent), ["Aus", "Grösste", "Kleinste"]);
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
  assert.deepEqual([...box.querySelectorAll("option")].map((o) => [o.value, o.textContent]), [["", "Off"], ["desc", "Largest"], ["asc", "Smallest"]]);
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
  assert.equal(row.querySelector("[data-hg-size]").textContent, "Size22.9 GB · 5 of 6 episodes");
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
  assert.deepEqual(lines(w), ["Size≈ 5.4 GB · in queue", "Size45.3 GB", "Size22.9 GB · 5 of 6 episodes", "Size—", "Size41.2 GB"]);
});

test("other pages with a sort select (Issues) are left alone", async () => {
  const w = load("request-sizes.js", `${SIDEBAR}<div class="max-w-8xl"><div class="mb-4"><div><div>${sel("sort", ["added"], "added")}</div></div></div></div>`, fakeXHR({}, []), "https://requests.example.com/issues");
  open.push(w);
  await settle();
  assert.equal(w.document.querySelector("[data-hg-size-sort]"), null);
});
