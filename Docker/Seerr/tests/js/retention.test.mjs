import { test } from "node:test";
import assert from "node:assert/strict";
import { load, modal, tick, hintOf } from "./fixture.mjs";

// Answers GETs from `routes` (url -> body or [status, body]); POSTs are recorded and answered from `posts`.
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
          const r = routes[this.url];
          if (r === undefined) return done(404, {});
          return Array.isArray(r) ? done(r[0], r[1]) : done(200, r);
        }
        sent.push({ method: this.method, url: this.url, body, headers: this.headers });
        const p = posts[this.url];
        return Array.isArray(p) ? done(p[0], p[1]) : done(201, p ?? {});
      }
    };
  };
}
const settle = async () => { for (let i = 0; i < 12; i++) await tick(); };
const TEMP = { key: "movie/1", known: true, state: "temporary", keep: false, source: "default", default: false, reasons: [], deleteOn: "2027-02-15", soon: false, overdue: false,
  imported: "2026-11-17", ssdUntil: "2026-12-17", hddFrom: "2026-12-17", tier: "ssd", canMark: true, canUnmark: false, marks: [], markQuota: { used: 0, max: 5 }, deleteEnabled: false };
const OLD = { ...TEMP, state: "keep", keep: true, default: true, reasons: ["old"], deleteOn: null, canMark: false };
const FAN = { ...TEMP, state: "keep", keep: true, source: "fan", deleteOn: null, marks: [{ name: "Me", mine: true }], canMark: false, canUnmark: true, markQuota: { used: 1, max: 5 } };
const T = (q) => "/_download-now/retention/title?" + q;
const D = (q) => T(q) + "&default=1";
const FACTS = `<div class="media-page"><div class="media-overview-right"><div class="media-facts">
  <div class="media-fact"><span>Status</span><span class="media-fact-value">Released</span></div>
  <div class="media-fact"><span>Runtime</span><span class="media-fact-value">2h</span></div></div></div></div>`;

test("request dialog: the box sits right below Can wait, preset from the title's state", async () => {
  const sent = [];
  const w = load("retention.js", `<div id="hg-can-wait"></div>` + modal("Request Movie"), fakeXHR({ [D("type=movie&tmdb=1")]: OLD }, sent));
  await settle();
  const d = w.document.querySelector('[role="dialog"]');
  const can = d.querySelector("#hg-can-wait") || null;
  const box = w.document.querySelector("#hg-keep-forever");
  assert.ok(box);
  assert.match(box.textContent, /♾️ Keep forever/);
  assert.equal(box.querySelector("input").checked, true);
  assert.equal(box.querySelector("input").disabled, true);
  assert.match(box.textContent, /released 12 or more years ago/);
  assert.equal(box.nextElementSibling.querySelector('[data-testid="modal-ok-button"]').textContent, "Request");
  if (can) assert.equal(can.nextElementSibling, box);
});

test("dialog box follows the Can wait box when that comes later", async () => {
  const w = load("retention.js", modal("Request Movie"), fakeXHR({ [D("type=movie&tmdb=1")]: TEMP }, []));
  await settle();
  const box = w.document.querySelector("#hg-keep-forever");
  const cw = w.document.createElement("div");
  cw.id = "hg-can-wait";
  box.parentElement.insertBefore(cw, box.nextElementSibling);
  await settle();
  assert.equal(cw.nextElementSibling, w.document.querySelector("#hg-keep-forever"));
});

test("German dialog and other dialogs", async () => {
  const w = load("retention.js", `<a href="/discover/tv">Serien</a>` + modal("Film anfragen"), fakeXHR({ [D("type=movie&tmdb=1")]: TEMP }, []));
  await settle();
  assert.match(w.document.querySelector("#hg-keep-forever").textContent, /♾️ Für immer behalten/);
  assert.equal(load("retention.js", modal("Report an Issue"), fakeXHR({}, [])).document.querySelector("#hg-keep-forever"), null);
});

test("submit: a changed box is stored after the request was created, an unchanged one is not", async () => {
  const sent = [];
  const w = load("retention.js", modal("Request Movie"), fakeXHR({ [D("type=movie&tmdb=1")]: TEMP }, sent, { "/_download-now/retention/keep": [200, OLD] }));
  await settle();
  const post = (body) => { const x = new w.XMLHttpRequest(); x.open("POST", "/api/v1/request"); x.send(JSON.stringify(body)); };
  post({ mediaId: 1, mediaType: "movie" });
  await settle();
  assert.deepEqual(sent.map((s) => s.url), ["/api/v1/request"]);
  w.document.querySelector("#hg-keep-forever input").checked = true;
  post({ mediaId: 1, mediaType: "movie" });
  await settle();
  const keep = sent.find((s) => s.url === "/_download-now/retention/keep");
  assert.deepEqual(JSON.parse(keep.body), { type: "movie", tmdbId: 1, keep: true });
  assert.equal(keep.headers["Content-Type"], "application/json");
});

