import { test } from "node:test";
import assert from "node:assert/strict";
import { load, tick } from "./fixture.mjs";

// GETs answered from `routes` (a function of the call count allows a changing answer); POSTs recorded and answered from `posts`.
function fakeXHR(routes, sent, posts = {}) {
  return function (w) {
    w.XMLHttpRequest = class {
      constructor() { this.headers = {}; this.listeners = {}; }
      open(method, url) { this.method = method; this.url = url; }
      setRequestHeader(k, v) { this.headers[k] = v; }
      addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
      send(body) {
        const done = (status, data) => {
          this.status = status;
          this.responseText = JSON.stringify(data ?? {});
          setTimeout(() => { this.onload && this.onload(); (this.listeners.load || []).forEach((f) => f()); }, 0);
        };
        if (this.method === "GET") {
          let r = routes[this.url];
          if (typeof r === "function") r = r();
          if (r === undefined) return done(404, {});
          return Array.isArray(r) ? done(r[0], r[1]) : done(200, r);
        }
        sent.push({ method: this.method, url: this.url, body, headers: this.headers });
        const p = posts[this.url];
        return Array.isArray(p) ? done(p[0], p[1]) : done(202, p ?? { state: "queued" });
      }
    };
  };
}
const settle = async () => { for (let i = 0; i < 12; i++) await tick(); };
const S = (q) => "/_download-now/delete-early/state?" + q;
const FREE = { key: "movie/1", allowed: true, reason: null, quota: { used: 2, max: 10 }, seasons: [], queued: [], items: [] };
const FACTS = `<div class="media-page"><div class="media-overview-right"><div class="media-facts">
  <div class="media-fact"><span>Status</span><span class="media-fact-value">Released</span></div>
  <div class="media-fact"><span>Runtime</span><span class="media-fact-value">2h</span></div></div></div></div>`;
const row = (w) => w.document.querySelector("[data-hg-delete-fact]");
const btn = (w, attr) => row(w).querySelector(`[${attr}]`);

test("film: a Delete early button that asks before it deletes", async () => {
  const sent = [];
  const w = load("retention.js", FACTS, fakeXHR({ [S("type=movie&tmdb=1")]: FREE }, sent));
  await settle();
  assert.ok(row(w));
  assert.match(row(w).textContent, /Delete early/);
  btn(w, "data-hg-delete-start").click();
  await settle();
  assert.equal(sent.length, 0, "nothing sent before the confirm");
  assert.match(row(w).textContent, /Delete this film now\?/);
  assert.match(row(w).textContent, /7 days in the recycle bin/);
  assert.match(row(w).textContent, /8 of 10 deletions left this week/);
  assert.match(row(w).textContent, /Goes to the recycle bin at once/);
  btn(w, "data-hg-delete-cancel").click();
  await settle();
  assert.equal(btn(w, "data-hg-delete-confirm"), null);
  btn(w, "data-hg-delete-start").click();
  await settle();
  btn(w, "data-hg-delete-confirm").click();
  await settle();
  assert.deepEqual(sent.map((s) => [s.url, JSON.parse(s.body)]), [["/_download-now/delete-early/delete", { type: "movie", tmdbId: 1, season: null }]]);
  assert.equal(sent[0].headers["Content-Type"], "application/json");
});

test("confirm names who must OK it first, from the server's plan", async () => {
  const plan = [{ reason: "fan", names: ["Jane Doe"] }, { reason: "watching", names: ["Ann"] }];
  const w = load("retention.js", FACTS, fakeXHR({ [S("type=movie&tmdb=1")]: { ...FREE, plan } }, []));
  await settle();
  btn(w, "data-hg-delete-start").click();
  await settle();
  assert.match(row(w).textContent, /Becomes a request first: Jane Doe \(fan favourite\), Ann \(watching it\)/);
  assert.doesNotMatch(row(w).textContent, /recycle bin at once/);
});

test("series: whole series or one season", async () => {
  const sent = [];
  const w = load("retention.js", FACTS, fakeXHR({ [S("type=tv&tmdb=1")]: { ...FREE, key: "tv/1", seasons: [1, 2] } }, sent), "https://requests.example.com/tv/1");
  await settle();
  const sel = row(w).querySelector("select[data-hg-delete-scope]");
  assert.deepEqual([...sel.options].map((o) => o.textContent), ["Whole series", "Season 1", "Season 2"]);
  sel.value = "2";
  btn(w, "data-hg-delete-start").click();
  await settle();
  assert.match(row(w).textContent, /Delete season 2 now\?/);
  btn(w, "data-hg-delete-confirm").click();
  await settle();
  assert.deepEqual(JSON.parse(sent[0].body), { type: "tv", tmdbId: 1, season: 2 });
});

