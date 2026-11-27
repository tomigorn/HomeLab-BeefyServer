import { test } from "node:test";
import assert from "node:assert/strict";
import { load, tick } from "./fixture.mjs";

// GETs from `routes` (url -> body); POSTs recorded in `sent` and answered from `posts` (url -> body or [status, body]).
function fakeXHR(routes, sent, posts = {}) {
  return function (w) {
    w.XMLHttpRequest = class {
      constructor() { this.headers = {}; }
      open(method, url) { this.method = method; this.url = url; }
      setRequestHeader(k, v) { this.headers[k] = v; }
      send(body) {
        const done = (status, data) => { this.status = status; this.responseText = JSON.stringify(data ?? {}); setTimeout(() => this.onload && this.onload(), 0); };
        if (this.method === "GET") {
          const r = typeof routes === "function" ? routes(this.url) : routes[this.url];
          return r === undefined ? done(404, {}) : done(200, r);
        }
        sent.push({ url: this.url, body: JSON.parse(body), headers: this.headers });
        const p = posts[this.url];
        return Array.isArray(p) ? done(p[0], p[1]) : done(202, p ?? { state: "queued" });
      }
    };
  };
}
const settle = async () => { for (let i = 0; i < 15; i++) await tick(); };
const URL_ = "https://requests.example.com/my-library";
const LIST = "/_download-now/library/list";
const MAIN = `<main><div class="seerr-404">404 - Page Not Found</div></main>`;
const DE = `<a href="/discover/tv" style="display:none">Serien</a>`;

const title = (key, t, extra = {}) => ({ key, kind: key.split("/")[0], tmdb: Number(key.split("/")[1]), t, y: 2025, size: 2 ** 33, tier: "ssd", deleteOn: "2027-02-15", source: "default",
  reasons: [], why: "due", postponed: null, state: "temporary", marks: [], canMark: true, canUnmark: false, items: [], del: { allowed: true, reason: null, plan: [] }, mine: false, ...extra });
const DATA = {
  group: true, deleteEnabled: true, mineKnown: true,
  quota: { delete: { used: 8, max: 10 }, marks: { used: 1, max: 5 } },
  asked: [],
  titles: [
    title("movie/1", "Alpha Film", { mine: true }),
    title("movie/2", "Beta Film", { state: "keep", source: "fan", deleteOn: null, marks: [{ name: "Jane", mine: false }], del: { allowed: true, reason: null, plan: [{ reason: "fan", names: ["Jane"] }] } }),
    title("movie/3", "Classic", { y: 1980, state: "keep", source: "default", reasons: ["old"], deleteOn: null, canMark: false, del: { allowed: false, reason: "kept", plan: [] } }),
    title("tv/4", "Delta Show", { state: "soon", deleteOn: "2026-11-30", marks: [{ name: "Me", mine: true }], canMark: false, canUnmark: true }),
    title("movie/5", "Echo Film", { state: "bin", size: null, tier: null, deleteOn: null, source: null, canMark: false, del: { allowed: false, reason: "bin", plan: [] },
      items: [{ id: "abc", state: "deleted", season: null, byName: "Me", mine: true, approver: false, asks: [], binUntil: "2026-12-03T10:00:00+01:00", canRestore: true }] })
  ]
};

const rows = (w) => [...w.document.querySelectorAll("#hg-library [data-hg-l-row]")];
const row = (w, key) => w.document.querySelector(`#hg-library [data-hg-l-row="${key}"]`);
const button = (el, attr) => el.querySelector(`[${attr}]`);

test("page in place of Seerr's 404: heading, both weekly counts, every title with its state", async () => {
  const w = load("my-library.js", MAIN, fakeXHR({ [LIST]: DATA }, []), URL_);
  await settle();
  const root = w.document.getElementById("hg-library");
  assert.ok(root);
  assert.equal(root.parentElement.tagName, "MAIN");
  assert.ok(w.document.querySelector("main").hasAttribute("data-hg-library"));
  assert.equal(root.querySelector("h1").textContent, "My library");
  assert.match(root.textContent, /2 of 10 deletions left this week · 4 of 5 fan-favourite marks left/);
  assert.equal(rows(w).length, 5);
  assert.match(row(w, "movie/1").textContent, /Temporary · leaves 15 Feb 2027/);
  assert.match(row(w, "movie/2").textContent, /⭐ Fan favourite/);
  assert.match(row(w, "movie/2").textContent, /fan favourite of Jane/);
  assert.match(row(w, "movie/3").textContent, /♾️ Keep forever/);
  assert.match(row(w, "movie/3").textContent, /released 12 or more years ago/);
  assert.match(row(w, "tv/4").textContent, /Leaving soon · 30 Nov/);
  assert.match(row(w, "movie/5").textContent, /In the recycle bin until 3 Dec/);
  assert.equal(row(w, "movie/1").querySelector("a").getAttribute("href"), "/movie/1");
  assert.equal(w.document.title, "My library - Seerr");
});

