import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, tick } from "./fixture.mjs";

const titlePage = (status = "Requested") => `
<main><div class="media-page"><div class="media-header">
  <div class="media-title"><div class="media-status"><span class="px-2 inline-flex rounded-full"><div class="relative z-20 flex items-center"><span>${status}</span></div></span></div>
    <h1 data-testid="media-title">Tied Up <span class="media-year">(2026)</span></h1></div>
  <div class="media-actions"><button class="button-md">Request</button></div>
</div><div class="media-overview">Overview</div></div></main>`;

const API = "/_download-now/request?type=movie&tmdb=1480613";
const PROFILES = [{ id: 7, name: "4K HDR - UHD Bluray + WEB (1080p fallback)" }, { id: 11, name: "4K preferred · older versions allowed" }];
const TAGS = [{ label: "audio-german", kind: "audio", name: "German" }, { label: "audio-swiss-german", kind: "audio", name: "Swiss German" }, { label: "subs-spanish", kind: "subs", name: "Spanish" }];
const R = (extra = {}) => ({ id: 57, status: 2, is4k: false, mine: true, by: null, createdAt: "2026-10-28T08:41:20.000Z", seasons: [], profile: PROFILES[0], tags: [TAGS[1]], auto: [], canWait: true, editable: true, deleteEffect: "clear", ...extra });
const STATE = (reqs = [R()], manager = false) => ({ requests: reqs, profiles: PROFILES, tags: TAGS, manager });

function fakeXHR(routes, posts, calls) {
  return function (w) {
    w.XMLHttpRequest = class {
      constructor() { this.headers = {}; }
      open(method, url) { this.method = method; this.url = url; }
      setRequestHeader(k, v) { this.headers[k] = v; }
      send(body) {
        calls.push({ method: this.method, url: this.url, body: body && JSON.parse(body), headers: this.headers });
        const r = this.method === "GET" ? routes[this.url] : posts[this.url];
        const v = typeof r === "function" ? r(body && JSON.parse(body)) : r;
        this.status = v ? v.status ?? 200 : 404;
        this.responseText = JSON.stringify(v ? v.body ?? v : {});
        setTimeout(() => this.onload && this.onload(), 0);
      }
    };
    w.hgNightLabel = { hintText: (list) => "Waiting for the night · " + list[1][1], icons: (key) => (key === "movie/1480613" ? [["🌙", "Waiting for the night — downloads at night (cheaper power)"], ["🧭", "No release found yet"]] : []) };
  };
}
const settle = async () => { for (let i = 0; i < 8; i++) { await new Promise((r) => setTimeout(r, 20)); await tick(); } };
const opened = [];
afterEach(() => { while (opened.length) opened.pop().close(); });
function page(state, posts = {}, calls = [], path = "/movie/1480613", body = titlePage(), confirmAnswer = true, confirms = []) {
  const routes = { "/api/v1/auth/me": { id: 7, permissions: 8352 }, [API]: state };
  const w = load("my-request.js", body, (win) => { fakeXHR(routes, posts, calls)(win); win.confirm = (t) => { confirms.push(t); return confirmAnswer; }; }, "https://requests.example.com" + path);
  opened.push(w);
  return w;
}
const panel = (w) => w.document.getElementById("hg-my-request");
const POST = "/_download-now/request";

test("own request: a collapsible card under the title header shows profile, tags, Can wait and the icons with hints", async () => {
  const w = page(STATE());
  await settle();
  const p = panel(w);
  assert.ok(p);
  assert.equal(p.tagName, "DETAILS");
  assert.equal(p.previousElementSibling.className, "media-header");
  const sum = p.querySelector("summary").textContent;
  assert.match(sum, /My request/);
  assert.match(sum, /🌙🧭/);
  const icon = p.querySelector("[data-hg-icon]");
  assert.equal(icon.textContent, "🌙🧭");
  assert.equal(icon.getAttribute("title"), "Waiting for the night · No release found yet");
  assert.equal(icon.getAttribute("aria-label"), "Waiting for the night · No release found yet");
  assert.equal(p.querySelector("select[name=profile]").value, "7");
  assert.deepEqual([...p.querySelectorAll("[data-tag]")].map((e) => e.getAttribute("data-tag")), ["audio-swiss-german"]);
  assert.match(p.querySelector("[data-tag]").textContent, /Audio: Swiss German/);
  assert.deepEqual([...p.querySelectorAll("select[name=add] option")].map((o) => o.value), ["", "audio-german", "subs-spanish"]);
  assert.equal(p.querySelector("input[name=canWait]").checked, true);
  assert.equal(p.querySelector("button[data-save]").disabled, true);
});

test("someone else's title (403), other pages, not logged in: no card", async () => {
  const w = page({ status: 403, body: {} });
  await settle();
  assert.equal(panel(w), null);
  const calls = [];
  const w2 = page(STATE(), {}, calls, "/discover");
  await settle();
  assert.equal(panel(w2), null);
  assert.ok(!calls.some((c) => c.url.startsWith("/_download-now/")));
});

