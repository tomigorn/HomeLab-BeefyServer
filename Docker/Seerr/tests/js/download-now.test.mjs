import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, tick } from "./fixture.mjs";

// Seerr's MovieDetails / TvDetails header (src/components/MovieDetails, TvDetails), reduced to what the script looks at.
const titlePage = (status = "Requested", action = "Watch Trailer") => `
<main class="relative top-16 z-0"><div class="media-page"><div class="media-header">
  <div class="media-poster"><img alt=""></div>
  <div class="media-title"><div class="media-status"><span class="px-2 inline-flex rounded-full"><div class="relative z-20 flex items-center"><span>${status}</span></div></span></div>
    <h1 data-testid="media-title">Some Film <span class="media-year">(2026)</span></h1></div>
  <div class="media-actions">
    <button class="inline-flex items-center justify-center border font-medium rounded-md px-4 py-2 text-sm button-md z-40 mr-2"><span class="flex max-w-full items-center"><svg></svg></span></button>
    <div class="z-20"><div class="relative z-10 inline-flex"><a class="relative z-10 inline-flex h-full items-center button-md rounded-md" href="https://www.youtube.com/watch?v=x" target="_blank"><svg></svg><span>${action}</span></a></div></div>
  </div>
</div></div></main>`;

const ME = { admin: { id: 1, permissions: 2 }, manager: { id: 3, permissions: 16 | 32 }, requester: { id: 7, permissions: 8352 } };

// GETs answer from `routes`, POSTs from `posts`; records every call.
function fakeXHR(routes, posts, calls) {
  return function (w) {
    w.XMLHttpRequest = class {
      constructor() { this.headers = {}; }
      open(method, url) { this.method = method; this.url = url; }
      setRequestHeader(k, v) { this.headers[k] = v; }
      send(body) {
        calls.push({ method: this.method, url: this.url, body, headers: this.headers });
        const r = this.method === "GET" ? routes[this.url] : posts[this.url];
        this.status = r ? r.status ?? 200 : 404;
        this.responseText = JSON.stringify(r ? r.body ?? r : {});
        setTimeout(() => this.onload && this.onload(), 0);
      }
    };
    w.hgNightLabel = { released: [], release(ids) { this.released.push(...ids); } };
  };
}
const settle = async () => { for (let i = 0; i < 8; i++) { await new Promise((r) => setTimeout(r, 30)); await tick(); } };
const API = "/_download-now/api?type=movie&tmdb=673";
const FORBIDDEN = { status: 403, body: { state: "error", error: "forbidden" } };
const routes = (me, state, extra = {}) => ({ "/api/v1/auth/me": me, [API]: state === 403 ? FORBIDDEN : { state, released: state === "started" ? ["abc"] : [], app: "radarr" }, ...extra });
const opened = [];
afterEach(() => { while (opened.length) opened.pop().close(); });
function page(body, r, posts = {}, calls = [], path = "/movie/673") {
  const w = load("download-now.js", body, fakeXHR(r, posts, calls), "https://requests.example.com" + path);
  opened.push(w);
  return w;
}
const box = (w) => w.document.querySelector("#hg-download-now");

test("admin: a title waiting for the night gets the button first in the title's action row, Seerr button look", async () => {
  const w = page(titlePage(), routes(ME.admin, "started"));
  await settle();
  const b = box(w);
  assert.ok(b);
  assert.equal(b.parentElement.className, "media-actions");
  assert.equal(b.parentElement.firstElementChild, b);
  assert.equal(b.tagName, "BUTTON");
  assert.ok(b.classList.contains("button-md"));
  assert.match(b.textContent, /⚡ Download now — at daytime power price/);
  assert.equal(b.disabled, false);
  assert.match(b.title, /Waiting for the night 🌙/);
});

test("requester: own title (backend allows) gets the button", async () => {
  const w = page(titlePage(), routes(ME.requester, "searching"));
  await settle();
  assert.ok(box(w));
  assert.match(box(w).title, /Nothing grabbed yet/);
});

test("requester: someone else's title (backend 403) gets nothing", async () => {
  const calls = [];
  const w = page(titlePage(), routes(ME.requester, 403), {}, calls);
  await settle();
  assert.equal(box(w), null);
  assert.ok(calls.some((c) => c.url === API));
});

test("not logged in: the backend is never asked", async () => {
  const calls = [];
  const w = page(titlePage(), { "/api/v1/auth/me": { status: 403, body: {} }, [API]: { state: "started" } }, {}, calls);
  await settle();
  assert.equal(box(w), null);
  assert.ok(!calls.some((c) => c.url.startsWith("/_download-now/")));
});