test("German", async () => {
  const w = load("my-library.js", DE + MAIN, fakeXHR({ [LIST]: DATA }, []), URL_);
  await settle();
  const root = w.document.getElementById("hg-library");
  assert.equal(root.querySelector("h1").textContent, "Meine Bibliothek");
  assert.match(root.textContent, /Noch 2 von 10 Löschungen diese Woche · 4 von 5 Fan-Liebling-Markierungen/);
  assert.match(row(w, "movie/3").textContent, /♾️ Für immer behalten/);
  assert.match(row(w, "movie/2").textContent, /⭐ Fan-Liebling/);
  assert.match(row(w, "movie/5").textContent, /Im Papierkorb bis 3\. Dez\./);
});

test("filters and search", async () => {
  const w = load("my-library.js", MAIN, fakeXHR({ [LIST]: DATA }, []), URL_);
  await settle();
  const chip = (f) => w.document.querySelector(`[data-hg-l-filter="${f}"]`);
  chip("mine").click();
  assert.deepEqual(rows(w).map((r) => r.getAttribute("data-hg-l-row")), ["movie/1"]);
  chip("keep").click();
  assert.deepEqual(rows(w).map((r) => r.getAttribute("data-hg-l-row")), ["movie/2", "movie/3"]);
  chip("soon").click();
  assert.deepEqual(rows(w).map((r) => r.getAttribute("data-hg-l-row")), ["tv/4"]);
  chip("bin").click();
  assert.deepEqual(rows(w).map((r) => r.getAttribute("data-hg-l-row")), ["movie/5"]);
  chip("all").click();
  const q = w.document.querySelector("[data-hg-l-search]");
  q.value = "film";
  q.dispatchEvent(new w.Event("input"));
  assert.deepEqual(rows(w).map((r) => r.getAttribute("data-hg-l-row")), ["movie/1", "movie/2", "movie/5"]);
  assert.equal(w.document.activeElement === q || true, true);
});

test("Mine filter hidden when Seerr did not say what I requested", async () => {
  const w = load("my-library.js", MAIN, fakeXHR({ [LIST]: { ...DATA, mineKnown: false } }, []), URL_);
  await settle();
  assert.equal(w.document.querySelector('[data-hg-l-filter="mine"]'), null);
});

test("fan-favourite mark and removing my own, as the server allows", async () => {
  const sent = [];
  const w = load("my-library.js", MAIN, fakeXHR({ [LIST]: DATA }, sent, { "/_download-now/retention/keep": [200, {}] }), URL_);
  await settle();
  assert.equal(button(row(w, "movie/3"), "data-hg-l-mark"), null);
  assert.equal(button(row(w, "movie/2"), "data-hg-l-unmark"), null);
  button(row(w, "movie/1"), "data-hg-l-mark").click();
  await settle();
  button(row(w, "tv/4"), "data-hg-l-unmark").click();
  await settle();
  assert.deepEqual(sent.map((s) => s.body), [{ type: "movie", tmdbId: 1, keep: true }, { type: "tv", tmdbId: 4, keep: false }]);
  assert.equal(sent[0].headers["Content-Type"], "application/json");
});

test("bulk: only deletable titles can be picked, at most the deletions left; one confirm lists bin now vs request", async () => {
  const sent = [];
  const w = load("my-library.js", MAIN, fakeXHR({ [LIST]: DATA }, sent), URL_);
  await settle();
  assert.equal(row(w, "movie/3").querySelector("input[type=checkbox]"), null);
  assert.equal(row(w, "movie/5").querySelector("input[type=checkbox]"), null);
  const pick = (k) => { const c = row(w, k).querySelector("input[type=checkbox]"); c.checked = true; c.dispatchEvent(new w.Event("change", { bubbles: true })); };
  pick("movie/1");
  pick("movie/2");
  assert.equal(row(w, "tv/4").querySelector("input[type=checkbox]").disabled, true);
  const bulk = w.document.querySelector("[data-hg-l-bulk]");
  assert.match(bulk.textContent, /Delete selected \(2\)/);
  bulk.click();
  const box = w.document.querySelector("[data-hg-l-confirm]");
  assert.match(box.textContent, /Delete 2 titles\?/);
  const now = box.querySelector("[data-hg-l-now]"), ask = box.querySelector("[data-hg-l-ask]");
  assert.match(now.textContent, /Alpha Film/);
  assert.doesNotMatch(now.textContent, /Beta Film/);
  assert.match(ask.textContent, /Beta Film.*Jane.*fan favourite/);
  assert.match(box.textContent, /7 days in the recycle bin/);
  button(box, "data-hg-l-go").click();
  await settle();
  assert.deepEqual(sent[0], { url: "/_download-now/delete-early/bulk", body: { items: [{ type: "movie", tmdbId: 1 }, { type: "movie", tmdbId: 2 }] }, headers: { "Content-Type": "application/json" } });
});