test("dialog box is my own fan-favourite mark: mine ticked and removable, someone else's named, none left = disabled", async () => {
  let w = load("retention.js", modal("Request Movie"), fakeXHR({ [D("type=movie&tmdb=1")]: FAN }, []));
  await settle();
  let box = w.document.querySelector("#hg-keep-forever");
  assert.equal(box.querySelector("input").checked, true);
  assert.equal(box.querySelector("input").disabled, false);
  assert.match(box.textContent, /Your ⭐ fan-favourite mark/);
  const theirs = { ...FAN, marks: [{ name: "Jane", mine: false }], canMark: true, canUnmark: false };
  w = load("retention.js", modal("Request Movie"), fakeXHR({ [D("type=movie&tmdb=1")]: theirs }, []));
  await settle();
  box = w.document.querySelector("#hg-keep-forever");
  assert.equal(box.querySelector("input").checked, false);
  assert.match(box.textContent, /fan favourite of Jane/);
  assert.match(box.textContent, /5 of 5 marks left this week|4 of 5 marks left this week/);
  w = load("retention.js", modal("Request Movie"), fakeXHR({ [D("type=movie&tmdb=1")]: { ...TEMP, canMark: false, markQuota: { used: 5, max: 5 } } }, []));
  await settle();
  box = w.document.querySelector("#hg-keep-forever");
  assert.equal(box.querySelector("input").disabled, true);
  assert.match(box.textContent, /used your 5 fan-favourite marks this week/);
});

test("submit for another title than the dialog's state is left alone", async () => {
  const sent = [];
  const w = load("retention.js", modal("Request Movie"), fakeXHR({ [D("type=movie&tmdb=1")]: TEMP }, sent));
  await settle();
  w.document.querySelector("#hg-keep-forever input").checked = true;
  const x = new w.XMLHttpRequest();
  x.open("POST", "/api/v1/request");
  x.send(JSON.stringify({ mediaId: 2, mediaType: "movie" }));
  await settle();
  assert.equal(sent.filter((s) => s.url.includes("retention")).length, 0);
});

test("title page: a fact row with the date pill and a Fan favourite button", async () => {
  const sent = [];
  const w = load("retention.js", FACTS, fakeXHR({ [T("type=movie&tmdb=1")]: TEMP }, sent, { "/_download-now/retention/keep": [200, FAN] }));
  await settle();
  const row = w.document.querySelector("[data-hg-keep-fact]");
  assert.ok(row);
  assert.equal(row.previousElementSibling.firstElementChild.textContent, "Status");
  assert.match(row.textContent, /🗑 Leaves on 15 Feb 2027/);
  const btn = row.querySelector("button[data-hg-keep-toggle]");
  assert.match(btn.textContent, /⭐ Fan favourite/);
  btn.click();
  await settle();
  assert.deepEqual(JSON.parse(sent[0].body), { type: "movie", tmdbId: 1, keep: true });
  const after = w.document.querySelector("[data-hg-keep-fact]");
  assert.match(after.textContent, /⭐ Fan favourite/);
  assert.match(after.querySelector("button[data-hg-keep-toggle]").textContent, /Remove my mark/);
  assert.match(hintOf(w, after.querySelector("[data-hg-keep-pill]")).text, /Kept because: fan favourite of you/);
});

test("title page: the pill hint names the stages and observe mode", async () => {
  const w = load("retention.js", FACTS, fakeXHR({ [T("type=movie&tmdb=1")]: TEMP }, []));
  await settle();
  const h = hintOf(w, w.document.querySelector("[data-hg-keep-pill]"));
  assert.match(h.text, /SSD until: 17 Dec/);
  assert.match(h.text, /not switched on yet/);
});

test("title page: leaving soon is orange, kept titles without the right have no toggle, unknown unrequested titles no row", async () => {
  let w = load("retention.js", FACTS, fakeXHR({ [T("type=movie&tmdb=1")]: { ...TEMP, soon: true, deleteOn: "2026-11-20" } }, []));
  await settle();
  const pill = w.document.querySelector("[data-hg-keep-pill]");
  assert.equal(pill.getAttribute("data-hg-keep-pill"), "soon");
  assert.match(pill.textContent, /Leaving soon/);
  w = load("retention.js", FACTS, fakeXHR({ [T("type=movie&tmdb=1")]: OLD }, []));
  await settle();
  assert.match(w.document.querySelector("[data-hg-keep-fact]").textContent, /♾️ Kept forever/);
  assert.equal(w.document.querySelector("[data-hg-keep-toggle]"), null);
  w = load("retention.js", FACTS, fakeXHR({ [T("type=movie&tmdb=1")]: { ...TEMP, known: false, default: false } }, []));
  await settle();
  assert.equal(w.document.querySelector("[data-hg-keep-fact]"), null);
});

