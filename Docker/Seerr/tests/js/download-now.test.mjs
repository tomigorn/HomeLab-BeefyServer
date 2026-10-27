import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, tick } from "./fixture.mjs";

// Seerr's ManageSlideOver (src/components/ManageSlideOver, Common/SlideOver), reduced to what the script looks at.
const slideOver = (title = "Manage Movie", open = "Open in Radarr") => `
<div class="fixed inset-0 z-50 overflow-hidden bg-gray-800/70"><div class="absolute inset-0 overflow-hidden"><section class="absolute inset-y-0 right-0 flex max-w-full"><div>
<div class="slideover relative h-full w-screen max-w-md p-2 sm:p-3"><div class="flex h-full flex-col rounded-lg">
  <header class="space-y-1 border-b border-gray-700 px-4 py-4"><div class="flex items-center justify-between space-x-3"><h2 class="text-overseerr text-2xl font-bold leading-7">${title}</h2></div><div><p>Some Film</p></div></header>
  <div class="hide-scrollbar flex flex-1 flex-col overflow-y-auto"><div class="flex-1 px-4 py-6 text-white"><div class="space-y-6">
    <div><h3 class="mb-2 text-xl font-bold">Media</h3><div class="space-y-2">
      ${open ? `<a href="https://radarr.example.com/movie/some-film" target="_blank" rel="noreferrer" class="block"><button class="button-md ghost w-full"><svg></svg><span>${open}</span></button></a>` : ""}
      <div><button>Remove from Radarr</button></div>
    </div></div>
  </div></div></div>
</div></div></div></section></div></div>`;

const ME = { admin: { id: 1, permissions: 2 }, manager: { id: 3, permissions: 16 | 32 }, requester: { id: 5, permissions: 8352 } };

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
const routes = (me, state, extra = {}) => ({ "/api/v1/auth/me": me, [API]: { state, released: state === "started" ? ["abc"] : [], app: "radarr" }, ...extra });
const opened = [];
afterEach(() => { while (opened.length) opened.pop().close(); });
function page(body, r, posts = {}, calls = [], path = "/movie/673?manage=1") {
  const w = load("download-now.js", body, fakeXHR(r, posts, calls), "https://requests.example.com" + path);
  opened.push(w);
  return w;
}
const box = (w) => w.document.querySelector("#hg-download-now");

test("admin: a title waiting for the night gets the button right after Open in Radarr", async () => {
  const w = page(slideOver(), routes(ME.admin, "started"));
  await settle();
  const b = box(w);
  assert.ok(b);
  assert.equal(b.previousElementSibling.tagName, "A");
  assert.match(b.querySelector("button").textContent, /⚡ Download now — at daytime power price/);
  assert.equal(b.querySelector("button").disabled, false);
  assert.match(b.textContent, /Waiting for the night 🌙/);
});

test("manage-requests without admin: no Open in link, the button goes at the top of the panel", async () => {
  const w = page(slideOver("Manage Movie", ""), routes(ME.manager, "searching"));
  await settle();
  const b = box(w);
  assert.ok(b);
  assert.equal(b.parentElement.className, "space-y-6");
  assert.equal(b.parentElement.firstElementChild, b);
  assert.match(b.textContent, /Nothing grabbed yet/);
});

test("requesters see nothing and the backend is never asked", async () => {
  const calls = [];
  const w = page(slideOver(), routes(ME.requester, "started"), {}, calls);
  await settle();
  assert.equal(box(w), null);
  assert.ok(!calls.some((c) => c.url.startsWith("/_download-now/")));
});

test("already downloading: a note instead of the button; downloaded or not in Radarr: nothing", async () => {
  let w = page(slideOver(), routes(ME.admin, "downloading"));
  await settle();
  assert.equal(box(w).querySelector("button"), null);
  assert.match(box(w).textContent, /Already downloading/);
  for (const s of ["available", "not-found"]) {
    w = page(slideOver(), routes(ME.admin, s));
    await settle();
    assert.equal(box(w), null, s);
  }
});

test("backend down or Seerr changed: no box, Seerr untouched", async () => {
  const w = page(slideOver(), { "/api/v1/auth/me": ME.admin });
  await settle();
  assert.equal(box(w), null);
  const w2 = page(slideOver("Edit Request"), routes(ME.admin, "started"));
  await settle();
  assert.equal(box(w2), null);
});

test("press: POSTs JSON for the title, shows Started and drops the night label", async () => {
  const calls = [];
  const w = page(slideOver(), routes(ME.admin, "started"), { "/_download-now/api": { state: "started", released: ["abc"], moved: 1 } }, calls);
  await settle();
  box(w).querySelector("button").click();
  await settle();
  const post = calls.find((c) => c.method === "POST");
  assert.deepEqual(JSON.parse(post.body), { type: "movie", tmdbId: 673 });
  assert.equal(post.headers["Content-Type"], "application/json");
  assert.match(box(w).textContent, /✅ Started — downloading now at daytime power price/);
  assert.equal(box(w).querySelector("button"), null);
  assert.deepEqual(w.hgNightLabel.released, ["abc"]);
});

test("press without a grab: Searching feedback", async () => {
  const w = page(slideOver(), routes(ME.admin, "searching"), { "/_download-now/api": { state: "searching", released: [] } });
  await settle();
  box(w).querySelector("button").click();
  await settle();
  assert.match(box(w).textContent, /Searching now/);
});

test("press fails: error text, button usable again", async () => {
  const w = page(slideOver(), routes(ME.admin, "started"), { "/_download-now/api": { status: 502, body: { state: "error" } } });
  await settle();
  box(w).querySelector("button").click();
  await settle();
  assert.match(box(w).textContent, /❌ Failed/);
  assert.equal(box(w).querySelector("button").disabled, false);
});

test("German UI: German texts; series pages use type tv", async () => {
  const calls = [];
  const r = { "/api/v1/auth/me": ME.admin, "/_download-now/api?type=tv&tmdb=76479": { state: "started", released: [] } };
  const w = page(slideOver("Serie verwalten", "In Sonarr öffnen"), r, {}, calls, "/tv/76479?manage=1");
  await settle();
  assert.match(box(w).querySelector("button").textContent, /⚡ Jetzt herunterladen — zum Tagstrompreis/);
  assert.match(box(w).textContent, /Wartet auf die Nacht 🌙/);
});

test("a panel opened later gets the box once", async () => {
  const calls = [];
  const w = page("<main></main>", routes(ME.admin, "started"), {}, calls);
  w.document.body.insertAdjacentHTML("beforeend", slideOver());
  await settle();
  w.document.body.insertAdjacentHTML("beforeend", "<p>re-render</p>");
  await settle();
  assert.equal(w.document.querySelectorAll("#hg-download-now").length, 1);
  assert.equal(calls.filter((c) => c.url === API).length, 1);
});