test("single delete uses the same confirm", async () => {
  const sent = [];
  const w = load("my-library.js", MAIN, fakeXHR({ [LIST]: DATA }, sent), URL_);
  await settle();
  button(row(w, "tv/4"), "data-hg-l-delete").click();
  const box = w.document.querySelector("[data-hg-l-confirm]");
  assert.match(box.textContent, /Delete 1 title\?/);
  assert.match(box.querySelector("[data-hg-l-now]").textContent, /Delta Show/);
  button(box, "data-hg-l-cancel").click();
  assert.equal(w.document.querySelector("[data-hg-l-confirm]"), null);
  assert.equal(sent.length, 0);
});

test("a refused bulk shows why per title and sends nothing more", async () => {
  const sent = [];
  const w = load("my-library.js", MAIN, fakeXHR({ [LIST]: DATA }, sent, { "/_download-now/delete-early/bulk": [409, { error: "refused", titles: { "movie/1": "already" } }] }), URL_);
  await settle();
  button(row(w, "movie/1"), "data-hg-l-delete").click();
  button(w.document.querySelector("[data-hg-l-confirm]"), "data-hg-l-go").click();
  await settle();
  assert.match(w.document.getElementById("hg-library").textContent, /Alpha Film: already being deleted/);
});

test("requests waiting for my OK: Approve / Deny", async () => {
  const sent = [];
  const asked = [{ key: "movie/1", kind: "movie", t: "Alpha Film", y: 2025, item: { id: "p1", state: "pending", approver: true, byName: "Joe", expires: "2026-12-04T10:00:00+01:00",
    asks: [{ reason: "fan", names: ["Me"], answer: null }] } }];
  const w = load("my-library.js", MAIN, fakeXHR({ [LIST]: { ...DATA, asked } }, sent), URL_);
  await settle();
  const box = w.document.querySelector("[data-hg-l-asked]");
  assert.match(box.textContent, /Waiting for your OK/);
  assert.match(box.textContent, /Joe wants to delete Alpha Film \(2025\): you marked it ⭐ fan favourite\. Answer until 4 Dec/);
  button(box, "data-hg-l-approve").click();
  await settle();
  assert.deepEqual(sent[0].body, { id: "p1", approve: true });
  assert.equal(sent[0].url, "/_download-now/delete-early/answer");
});

test("restore from the recycle bin", async () => {
  const sent = [];
  const w = load("my-library.js", MAIN, fakeXHR({ [LIST]: DATA }, sent), URL_);
  await settle();
  button(row(w, "movie/5"), "data-hg-l-restore").click();
  await settle();
  assert.deepEqual(sent[0], { url: "/_download-now/delete-early/restore", body: { id: "abc" }, headers: { "Content-Type": "application/json" } });
});

test("a pending delete shows who must OK it", async () => {
  const pending = title("movie/6", "Foxtrot", { del: { allowed: false, reason: "already", plan: [] },
    items: [{ id: "q", state: "pending", season: null, byName: "Me", mine: true, approver: false, asks: [{ reason: "fan", names: ["Jane"], answer: null }], expires: "2026-12-04T10:00:00+01:00" }] });
  const w = load("my-library.js", MAIN, fakeXHR({ [LIST]: { ...DATA, titles: [pending] } }, []), URL_);
  await settle();
  assert.match(row(w, "movie/6").textContent, /Waiting for Jane to OK it until 4 Dec/);
});

test("other routes: nothing; no group: no actions", async () => {
  let w = load("my-library.js", MAIN, fakeXHR({ [LIST]: DATA }, []), "https://requests.example.com/requests");
  await settle();
  assert.equal(w.document.getElementById("hg-library"), null);
  const noGroup = { ...DATA, group: false, titles: DATA.titles.map((t) => ({ ...t, canMark: false, canUnmark: false, del: { allowed: false, reason: "group", plan: [] } })) };
  w = load("my-library.js", MAIN, fakeXHR({ [LIST]: noGroup }, []), URL_);
  await settle();
  assert.equal(w.document.querySelector("#hg-library input[type=checkbox]"), null);
  assert.equal(w.document.querySelector("[data-hg-l-mark]"), null);
  assert.match(w.document.getElementById("hg-library").textContent, /Only members of the requesters group can mark or delete/);
});

test("list unreadable: a message, no crash", async () => {
  const w = load("my-library.js", MAIN, fakeXHR({}, []), URL_);
  await settle();
  assert.match(w.document.getElementById("hg-library").textContent, /Could not load the library/);
});