const ROWS = `<div class="requests"><div class="py-2"><div><a href="/movie/1">One</a>
  <div class="card-field"><span class="card-field-name">Status</span><span class="badge">Available</span></div></div></div>
  <div class="py-2"><div><a href="/tv/2">Two</a><div class="card-field"><span class="card-field-name">Status</span><span class="badge">Available</span></div></div></div>
  <div class="py-2"><div><a href="/movie/3">Three</a><div class="card-field"><span class="card-field-name">Status</span><span class="badge">Requested</span></div></div></div></div>`;

test("request rows and cards: a pill in the Status field from one batch call", async () => {
  const batch = "/_download-now/retention/titles?keys=movie%2F1%2Ctv%2F2%2Cmovie%2F3";
  const w = load("retention.js", ROWS, fakeXHR({ [batch]: { titles: { "movie/1": { state: "temporary", deleteOn: "2027-02-15", soon: false }, "tv/2": { state: "keep", source: "default" } } } }, []), "https://requests.example.com/requests");
  await settle();
  const pills = [...w.document.querySelectorAll("[data-hg-keep-card]")];
  assert.equal(pills.length, 2);
  assert.match(pills[0].textContent, /🗑 Leaves on 15 Feb 2027/);
  assert.match(pills[1].textContent, /♾️ Kept forever/);
  assert.equal(pills[0].parentElement.firstElementChild.textContent, "Status");
});

test("admin list on the Collections page, with counts and observe note; none for others", async () => {
  const list = { deleteEnabled: false, counts: { titles: 3, keep: 1, temporary: 2, old: 1, niche: 0, goated: 0, fan: 0, protected: 0, soon: 1 },
    items: [{ key: "movie/9", t: "Invented Film", y: 2025, kind: "movie", deleteOn: "2026-11-20", why: "due", soon: true, size: 2 ** 34, tier: "hdd" }], promote: [{ key: "tv/4", title: "Invented Show", seasons: [2], played: "2026-11-15T20:00:00Z" }] };
  const page = `<main data-hg-collections><div id="hg-collections"><h1>Collections</h1></div></main>`;
  let w = load("retention.js", page, fakeXHR({ "/_download-now/retention/list": list }, []), "https://requests.example.com/collections");
  await settle();
  const sec = w.document.querySelector("#hg-collections [data-hg-keep-list]");
  assert.ok(sec);
  assert.match(sec.textContent, /Leaving the server/);
  assert.match(sec.textContent, /Invented Film \(2025\)/);
  assert.match(sec.textContent, /nothing is deleted/i);
  assert.match(sec.textContent, /Invented Show/);
  assert.equal(sec.querySelector('a[href="/movie/9"]').textContent, "Invented Film (2025)");
  w = load("retention.js", page, fakeXHR({ "/_download-now/retention/list": [403, { error: "forbidden" }] }, []), "https://requests.example.com/collections");
  await settle();
  assert.equal(w.document.querySelector("[data-hg-keep-list]"), null);
});

test("title page: the date says what started the clock, the hint shows the last play", async () => {
  let w = load("retention.js", FACTS, fakeXHR({ [T("type=movie&tmdb=1")]: { ...TEMP, lastPlayed: "2026-11-17", anchor: "2026-11-17", anchorBy: "play" } }, []));
  await settle();
  const pill = w.document.querySelector("[data-hg-keep-pill]");
  assert.match(pill.textContent, /🗑 Leaves on 15 Feb 2027 \(3 months after the last play\)/);
  assert.match(hintOf(w, pill).text, /Last played: 17 Nov/);
  w = load("retention.js", FACTS, fakeXHR({ [T("type=movie&tmdb=1")]: { ...TEMP, anchorBy: "import" } }, []));
  await settle();
  assert.match(w.document.querySelector("[data-hg-keep-pill]").textContent, /\(3 months after the download\)/);
});

test("dialog texts name the reset by watching", async () => {
  const w = load("retention.js", modal("Request Movie"), fakeXHR({ [D("type=movie&tmdb=1")]: TEMP }, []));
  await settle();
  assert.match(w.document.querySelector("#hg-keep-forever").textContent, /3 months after the download or the last play/);
});