test("change the profile: only that is sent, the card shows the saved state", async () => {
  const calls = [];
  const w = page(STATE(), { [POST]: (b) => ({ changed: ["profile 7 -> 11"], request: R({ profile: PROFILES[1] }) }) }, calls);
  await settle();
  const sel = panel(w).querySelector("select[name=profile]");
  sel.value = "11";
  sel.dispatchEvent(new w.Event("change", { bubbles: true }));
  const save = panel(w).querySelector("button[data-save]");
  assert.equal(save.disabled, false);
  save.click();
  await settle();
  const post = calls.find((c) => c.method === "POST");
  assert.deepEqual(post.body, { type: "movie", tmdbId: 1480613, action: "update", requestId: 57, profileId: 11 });
  assert.equal(post.headers["Content-Type"], "application/json");
  assert.match(panel(w).querySelector("[role=status]").textContent, /✅ Saved/);
  assert.equal(panel(w).querySelector("select[name=profile]").value, "11");
  assert.equal(panel(w).open, true);
});

test("tags: remove one, add one from the allowed list; Can wait off", async () => {
  const calls = [];
  const w = page(STATE(), { [POST]: { changed: ["x"], request: R() } }, calls);
  await settle();
  const p = panel(w);
  p.querySelector("[data-tag=audio-swiss-german] button").click();
  const add = p.querySelector("select[name=add]");
  add.value = "subs-spanish";
  add.dispatchEvent(new w.Event("change", { bubbles: true }));
  const cw = p.querySelector("input[name=canWait]");
  cw.checked = false;
  cw.dispatchEvent(new w.Event("change", { bubbles: true }));
  p.querySelector("button[data-save]").click();
  await settle();
  assert.deepEqual(calls.find((c) => c.method === "POST").body, { type: "movie", tmdbId: 1480613, action: "update", requestId: 57, tags: ["subs-spanish"], canWait: false });
});

test("delete: the confirm text says exactly what goes; cancel sends nothing, OK deletes", async () => {
  const calls = [], confirms = [];
  const w = page(STATE(), { [POST]: { deleted: "clear" } }, calls, undefined, undefined, false, confirms);
  await settle();
  panel(w).querySelector("button[data-delete]").click();
  await settle();
  assert.match(confirms[0], /Delete your request for “Tied Up”\?/);
  assert.match(confirms[0], /the title too: its Radarr\/Sonarr entry, downloaded files \(7 days in the recycle bin\), running downloads and the Jellyfin entry/);
  assert.ok(!calls.some((c) => c.method === "POST"));
  const calls2 = [], confirms2 = [];
  const w2 = page(STATE([R({ deleteEffect: "request-only" })]), { [POST]: { deleted: "request-only" } }, calls2, undefined, undefined, true, confirms2);
  await settle();
  panel(w2).querySelector("button[data-delete]").click();
  await settle();
  assert.match(confirms2[0], /Only your request goes; the title stays/);
  assert.deepEqual(calls2.find((c) => c.method === "POST").body, { type: "movie", tmdbId: 1480613, action: "delete", requestId: 57 });
  assert.match(panel(w2).querySelector("[role=status]").textContent, /Request deleted/);
});

test("shared title: read-only with a note, delete still possible", async () => {
  const w = page(STATE([R({ editable: false, deleteEffect: "request-only" })]));
  await settle();
  const p = panel(w);
  assert.equal(p.querySelector("select[name=profile]").disabled, true);
  assert.equal(p.querySelector("input[name=canWait]").disabled, true);
  assert.equal(p.querySelector("[data-tag] button"), null);
  assert.equal(p.querySelector("button[data-save]"), null);
  assert.match(p.textContent, /Others requested this title too: ask an admin to change it/);
  assert.ok(p.querySelector("button[data-delete]"));
});

test("manager: every request with its requester; seasons and automatic tags shown", async () => {
  const w = page(STATE([R({ mine: true }), R({ id: 60, mine: false, by: "Alezauron", seasons: [1, 2], auto: ["old-rare"], status: 1 })], true), {}, [], "/movie/1480613");
  await settle();
  const p = panel(w);
  assert.match(p.querySelector("summary").textContent, /Requests \(2\)/);
  const items = p.querySelectorAll("[data-request]");
  assert.equal(items.length, 2);
  assert.match(items[1].textContent, /by Alezauron/);
  assert.match(items[1].textContent, /Pending approval/);
  assert.match(items[1].textContent, /Seasons 1, 2/);
  assert.match(items[1].textContent, /Old & rare \(automatic\)/);
});

test("a picked profile shows as kept", async () => {
  const w = page(STATE([R({ auto: ["profile-pinned"] })]), {}, [], "/movie/1480613");
  await settle();
  assert.match(panel(w).querySelector("[data-request]").textContent, /Quality kept \(your pick\)/);
});

test("German UI", async () => {
  const w = page(STATE(), {}, [], "/movie/1480613", titlePage("Angefragt"));
  await settle();
  const p = panel(w);
  assert.match(p.querySelector("summary").textContent, /Meine Anfrage/);
  assert.match(p.querySelector("[data-tag]").textContent, /Ton: Swiss German/);
  assert.match(p.textContent, /Kann warten — nachts herunterladen \(günstigerer Strom\)/);
  assert.equal(p.querySelector("button[data-delete]").textContent, "Anfrage löschen");
});

test("leaving the title removes the card; a re-render keeps one card", async () => {
  const calls = [];
  const w = page(STATE(), {}, calls);
  await settle();
  w.document.body.insertAdjacentHTML("beforeend", "<p>re-render</p>");
  await settle();
  assert.equal(w.document.querySelectorAll("#hg-my-request").length, 1);
  w.history.pushState({}, "", "/discover");
  w.document.body.insertAdjacentHTML("beforeend", "<p>route</p>");
  await settle();
  assert.equal(panel(w), null);
});