test("already downloading, downloaded or not in Radarr: no button", async () => {
  for (const s of ["downloading", "available", "not-found"]) {
    const w = page(titlePage(), routes(ME.admin, s));
    await settle();
    assert.equal(box(w), null, s);
  }
});

test("backend down, other pages or no action row: no button, Seerr untouched", async () => {
  const w = page(titlePage(), { "/api/v1/auth/me": ME.admin });
  await settle();
  assert.equal(box(w), null);
  const calls = [];
  const w2 = page(titlePage(), routes(ME.admin, "started"), {}, calls, "/movie/673/cast");
  await settle();
  assert.equal(box(w2), null);
  const w3 = page("<main><div class='media-header'></div></main>", routes(ME.admin, "started"));
  await settle();
  assert.equal(box(w3), null);
});

test("press: POSTs JSON for the title, shows Started in place of the button and drops the night label", async () => {
  const calls = [];
  const w = page(titlePage(), routes(ME.admin, "started"), { "/_download-now/api": { state: "started", released: ["abc"], moved: 1 } }, calls);
  await settle();
  box(w).click();
  await settle();
  const post = calls.find((c) => c.method === "POST");
  assert.deepEqual(JSON.parse(post.body), { type: "movie", tmdbId: 673 });
  assert.equal(post.headers["Content-Type"], "application/json");
  assert.match(box(w).textContent, /✅ Started — downloading now at daytime power price/);
  assert.equal(box(w).tagName, "SPAN");
  assert.equal(box(w).parentElement.className, "media-actions");
  assert.deepEqual(w.hgNightLabel.released, ["abc"]);
});

test("press without a grab: Searching feedback", async () => {
  const w = page(titlePage(), routes(ME.requester, "searching"), { "/_download-now/api": { state: "searching", released: [] } });
  await settle();
  box(w).click();
  await settle();
  assert.match(box(w).textContent, /Searching now/);
});

test("press fails: error text, button usable again", async () => {
  const w = page(titlePage(), routes(ME.admin, "started"), { "/_download-now/api": { status: 502, body: { state: "error" } } });
  await settle();
  box(w).click();
  await settle();
  assert.equal(box(w).tagName, "BUTTON");
  assert.equal(box(w).disabled, false);
  assert.match(box(w).title, /❌ Failed/);
  assert.match(box(w).textContent, /❌ Failed/);
});

test("German UI: German texts; series pages use type tv", async () => {
  const r = { "/api/v1/auth/me": ME.admin, "/_download-now/api?type=tv&tmdb=76479": { state: "started", released: [] } };
  const w = page(titlePage("Angefragt", "Trailer ansehen"), r, {}, [], "/tv/76479");
  await settle();
  assert.match(box(w).textContent, /⚡ Jetzt herunterladen — zum Tagstrompreis/);
  assert.match(box(w).title, /Wartet auf die Nacht 🌙/);
});

test("German from the user's Seerr language too", async () => {
  const w = page(titlePage(), routes({ ...ME.admin, settings: { locale: "de" } }, "started"));
  await settle();
  assert.match(box(w).textContent, /Jetzt herunterladen/);
});

test("a header rendered later gets the button once; a re-render that drops it puts it back without asking again", async () => {
  const calls = [];
  const w = page("<div id='root'></div>", routes(ME.admin, "started"), {}, calls);
  w.document.getElementById("root").innerHTML = titlePage();
  await settle();
  w.document.body.insertAdjacentHTML("beforeend", "<p>re-render</p>");
  await settle();
  assert.equal(w.document.querySelectorAll("#hg-download-now").length, 1);
  box(w).remove();
  w.document.body.insertAdjacentHTML("beforeend", "<p>re-render</p>");
  await settle();
  assert.equal(w.document.querySelectorAll("#hg-download-now").length, 1);
  assert.equal(calls.filter((c) => c.url === API).length, 1);
});

test("moving to another title in the app: the old button goes, the new title is asked", async () => {
  const calls = [];
  const r = routes(ME.admin, "started", { "/_download-now/api?type=movie&tmdb=99": { status: 403, body: {} } });
  const w = page(titlePage(), r, {}, calls);
  await settle();
  assert.ok(box(w));
  w.history.pushState({}, "", "/movie/99");
  w.document.body.insertAdjacentHTML("beforeend", "<p>route change</p>");
  await settle();
  assert.equal(box(w), null);
  assert.ok(calls.some((c) => c.url === "/_download-now/api?type=movie&tmdb=99"));
});