test("no row without the right or for the test film", async () => {
  for (const reason of ["group", "permission", "protected", "library", "kept"]) {
    const w = load("retention.js", FACTS, fakeXHR({ [S("type=movie&tmdb=1")]: { ...FREE, allowed: false, reason } }, []));
    await settle();
    assert.equal(row(w), null, reason);
  }
});

test("asked person: an Approve / Deny box", async () => {
  const sent = [];
  const st = { ...FREE, items: [{ id: "a1", state: "pending", season: null, byName: "Joe Bloggs", mine: false, approver: true, asks: [{ reason: "keep", names: ["Jane Doe"], answer: null }], expires: "2027-01-05T10:00:00+01:00", canRestore: false }] };
  const w = load("retention.js", FACTS, fakeXHR({ [S("type=movie&tmdb=1")]: st }, sent));
  await settle();
  assert.match(row(w).textContent, /Joe Bloggs wants to delete this film/);
  assert.match(row(w).textContent, /♾️ Keep forever/);
  assert.match(row(w).textContent, /5 Jan/);
  btn(w, "data-hg-delete-deny").click();
  await settle();
  assert.deepEqual(sent.map((s) => [s.url, JSON.parse(s.body)]), [["/_download-now/delete-early/answer", { id: "a1", approve: false }]]);
});

test("deleter waits for the OK; others see nothing to answer", async () => {
  const st = { ...FREE, items: [{ id: "a1", state: "pending", season: 2, byName: "Joe Bloggs", mine: true, approver: false, asks: [{ reason: "watching", names: ["Jane Doe"], answer: null }], expires: "2027-01-05T10:00:00+01:00" }] };
  const w = load("retention.js", FACTS, fakeXHR({ [S("type=movie&tmdb=1")]: st }, []));
  await settle();
  assert.match(row(w).textContent, /Waiting for Jane Doe/);
  assert.equal(btn(w, "data-hg-delete-approve"), null);
});

test("in the recycle bin: date, who, and Restore", async () => {
  const sent = [];
  const st = { ...FREE, items: [{ id: "a1", state: "deleted", season: null, byName: "Joe Bloggs", mine: false, approver: false, asks: [], binUntil: "2027-01-05T10:00:00+01:00", canRestore: true }] };
  const w = load("retention.js", FACTS, fakeXHR({ [S("type=movie&tmdb=1")]: st }, sent));
  await settle();
  assert.match(row(w).textContent, /In the recycle bin until 5 Jan/);
  assert.match(row(w).textContent, /Joe Bloggs/);
  assert.equal(btn(w, "data-hg-delete-start"), null, "no second delete while in the bin");
  btn(w, "data-hg-delete-restore").click();
  await settle();
  assert.deepEqual(sent.map((s) => [s.url, JSON.parse(s.body)]), [["/_download-now/delete-early/restore", { id: "a1" }]]);
});

test("German texts", async () => {
  const w = load("retention.js", `<a href="/discover/tv">Serien</a>` + FACTS, fakeXHR({ [S("type=movie&tmdb=1")]: FREE }, []));
  await settle();
  assert.match(row(w).textContent, /Früher löschen/);
  btn(w, "data-hg-delete-start").click();
  await settle();
  assert.match(row(w).textContent, /Diesen Film jetzt löschen\?/);
  assert.match(row(w).textContent, /Papierkorb/);
});

test("a refused delete says why", async () => {
  const sent = [];
  const w = load("retention.js", FACTS, fakeXHR({ [S("type=movie&tmdb=1")]: FREE }, sent, { "/_download-now/delete-early/delete": [429, { error: "quota" }] }));
  await settle();
  btn(w, "data-hg-delete-start").click();
  await settle();
  btn(w, "data-hg-delete-confirm").click();
  await settle();
  assert.match(row(w).textContent, /10 deletions a week/);
});

test("an older restore is not repeated once the title was deleted again", async () => {
  const st = { ...FREE, items: [
    { id: "a1", state: "restored", season: null, byName: "Joe Bloggs", mine: true, approver: false, asks: [], at: "2026-11-22T14:03:40+01:00" },
    { id: "a2", state: "restored", season: null, byName: "Joe Bloggs", mine: true, approver: false, asks: [], at: "2026-11-22T14:05:40+01:00" },
    { id: "a3", state: "deleted", season: null, byName: "Joe Bloggs", mine: true, approver: false, asks: [], at: "2026-11-22T14:08:40+01:00", binUntil: "2027-01-05T10:00:00+01:00", canRestore: true }] };
  const w = load("retention.js", FACTS, fakeXHR({ [S("type=movie&tmdb=1")]: st }, []));
  await settle();
  assert.doesNotMatch(row(w).textContent, /Restored/);
  assert.match(row(w).textContent, /In the recycle bin/);
});
