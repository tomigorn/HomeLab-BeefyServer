import { test } from "node:test";
import assert from "node:assert/strict";
import { load, tick } from "./fixture.mjs";

// Seerr's layout around its 404 page (an unknown route like /manual), reduced to what the script looks at
const NOT_FOUND = `<div class="flex h-full min-w-0 min-h-full bg-gray-900"><div class="sidebar"><a href="/discover/tv" class="group flex">Series</a></div>
<div class="relative mb-16 flex w-0 min-w-0 flex-1 flex-col lg:ml-64"><main class="relative top-16 z-0 focus:outline-none" tabindex="0"><div class="mb-6"><div class="max-w-8xl mx-auto px-4"><div class="error-message" data-seerr-404><p>Oops</p><p>404 - Page Not Found</p><a href="/">Return Home</a></div></div></div></main></div></div>`;
const PAGE = NOT_FOUND;

function problem(over) {
  return Object.assign({ id: "aaaaaaaaaaaa", type: "removed", key: "movie/10248", title: "The Ring Thing", year: 2004, requesters: ["jane"], details: {},
    firstSeen: "2026-11-10T03:00:00Z", lastSeen: "2026-11-15T03:00:00Z", actions: [], resolved: null }, over);
}

// fetch stub: routes by path prefix; records every call
function world(routes) {
  const calls = [];
  const before = (w) => {
    w.fetch = (url, opts) => {
      calls.push({ url, opts: opts || {} });
      const key = Object.keys(routes).find((k) => url.startsWith(k));
      const r = key ? routes[key] : { status: 404, body: {} };
      const res = typeof r === "function" ? r(url, opts) : r;
      return Promise.resolve({ ok: res.status === 200, status: res.status, json: () => Promise.resolve(res.body) });
    };
  };
  return { calls, before };
}

const ME = { status: 200, body: { id: 7, displayName: "jane", settings: { locale: "" } } };

async function page(routes, url = "https://requests.example.com/manual", body = PAGE) {
  const s = world(Object.assign({ "/api/v1/auth/me": ME }, routes));
  const w = load("manual.js", body, s.before, url);
  for (let i = 0; i < 10; i++) await tick();
  return { w, calls: s.calls, $: (q) => w.document.querySelector(q), $$: (q) => [...w.document.querySelectorAll(q)] };
}

const settle = async () => { for (let i = 0; i < 10; i++) await tick(); };
const posts = (calls) => calls.filter((c) => c.opts.method === "POST").map((c) => ({ url: c.url, body: JSON.parse(c.opts.body), type: c.opts.headers["Content-Type"] }));

test("open problems with title, type, requesters; tab counts", async () => {
  const done = problem({ id: "bbbbbbbbbbbb", resolved: { at: "2026-11-14T03:00:00Z", how: "gone" } });
  const { $, $$ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [problem(), problem({ id: "cccccccccccc", key: "movie/77", title: "Other", type: "no-release", requesters: ["jane", "joe"] })], done: [done], admin: false } } });
  assert.equal($$("[data-problem]").length, 2);
  assert.match($('[data-card="movie/10248"] [data-title]').textContent, /The Ring Thing \(2004\)/);
  assert.match($('[data-problem="aaaaaaaaaaaa"] [data-type]').textContent, /Removed from Radarr\/Sonarr/);
  assert.match($('[data-card="movie/77"] [data-requesters]').textContent, /jane, joe/);
  assert.match($('[data-tab="open"]').textContent, /Open \(2\)/);
  assert.match($('[data-tab="done"]').textContent, /Done \(1\)/);
});

test("one card per title: each download inside with its own actions; tabs count problems", async () => {
  const a = problem({ type: "import", key: "tv/312697", title: "Company Retreat", details: { release: "Show.S01E09-GRP", episodes: 1 } });
  const b = problem({ id: "dddddddddddd", type: "import", key: "tv/312697", title: "Company Retreat", requesters: ["joe"], details: { release: "Show.S01E10-GRP", episodes: 1 } });
  const { $, $$ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [a, b], done: [], admin: false } } });
  assert.equal($$("[data-card]").length, 1);
  assert.equal($$('[data-card="tv/312697"] [data-problem]').length, 2);
  assert.match($('[data-problem="dddddddddddd"] [data-file-name]').textContent, /S01E10/);
  assert.equal($$('[data-card] [data-act="files"]').length, 2);
  assert.match($("[data-requesters]").textContent, /jane, joe/);
  assert.match($('[data-tab="open"]').textContent, /Open \(2\)/);
  assert.match($('[data-card="tv/312697"] [data-count]').textContent, /2 problems/);
});

test("Check files to finish importing toggles to Hide files, is disabled while loading, opens again", async () => {
  const p = problem({ type: "import", details: { release: "Some.Film.2024", message: "" } });
  let release;
  const files = { token: "t", kind: "movie", files: [{ n: 0, name: "film.mkv", size: 9e9, episodeIds: [], probable: true, rejections: [] }], episodes: [] };
  const { $, $$, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/files": () => ({ status: 200, body: files }) });
  const btn = $('[data-act="files"]');
  assert.equal(btn.textContent, "Check files to finish importing");
  btn.click();
  assert.equal(btn.disabled, true);
  assert.equal(btn.textContent, "Loading…");
  await settle();
  assert.equal(btn.disabled, false);
  assert.equal(btn.textContent, "Hide files");
  assert.equal($$("input[data-file]").length, 1);
  btn.click();
  await settle();
  assert.equal($$("input[data-file]").length, 0);
  assert.equal(btn.textContent, "Check files to finish importing");
  btn.click();
  await settle();
  assert.equal($$("input[data-file]").length, 1);
  assert.equal(calls.filter((c) => c.url.startsWith("/_download-now/manual/files")).length, 2);
});

test("done tab: a release blocked automatically for its audio, with what was found and what happened to the file", async () => {
  const d = problem({ type: "auto-block", key: "movie/4242", title: "Some Film", year: 2020, requesters: [], resolved: { at: "2026-11-15T02:00:00Z", how: "auto-block" },
    details: { reason: "no-language-audio", release: "Some.Film.2020.MULTi.1080p-GRP", audio: ["French", "Italian"], original: "English", file: "removed", searched: true },
    actions: [{ at: "2026-11-15T02:00:00Z", user: null, action: "auto-block", result: "removed" }] });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [], done: [d], admin: false } } });
  $('[data-tab="done"]').click();
  await settle();
  const text = $("[data-problem]").textContent;
  assert.match($("[data-problem] [data-type]").textContent, /Blocked automatically/);
  assert.match(text, /Blocked automatically: no German, English or original-language audio — searching again/);
  assert.match($("[data-problem] [data-auto-block]").textContent, /Audio: French, Italian · original language English · the file was removed/);
  assert.match(text, /Some\.Film\.2020\.MULTi/);
  assert.doesNotMatch(text, /Fixed by/);
  assert.equal($("[data-problem] [data-act]"), null);
});

test("German: automatisch gesperrt, previous file put back", async () => {
  const d = problem({ type: "auto-block", key: "movie/4242", resolved: { at: "2026-11-15T02:00:00Z", how: "auto-block" },
    details: { reason: "no-language-audio", release: "X", audio: ["Russian"], original: null, file: "previous restored", searched: true } });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [], done: [d], admin: false } } }, undefined, PAGE.replace(">Series<", ">Serien<"));
  $('[data-tab="done"]').click();
  await settle();
  assert.match($("[data-problem]").textContent, /Automatisch gesperrt: kein Deutsch, Englisch oder Ton in der Originalsprache — wird neu gesucht/);
  assert.match($("[data-problem] [data-auto-block]").textContent, /Ton: Russian · die vorherige Datei ist wieder da/);
});

const MISMATCH = { reason: "mismatch", seerr: { title: "Jury Duty Presents: Company Retreat", year: 2025, tmdb: 312697, tvdb: null },
  arr: { app: "sonarr", title: "Behind the Scenes of Company Retreat", year: 2025, tmdb: 319013, tvdb: 476659 } };

test("linked to a different show: both sides with their ids, lookup to pick the right one, delete only for admins", async () => {
  const p = problem({ type: "mismatch", key: "tv/312697", title: "Jury Duty Presents: Company Retreat", year: 2025, offers: ["link"], details: MISMATCH });
  const routes = (admin) => ({ "/_download-now/manual/problems": { status: 200, body: { open: [admin ? Object.assign({}, p, { offers: ["delete", "link"] }) : p], done: [], admin } },
    "/_download-now/manual/lookup": { status: 200, body: { token: "t-look", results: [{ n: 0, title: "Jury Duty", year: 2023, overview: "", tvdbId: 432093, inLibrary: true, probable: true }] } },
    "/_download-now/manual/action": { status: 200, body: { ok: true, result: "already in Sonarr" } } });
  let { $, calls } = await page(routes(false));
  assert.match($("[data-problem] [data-type]").textContent, /Linked to a different show in Sonarr\/Radarr/);
  assert.match($("[data-what]").textContent, /points to another title/);
  const facts = $("[data-mismatch]").textContent;
  assert.match(facts, /Seerr: Jury Duty Presents: Company Retreat \(2025\) · TMDB 312697 · TVDB –/);
  assert.match(facts, /Sonarr: Behind the Scenes of Company Retreat \(2025\) · TMDB 319013 · TVDB 476659/);
  assert.match($("[data-todo]").textContent, /nothing is deleted automatically/);
  assert.equal($('[data-act="delete"]'), null);
  assert.equal($("input[data-term]").value, "Jury Duty Presents: Company Retreat");
  $('[data-act="lookup"]').click();
  await settle();
  $('[data-act="link"]').click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "link", pick: 0, token: "t-look" });
  ({ $, calls } = await page(routes(true)));
  $('[data-act="delete"]').click();
  await settle();
  $('[data-act="delete"]').click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "delete", confirm: "delete" });
});

test("imported twice: every copy with its files and size, no buttons", async () => {
  const p = problem({ type: "duplicate", key: "tv/319013", title: "Behind the Scenes of Company Retreat", year: 2025, offers: [],
    details: { reason: "duplicate", release: "Jury.Duty.S02.2160p.AMZN.WEB-DL-UBWEB", copies: [
      { app: "sonarr", title: "Jury Duty", year: 2023, tmdb: 222023, tvdb: 432093, files: 8, size: 27.8e9, added: "2026-11-09T22:03:46Z" },
      { app: "sonarr", title: "Behind the Scenes of Company Retreat", year: 2025, tmdb: 319013, tvdb: 476659, files: 8, size: 27.8e9, added: "2026-11-10T01:00:00Z" }] } });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: true } } });
  assert.match($("[data-problem] [data-type]").textContent, /Imported twice/);
  assert.match($("[data-what]").textContent, /same release was imported into more than one title/);
  const copies = $("[data-duplicate]").textContent;
  assert.match(copies, /Sonarr: Jury Duty \(2023\) · 8 files · 27\.8 GB/);
  assert.match(copies, /Sonarr: Behind the Scenes of Company Retreat \(2025\) · 8 files · 27\.8 GB/);
  assert.match($("[data-file-name]").textContent, /Jury\.Duty\.S02/);
  assert.equal($("[data-problem] [data-act]"), null);
});

test("German: mit einem anderen Titel verknüpft, doppelt importiert", async () => {
  const a = problem({ type: "mismatch", key: "tv/312697", offers: ["link"], details: MISMATCH });
  const b = problem({ id: "eeeeeeeeeeee", type: "duplicate", key: "tv/1", offers: [], details: { reason: "duplicate", copies: [{ app: "radarr", title: "A", year: 2001, files: 1, size: 1e9 }] } });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [a, b], done: [], admin: false } } }, undefined, PAGE.replace(">Series<", ">Serien<"));
  assert.match($('[data-problem="aaaaaaaaaaaa"] [data-type]').textContent, /Mit einem anderen Titel in Sonarr\/Radarr verknüpft/);
  assert.match($('[data-problem="eeeeeeeeeeee"] [data-type]').textContent, /Doppelt importiert/);
  assert.match($("[data-duplicate]").textContent, /Radarr: A \(2001\) · 1 Datei · 1\.0 GB/);
});

test("German: Dateien prüfen und Import abschliessen", async () => {
  const p = problem({ type: "import", details: { release: "Some.Film.2024", message: "" } });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } } }, undefined, PAGE.replace(">Series<", ">Serien<"));
  assert.equal($('[data-act="files"]').textContent, "Dateien prüfen und Import abschliessen");
});

test("on /manual: inside Seerr's page, Seerr's 404 hidden, a sensible title", async () => {
  const { w, $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [problem()], done: [], admin: false } } });
  const main = w.document.querySelector("main");
  assert.ok(main.querySelector("#hg-manual"));
  assert.equal(main.hasAttribute("data-hg-manual"), true);
  assert.equal(w.getComputedStyle(w.document.querySelector("main > .mb-6")).display, "none");
  assert.notEqual(w.getComputedStyle($("#hg-manual")).display, "none");
  assert.equal(w.document.title, "Manual interaction - Seerr");
});

test("other pages: nothing of ours, no data asked", async () => {
  const { w, calls } = await page({}, "https://requests.example.com/discover/movies");
  assert.equal(w.document.querySelector("#hg-manual"), null);
  assert.equal(calls.filter((c) => c.url.startsWith("/_download-now/manual/")).length, 0);
});

test("Seerr's in-app navigation: in to /manual and out again", async () => {
  const { w, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [problem()], done: [], admin: false } } }, "https://requests.example.com/discover/movies");
  w.history.pushState({}, "", "/manual");
  w.document.querySelector("main .max-w-8xl").insertAdjacentHTML("beforeend", "<p>re-render</p>");
  await settle();
  assert.ok(w.document.querySelector("main #hg-manual [data-card]"));
  w.history.pushState({}, "", "/discover/tv");
  w.document.querySelector("main .max-w-8xl").insertAdjacentHTML("beforeend", "<p>tv</p>");
  await settle();
  assert.equal(w.document.querySelector("#hg-manual"), null);
  assert.equal(w.document.querySelector("main").hasAttribute("data-hg-manual"), false);
  assert.equal(calls.filter((c) => c.url.startsWith("/_download-now/manual/problems")).length, 1);
});

test("React replacing the page content: the UI comes back once, no duplicate", async () => {
  const { w } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [problem()], done: [], admin: false } } });
  const main = w.document.querySelector("main");
  main.innerHTML = '<div class="mb-6"><div class="max-w-8xl"><div data-seerr-404><p>Oops</p></div></div></div>';
  await settle();
  assert.equal(w.document.querySelectorAll("#hg-manual").length, 1);
  assert.ok(w.document.querySelector("#hg-manual [data-card]"));
});

test("no open problems: a calm empty state", async () => {
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [], done: [], admin: false } } });
  assert.match($("[data-empty]").textContent, /Nothing needs a hand/);
});

test("text from the data is never markup", async () => {
  const evil = problem({ title: '<img src=x onerror="alert(1)">', requesters: ["<b>x</b>"], details: { release: "<script>alert(1)</script>" }, type: "import" });
  const { $, $$ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [evil], done: [], admin: false } } });
  assert.equal($$("[data-card] img, [data-card] b, [data-card] script").length, 0);
  assert.match($("[data-title]").textContent, /<img src=x/);
});

test("German: from Seerr's German menu or the user's Seerr language", async () => {
  const body = { status: 200, body: { open: [problem()], done: [], admin: false } };
  let { $ } = await page({ "/_download-now/manual/problems": body }, undefined, PAGE.replace(">Series<", ">Serien<"));
  assert.match($("h1").textContent, /Manuell beheben/);
  assert.match($('[data-tab="open"]').textContent, /Offen \(1\)/);
  assert.match($("[data-type]").textContent, /Aus Radarr\/Sonarr entfernt/);
  ({ $ } = await page({ "/_download-now/manual/problems": body, "/api/v1/auth/me": { status: 200, body: { id: 7, settings: { locale: "de" } } } }));
  assert.match($("h1").textContent, /Manuell beheben/);
});

test("not signed in: a link to the sign-in, no list", async () => {
  const { $ } = await page({ "/_download-now/manual/problems": { status: 401, body: {} } });
  assert.ok($('a[href="/login"]'));
  assert.equal($("[data-problem]"), null);
});

test("removed: Add again for everyone, Delete request only for admins and only after a second tap", async () => {
  const list = (admin) => ({ "/_download-now/manual/problems": { status: 200, body: { open: [problem()], done: [], admin } }, "/_download-now/manual/action": { status: 200, body: { ok: true, result: "added", problem: problem() } } });
  let { $, calls } = await page(list(false));
  assert.equal($('[data-act="delete"]'), null);
  $('[data-act="readd"]').click();
  await settle();
  assert.deepEqual(posts(calls), [{ url: "/_download-now/manual/action", body: { id: "aaaaaaaaaaaa", action: "readd" }, type: "application/json" }]);
  ({ $, calls } = await page(list(true)));
  $('[data-act="delete"]').click();
  await settle();
  assert.equal(posts(calls).length, 0);
  assert.match($("[data-warning]").textContent, /only the Seerr request/);
  assert.match($("[data-warning]").textContent, /files stay/);
  $('[data-act="delete"]').click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "delete", confirm: "delete" });
});

test("wrong film: Block and search again after a confirmation tap", async () => {
  const p = problem({ type: "wrong-film", offers: ["block"], details: { release: "Some.Film.1987", releaseYear: 1987, year: 2024 } });
  const { $, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } }, "/_download-now/manual/action": { status: 200, body: { ok: true, result: "blocked" } } });
  assert.match($("[data-details]").textContent, /1987/);
  $('[data-act="block"]').click();
  await settle();
  assert.equal(posts(calls).length, 0);
  $('[data-act="block"]').click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "block" });
});

const RELEASES = { token: "t-rel", releases: [
  { n: 0, title: "The.Ring.Thing.2004.German.720p", size: 2e9, quality: "WEBDL-720p", languages: ["German"], seeders: 9, score: 50, rejected: false, rejections: [], indexer: "Tracker", protocol: "torrent", age: 30 },
  { n: 1, title: "The.Ring.Thing.2004.DVDRip", size: 7e8, quality: "DVD", languages: ["German"], seeders: 4, score: 10, rejected: true, rejections: ["Quality DVD not wanted in profile"], indexer: "Tracker", protocol: "torrent", age: 100 }], season: null };

test("nothing found: older-versions switch suggested for old titles, release list, rejected ones cannot be picked, Download grabs", async () => {
  const p = problem({ type: "no-release", details: { lastSearch: "2026-11-14T04:17:00Z", profile: "4K HDR", older: "4K preferred · older versions allowed", old: true, seasons: [] } });
  const { $, $$, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/releases": { status: 200, body: RELEASES }, "/_download-now/manual/action": { status: 200, body: { ok: true, result: "grabbed" } } });
  assert.match($('[data-act="profile"]').textContent, /older versions allowed/);
  assert.ok($("[data-suggest]"));
  $('[data-act="releases"]').click();
  await settle();
  assert.ok(calls.some((c) => c.url === "/_download-now/manual/releases?id=aaaaaaaaaaaa"));
  const radios = $$('input[name="release-aaaaaaaaaaaa"]');
  assert.equal(radios.length, 2);
  assert.equal(radios[1].disabled, true);
  assert.match($$("[data-release]")[1].textContent, /Quality DVD not wanted/);
  radios[0].checked = true;
  $('[data-act="grab"]').click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "grab", release: 0, token: "t-rel" });
});

test("nothing found, recent title: the switch is offered but not suggested", async () => {
  const p = problem({ type: "no-release", details: { lastSearch: null, profile: "4K HDR", older: "4K preferred · older versions allowed", old: false, seasons: [] } });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } } });
  assert.ok($('[data-act="profile"]'));
  assert.equal($("[data-suggest]"), null);
});

test("unmatched: search prefilled, most probable match preselected, Link sends the pick", async () => {
  const p = problem({ type: "unmatched", key: "tv/4040", title: "Seerr Series", year: 2019 });
  const lookup = { token: "t-look", results: [{ n: 0, title: "Other Show", year: 2001, overview: "", tvdbId: 1111, inLibrary: false, probable: false }, { n: 1, title: "Seerr Series", year: 2019, overview: "", tvdbId: 9090, inLibrary: false, probable: true }] };
  const { $, $$, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/lookup": { status: 200, body: lookup }, "/_download-now/manual/action": { status: 200, body: { ok: true, result: "linked" } } });
  assert.equal($("input[data-term]").value, "Seerr Series");
  $("input[data-term]").value = "Seerr Series 2019";
  $('[data-act="lookup"]').click();
  await settle();
  assert.ok(calls.some((c) => c.url === "/_download-now/manual/lookup?id=aaaaaaaaaaaa&term=Seerr%20Series%202019"));
  const radios = $$('input[name="pick-aaaaaaaaaaaa"]');
  assert.equal(radios[1].checked, true);
  assert.match($$("[data-result]")[1].textContent, /Most likely/);
  $('[data-act="link"]').click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "link", pick: 1, token: "t-look" });
});

test("import: files with the probable episode picked, Import sends the checked files", async () => {
  const p = problem({ type: "import", key: "tv/4040", details: { release: "Company.Retreat.2026.S01", message: "matched to series by ID", episodes: 2 } });
  const files = { token: "t-files", kind: "tv", files: [{ n: 0, name: "CR.S01E01.mkv", size: 3e9, episodeIds: [501], probable: true, rejections: [] }, { n: 1, name: "sample.mkv", size: 1e6, episodeIds: [], probable: false, rejections: ["Sample"] }],
    episodes: [{ id: 501, label: "S01E01", title: "Pilot" }, { id: 502, label: "S01E02", title: "Two" }] };
  const { $, $$, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/files": { status: 200, body: files }, "/_download-now/manual/action": { status: 200, body: { ok: true, result: "import started" } } });
  $('[data-act="files"]').click();
  await settle();
  const boxes = $$("input[data-file]");
  assert.deepEqual(boxes.map((b) => b.checked), [true, false]);
  const sel = $$("select[data-episode]");
  assert.equal(sel[0].value, "501");
  sel[0].value = "502";
  $('[data-act="import"]').click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "import", files: [{ n: 0, episodeIds: [502] }], token: "t-files" });
});

test("movie import: no episode picker, the files only", async () => {
  const p = problem({ type: "import", details: { release: "Some.Film.2024", message: "" } });
  const files = { token: "t-movie", kind: "movie", files: [{ n: 0, name: "film.mkv", size: 9e9, episodeIds: [], probable: true, rejections: [] }], episodes: [] };
  const { $, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/files": { status: 200, body: files }, "/_download-now/manual/action": { status: 200, body: { ok: true, result: "ok" } } });
  $('[data-act="files"]').click();
  await settle();
  assert.equal($("select[data-episode]"), null);
  $('[data-act="import"]').click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "import", files: [{ n: 0 }], token: "t-movie" });
});

test("done tab: how it ended and who did what", async () => {
  const d = problem({ resolved: { at: "2026-11-14T03:00:00Z", how: "readd" }, actions: [{ at: "2026-11-14T03:00:00Z", user: "jane", action: "readd", result: "added to Radarr again" }] });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [], done: [d], admin: false } } });
  $('[data-tab="done"]').click();
  await settle();
  assert.match($("[data-problem]").textContent, /jane/);
  assert.match($("[data-problem]").textContent, /added to Radarr again/);
  assert.equal($("[data-problem] [data-act]"), null);
});

test("a refused action shows why, nothing breaks", async () => {
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [problem()], done: [], admin: false } }, "/_download-now/manual/action": { status: 429, body: { error: "slow down" } } });
  $('[data-act="readd"]').click();
  await settle();
  assert.match($("[data-msg]").textContent, /Too many tries/);
});

test("Show releases toggles too, German labels", async () => {
  const p = problem({ type: "no-release", details: { lastSearch: null, profile: "x", older: null, old: false, seasons: [] } });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/releases": { status: 200, body: RELEASES } }, undefined, PAGE.replace(">Series<", ">Serien<"));
  const btn = $('[data-act="releases"]');
  assert.equal(btn.textContent, "Releases zeigen");
  btn.click();
  await settle();
  assert.equal(btn.textContent, "Releases ausblenden");
  btn.click();
  assert.equal(btn.textContent, "Releases zeigen");
});

const BY_ID = "Found matching series via grab history, but release was matched to series by ID. Automatic import is not possible. See the FAQ for details.";
const E09 = "Na.Mira.do.Juri.Apresenta.Cenas.dos.Bastidores.de.um.Retiro.Corporativo.S01E09.1080p.AMZN.WEB-DL.DDP5.1.H.264-Full3R";
const MEDIA = { resolution: "1080p", video: "H.264", hdr: null, audio: [{ language: "por", codec: "E-AC3", channels: "5.1" }, { language: "eng", codec: "AAC", channels: "2.0" }], subtitles: ["por", "eng"], size: 1.59e9, minutes: 45 };
const e09 = (over) => problem(Object.assign({ type: "import", key: "tv/312697", title: "Jury Duty Presents: Behind the Scenes of Company Retreat", year: 2026,
  details: { reason: "id-match", release: E09, message: BY_ID, episodes: 1, media: MEDIA } }, over));

test("a problem reads in sections: what's wrong in plain words, what to do, the download with its media, raw details collapsed", async () => {
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [e09()], done: [], admin: false } } });
  assert.match($("[data-what]").textContent, /release name doesn't match the title/);
  assert.doesNotMatch($("[data-what]").textContent, /grab history/);
  assert.ok($("[data-todo]").textContent.length > 10);
  assert.equal($("[data-file-label]").textContent, "File name of the download:");
  assert.equal($("[data-file-name]").textContent, E09);
  assert.ok($("[data-file-name] wbr"));
  const raw = $("details[data-raw]");
  assert.equal(raw.open, false);
  assert.equal(raw.querySelector("summary").textContent, "Details from Sonarr/Radarr");
  assert.match(raw.textContent, /grab history/);
  assert.equal($("[data-summary]").textContent, "1080p · Portuguese, English");
  assert.equal($("[data-download] [data-media]"), null);
  assert.equal($("[data-download] [data-check]"), null);
});

test("an unknown arr message: a general sentence with the message itself", async () => {
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [e09({ details: { reason: "unknown", release: E09, message: "Something new happened" } })], done: [], admin: false } } });
  assert.match($("[data-what]").textContent, /Something new happened/);
});

test("every reason has a plain sentence in both languages", () => {
  const w = load("manual.js", "", null, "https://requests.example.com/discover/movies");
  const { TEXT } = w.hgManual;
  for (const code of ["id-match", "no-eligible", "not-upgrade", "sample", "unparsable", "not-in-release", "wrong-film", "dub-only", "failed", "no-release", "removed", "unmatched", "mismatch", "duplicate", "unknown"]) {
    assert.ok(TEXT.en.reasons[code] && TEXT.de.reasons[code], code);
  }
  assert.doesNotMatch(JSON.stringify(TEXT.de), /ß/);
});

test("German: labels and reason in German", async () => {
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [e09()], done: [], admin: false } } }, undefined, PAGE.replace(">Series<", ">Serien<"));
  assert.equal($("[data-file-label]").textContent, "Dateiname des Downloads:");
  assert.equal($("details[data-raw] summary").textContent, "Details von Sonarr/Radarr");
  assert.match($("[data-what]").textContent, /Release-Name passt nicht/);
  assert.match($("[data-summary]").textContent, /Portugiesisch, Englisch/);
});

test("file list: each file with its media and a plain reason for a rejection", async () => {
  const files = { token: "t", kind: "tv", files: [{ n: 0, name: E09 + ".mkv", size: 1.59e9, episodeIds: [2045], probable: true, rejections: ["Not an upgrade for existing episode file(s)"], reasons: ["not-upgrade"], media: MEDIA }],
    episodes: [{ id: 2045, label: "S01E09", title: "" }] };
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [e09()], done: [], admin: false } }, "/_download-now/manual/files": { status: 200, body: files } });
  $('[data-act="files"]').click();
  await settle();
  assert.match($("[data-file-media]").textContent, /1080p · H\.264/);
  assert.match($("[data-list] .hg-m-why").textContent, /already has this/);
  assert.ok($("[data-list] .hg-m-name wbr"));
});

const CHECKS = [
  { rule: "original", mark: "bad", language: "English" },
  { rule: "german", mark: "none" },
  { rule: "quality", mark: "ok", quality: "WEBDL-1080p", upgrade: "WEB 2160p" },
  { rule: "hdr", mark: "ok", hdr: "SDR", upgrade: true },
  { rule: "runtime", mark: "ok", minutes: 31, expected: 30 },
  { rule: "size", mark: "none", size: 1.59e9, gbh: 3.1, limit: 20 }];

test("checks against our rules: one line on the card, a mark per property in the file list", async () => {
  const p = e09({ details: { reason: "id-match", release: E09, message: BY_ID, episodes: 1, media: MEDIA, checks: CHECKS, virus: { state: "clean", at: "2026-11-15T04:25:00Z", signature: null } } });
  const files = { token: "t", kind: "tv", files: [{ n: 0, name: E09 + ".mkv", size: 1.59e9, episodeIds: [2045], probable: true, rejections: [], media: MEDIA, checks: CHECKS, virus: { state: "clean", at: "2026-11-15T04:25:00Z" } }],
    episodes: [{ id: 2045, label: "S01E09", title: "" }] };
  const { $, w } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } }, "/_download-now/manual/files": { status: 200, body: files } });
  assert.equal($("[data-summary]").textContent, "1080p · Portuguese, English · ❌ 1 problem · ✅ virus scan clean");
  assert.equal($("[data-check]"), null);
  $('[data-act="files"]').click();
  await settle();
  const c = (rule) => $(`[data-list] [data-check="${rule}"]`);
  assert.equal(c("original").getAttribute("data-mark"), "bad");
  assert.match(c("original").textContent, /^❌ Original language \(English\): missing/);
  assert.equal(w.hgTip.text(w.hgTip.get(c("original"))), "❌ Original language (English)\nMissing, dubbed only");
  assert.equal(w.hgTip.get(c("quality")), null, "nothing more to say on a passed check");
  assert.match(c("german").textContent, /^– German/);
  assert.match(c("quality").textContent, /^✅ Quality WEBDL-1080p: allowed by the profile/);
  assert.match(c("quality").querySelector("[data-info]").textContent, /may upgrade to WEB 2160p later/);
  assert.match(c("hdr").textContent, /^✅ SDR: allowed by the profile/);
  assert.match(c("hdr").querySelector("[data-info]").textContent, /may upgrade to 4K HDR later/);
  assert.match(c("runtime").textContent, /^✅ Running time 31 min \(expected 30\)/);
  assert.match(c("size").textContent, /1\.6 GB · 3\.1 GB\/h/);
  assert.equal($("[data-list] [data-virus-verdict]").textContent, "✅ No virus found");
  assert.match($('[data-list] [data-virus-src="clamav"]').textContent, /^ClamAV: clean/);
});

test("virus scan states in both languages", async () => {
  const states = [[{ state: "pending" }, "⏳ virus scan pending", "⏳ Virenscan läuft"],
    [{ state: "infected", signature: "Win.Test.EICAR_HDB-1" }, "❌ malware found (Win.Test.EICAR_HDB-1)", "❌ Schadsoftware gefunden (Win.Test.EICAR_HDB-1)"],
    [{ state: "unknown" }, "⏳ not virus-scanned yet", "⏳ noch nicht auf Viren geprüft"]];
  for (const [virus, en, de] of states) {
    const p = e09({ details: { reason: "id-match", release: E09, message: BY_ID, virus } });
    let { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } } });
    assert.equal($("[data-summary]").textContent, en);
    ({ $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } } }, undefined, PAGE.replace(">Series<", ">Serien<")));
    assert.equal($("[data-summary]").textContent, de);
  }
});

test("file list: virus scan and checks per file; a refused import says why", async () => {
  const files = { token: "t", kind: "tv", files: [{ n: 0, name: E09 + ".mkv", size: 1.59e9, episodeIds: [2045], probable: true, rejections: [], media: MEDIA, checks: CHECKS, virus: { state: "infected", signature: "Win.Test.EICAR_HDB-1" } }],
    episodes: [{ id: 2045, label: "S01E09", title: "" }] };
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [e09()], done: [], admin: false } }, "/_download-now/manual/files": { status: 200, body: files },
    "/_download-now/manual/action": { status: 403, body: { error: "malware" } } });
  $('[data-act="files"]').click();
  await settle();
  assert.match($("[data-list] [data-virus]").textContent, /malware found/);
  assert.ok($('[data-list] [data-check="runtime"]'));
  $('[data-act="import"]').click();
  await settle();
  assert.match($("[data-msg]").textContent, /Malware found/);
});

test("a release search behind another one waits and arrives by itself", async () => {
  const p = problem({ id: "eeeeeeeeeeee", title: "The Ring Thing", type: "no-release", details: { reason: "no-release", lastSearch: null, profile: "x", older: null, old: false, seasons: [] } });
  const answers = [{ status: 200, body: { state: "queued", waitingFor: "Tied Up", ahead: 1, season: null } }, { status: 200, body: { state: "running", ahead: 0, season: null } }, { status: 200, body: Object.assign({ state: "done" }, RELEASES) }];
  const { w, $, $$, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/releases": () => answers.length > 1 ? answers.shift() : answers[0] });
  w.hgManual.setPoll(40);
  $('[data-act="releases"]').click();
  await settle();
  assert.match($("[data-msg]").textContent, /Waiting for the search for “Tied Up” to finish, then this starts by itself/);
  for (let i = 0; i < 100 && !$$('input[name="release-eeeeeeeeeeee"]').length; i++) await new Promise((r) => setTimeout(r, 5));
  assert.equal($$('input[name="release-eeeeeeeeeeee"]').length, 2);
  assert.equal($('[data-act="releases"]').textContent, "Hide releases");
  assert.equal($("[data-msg]").textContent, "");
  assert.equal(calls.filter((c) => c.url.startsWith("/_download-now/manual/releases")).length, 3);
});

test("a full search queue is said as it is, not as too many tries", async () => {
  const p = problem({ type: "no-release", details: { reason: "no-release", lastSearch: null, profile: "x", older: null, old: false, seasons: [] } });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/releases": { status: 429, body: { error: "search queue full" } } });
  $('[data-act="releases"]').click();
  await settle();
  assert.match($("[data-msg]").textContent, /Too many searches are waiting right now/);
  assert.doesNotMatch($("[data-msg]").textContent, /Too many tries/);
  assert.equal($('[data-act="releases"]').textContent, "Show releases");
});

test("release list: allowed ones to pick, the not allowed ones folded with plain reasons", async () => {
  const p = problem({ type: "no-release", details: { reason: "no-release", lastSearch: null, profile: "x", older: null, old: false, seasons: [] } });
  const body = { state: "done", token: "t", dropped: 194, releases: [
    Object.assign({}, RELEASES.releases[0], { gbh: 13.3 }),
    Object.assign({}, RELEASES.releases[1], { rejections: ["Custom Formats LQ have score -29997750 below Movie's profile minimum -10000"], reasons: ["cf-score"] })] };
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } }, "/_download-now/manual/releases": { status: 200, body } });
  $('[data-act="releases"]').click();
  await settle();
  const folded = $("details[data-not-allowed]");
  assert.equal(folded.open, false);
  assert.equal(folded.querySelector("summary").textContent, "1 more, not allowed — show why");
  assert.match(folded.textContent, /score by our rules is too low/);
  assert.doesNotMatch(folded.textContent, /-29997750/);
  assert.equal($("[data-list] > ul [data-release]").textContent.includes("13.3 GB/h"), true);
});

test("release list without a match: an honest empty state and the older-versions hint", async () => {
  const p = problem({ type: "no-release", details: { reason: "no-release", lastSearch: null, profile: "x", older: "4K preferred · older versions allowed", old: true, seasons: [] } });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/releases": { status: 200, body: { state: "done", token: "t", dropped: 210, releases: [] } } });
  $('[data-act="releases"]').click();
  await settle();
  assert.match($("[data-no-releases]").textContent, /No release found for this title — the indexers don't know a version yet/);
  assert.match($("[data-no-releases]").textContent, /Allow older versions/);
  assert.equal($('[data-act="grab"]'), null);
});

test("done: how each problem ended, in words", async () => {
  const d = (id, how, actions) => problem({ id, key: "movie/" + id.length + id[0].charCodeAt(0), resolved: { at: "2026-11-14T03:00:00Z", how }, actions: actions || [] });
  const done = [d("a00000000001", "gone"), d("b00000000002", "imported"), d("c00000000003", "found"),
    d("d00000000004", "delete", [{ at: "2026-11-14T03:00:00Z", user: "janedoe", action: "delete", result: "1 Seerr request(s) deleted" }]),
    d("e00000000005", "readd", [{ at: "2026-11-14T03:00:00Z", user: "jane", action: "readd", result: "added" }])];
  const { $, $$ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [], done, admin: false } } });
  $('[data-tab="done"]').click();
  await settle();
  const how = $$("[data-resolved]").map((x) => x.textContent.split(" · ")[0]);
  assert.deepEqual(how, ["Went away by itself", "Imported", "Found and downloaded", "Removed by admin (janedoe)", "Fixed by jane (added again)"]);
});

test("one episode is singular", () => {
  const w = load("manual.js", "", null, "https://requests.example.com/discover/movies");
  const { TEXT } = w.hgManual;
  assert.deepEqual([TEXT.en.episodes(1), TEXT.en.episodes(2), TEXT.de.episodes(1), TEXT.de.episodes(2)], ["1 episode", "2 episodes", "1 Folge", "2 Folgen"]);
});

test("one waiting search per user: the message says so", () => {
  const w = load("manual.js", "", null, "https://requests.example.com/discover/movies");
  const { TEXT } = w.hgManual;
  assert.equal(TEXT.en.errorCodes["too many searches"], "You already have a search waiting: this one can start when it is done.");
  assert.equal(TEXT.de.errorCodes["too many searches"], "Du hast schon eine Suche in der Warteschlange: diese kann starten, wenn sie fertig ist.");
});

test("release rows: details link at the indexer, account hint, what the NFO says", async () => {
  const p = problem({ type: "no-release", details: { reason: "no-release", lastSearch: null, profile: "x", older: null, old: false, seasons: [] } });
  const body = { state: "done", token: "t", dropped: 0, releases: [
    Object.assign({}, RELEASES.releases[0], { link: "https://nzbgeek.info/geekseek.php?guid=abc", private: true, scout: { state: "read", source: "nfo", langs: ["Swiss German", "German"], lines: ["Audio: <b>Schwiizerdütsch</b>, Deutsch"] } }),
    Object.assign({}, RELEASES.releases[0], { n: 2, title: "Other", link: "javascript:alert(1)", private: false, scout: null, readable: false })] };
  const { $$ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } }, "/_download-now/manual/releases": { status: 200, body } });
  $$('[data-act="releases"]')[0].click();
  await settle();
  const [a, b] = $$("[data-release]");
  const link = a.querySelector("a[data-details-link]");
  assert.equal(link.getAttribute("href"), "https://nzbgeek.info/geekseek.php?guid=abc");
  assert.equal(link.getAttribute("target"), "_blank");
  assert.equal(link.getAttribute("rel"), "noopener noreferrer");
  assert.equal(link.textContent, "Details ↗");
  assert.match(a.textContent, /🔒 account needed/);
  assert.match(a.querySelector("[data-scout]").textContent, /^📄 NFO: Swiss German, German/);
  assert.equal(a.querySelector("[data-scout] li").textContent, "Audio: <b>Schwiizerdütsch</b>, Deutsch");
  assert.equal(a.querySelector("[data-scout] b"), null);
  assert.equal(b.querySelector("a[data-details-link]"), null);
  assert.doesNotMatch(b.textContent, /account needed|NFO/);
  assert.equal(b.querySelector('[data-act="describe"]'), null);
});

test("read a description on demand: asks, shows it is reading, the result arrives by itself", async () => {
  const p = problem({ type: "no-release", details: { reason: "no-release", lastSearch: null, profile: "x", older: null, old: false, seasons: [] } });
  const row = (scout) => ({ state: "done", token: "t", dropped: 0, releases: [Object.assign({}, RELEASES.releases[0], { title: "The Ring Thing (2004) [XVID]", readable: true, scout })] });
  const answers = [row(null), row({ state: "pending" }), row({ state: "read", source: "description", langs: ["Swiss German"], lines: ["Language: Schwyzerdütsch"] })];
  const { w, $, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/releases": () => ({ status: 200, body: answers.length > 1 ? answers.shift() : answers[0] }),
    "/_download-now/manual/action": { status: 200, body: { ok: true, result: "asked" } } });
  w.hgManual.setPoll(20);
  $('[data-act="releases"]').click();
  await settle();
  assert.equal($('[data-act="describe"]').textContent, "Read description");
  $('[data-act="describe"]').click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "describe", release: 0, token: "t" });
  assert.match($("[data-scout]").textContent, /Reading the description/);
  for (let i = 0; i < 100 && !/Swiss German/.test(($("[data-scout]") || {}).textContent || ""); i++) await new Promise((r) => setTimeout(r, 5));
  assert.match($("[data-scout]").textContent, /^📄 Description: Swiss German/);
  assert.equal($('[data-act="releases"]').textContent, "Hide releases");
});

test("a refused read says why", async () => {
  const p = problem({ type: "no-release", details: { reason: "no-release", lastSearch: null, profile: "x", older: null, old: false, seasons: [] } });
  const body = { state: "done", token: "t", dropped: 0, releases: [Object.assign({}, RELEASES.releases[0], { readable: true, scout: { state: "refused", why: "daily limit reached, try tomorrow" } })] };
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } }, "/_download-now/manual/releases": { status: 200, body } },
    undefined, PAGE.replace(">Series<", ">Serien<"));
  $('[data-act="releases"]').click();
  await settle();
  assert.match($("[data-scout]").textContent, /daily limit reached, try tomorrow/);
  assert.equal($('[data-act="describe"]').textContent, "Beschreibung lesen");
});

test("too big for the full virus scan: a clean parts scan of a genuine video passes, the rest shown as it is; the 2 GiB limit in the hint box", async () => {
  const cases = [[{ state: "parts-clean", minutes: 34, parts: 1 }, "✅ No virus found|ClamAV: clean (large file: attachments and subtitles scanned, genuine video)", "✅ parts scanned clean",
    "✅ Kein Virus gefunden|ClamAV: sauber (grosse Datei: Anhänge und Untertitel geprüft, echtes Video)", "✅ Teile sauber"],
    [{ state: "parts-error", why: "no video stream" }, "⚠️ Not fully scannable — admins only|ClamAV: too big for the full scan, parts scan not possible (no video stream)", "⚠️ only partly scannable",
    "⚠️ Nicht vollständig prüfbar — nur Admins|ClamAV: zu gross für den vollen Scan, Teil-Scan nicht möglich (no video stream)", "⚠️ nur teilweise prüfbar"]];
  for (const [virus, full, short, fullDe, shortDe] of cases) {
    const p = e09({ details: { reason: "id-match", release: E09, message: BY_ID, virus } });
    const files = { token: "t", kind: "tv", files: [{ n: 0, name: E09 + ".mkv", size: 2.48e9, episodeIds: [2045], probable: true, rejections: [], virus }], episodes: [{ id: 2045, label: "S01E09", title: "" }] };
    for (const [body, f, sh] of [[PAGE, full, short], [PAGE.replace(">Series<", ">Serien<"), fullDe, shortDe]]) {
      const { $, w } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } }, "/_download-now/manual/files": { status: 200, body: files } }, undefined, body);
      assert.equal($("[data-summary]").textContent, sh);
      $('[data-act="files"]').click();
      await settle();
      assert.equal(virusRows($), f);
      assert.match(w.hgTip.text(w.hgTip.get($('[data-list] [data-virus-src="clamav"]'))), body === PAGE ? /ClamAV scans at most 2 GiB, so the video stream itself is not scanned/ : /ClamAV prüft höchstens 2 GiB, darum wird der Videostrom selbst nicht gescannt/);
    }
  }
});

const SEEDLESS = { n: 0, title: "The Ring Thing (2004) [XVID]", size: 7e8, quality: "DVD", languages: ["German"], seeders: 0, score: 0, rejected: true,
  rejections: ["Does not contain one of the required terms: Mundart", "Not enough seeders: 0. Minimum seeders: 1"], reasons: ["required-terms", "seeders"], indexer: "Tracker", protocol: "torrent", age: 900 };
const NOTHING = () => problem({ type: "no-release", details: { reason: "no-release", lastSearch: null, profile: "Swiss German (Mundart) only - any quality", older: null, old: true, seasons: [] } });

test("after a week of nothing found: every rejected release has Download anyway with its reasons in plain words", async () => {
  const { $, $$, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [NOTHING()], done: [], admin: false } },
    "/_download-now/manual/releases": { status: 200, body: { state: "done", token: "t-ov", override: true, releases: [SEEDLESS] } },
    "/_download-now/manual/action": { status: 200, body: { ok: true, result: "downloaded anyway" } } });
  $('[data-act="releases"]').click();
  await settle();
  const fold = $("details[data-not-allowed]");
  assert.equal(fold.open, true);
  assert.match($("[data-override-hint]").textContent, /Nothing found for a week/);
  const row = $$("[data-release]")[0];
  assert.match(row.textContent, /lacks a term the profile requires/);
  assert.match(row.textContent, /Too few seeders/);
  const btn = row.querySelector('[data-act="override"]');
  assert.equal(btn.textContent, "Download anyway");
  btn.click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "override", release: 0, token: "t-ov" });
});

test("before a week: rejected releases stay unpickable, no override", async () => {
  const { $, $$ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [NOTHING()], done: [], admin: false } },
    "/_download-now/manual/releases": { status: 200, body: { state: "done", token: "t", override: false, releases: [SEEDLESS] } } });
  $('[data-act="releases"]').click();
  await settle();
  assert.equal($$('[data-act="override"]').length, 0);
  assert.equal($("[data-override-hint]"), null);
});

test("German: Trotzdem herunterladen", async () => {
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [NOTHING()], done: [], admin: false } },
    "/_download-now/manual/releases": { status: 200, body: { state: "done", token: "t", override: true, releases: [SEEDLESS] } } }, undefined, PAGE.replace(">Series<", ">Serien<"));
  $('[data-act="releases"]').click();
  await settle();
  assert.equal($('[data-act="override"]').textContent, "Trotzdem herunterladen");
  assert.match($("[data-override-hint]").textContent, /Seit einer Woche nichts gefunden/);
});

const overridden = (o) => problem({ type: "no-release", resolved: { at: "2026-11-14T03:00:00Z", how: "override" },
  actions: [{ at: "2026-11-14T03:00:00Z", user: "jane", action: "override", result: "downloaded anyway: The Ring Thing (2004) [XVID]" }],
  override: Object.assign({ at: "2026-11-14T03:00:00Z", user: "jane", release: "The Ring Thing (2004) [XVID]", seeders: 0, protocol: "torrent", state: "downloading", seeds: 0 }, o) });

test("done: downloaded anyway by whom, and where the download stands", async () => {
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [], done: [overridden()], admin: false } } });
  $('[data-tab="done"]').click();
  await settle();
  assert.match($("[data-resolved]").textContent, /^Downloaded anyway by jane · /);
  assert.equal($("[data-override]").textContent, "⬇️ Downloading anyway (0 seeders — waits for a seeder, checked every night for a better version)");
});

test("override states in both languages", () => {
  const states = [[{}, "⬇️ Downloading anyway (0 seeders — waits for a seeder, checked every night for a better version)", "⬇️ Wird trotzdem heruntergeladen (0 Seeder — wartet auf einen Seeder, jede Nacht wird nach einer besseren Version gesucht)"],
    [{ seeds: 3 }, "⬇️ Downloading anyway (3 seeders — checked every night for a better version)", "⬇️ Wird trotzdem heruntergeladen (3 Seeder — jede Nacht wird nach einer besseren Version gesucht)"],
    [{ protocol: "usenet", seeds: null, seeders: null }, "⬇️ Downloading anyway (checked every night for a better version)", "⬇️ Wird trotzdem heruntergeladen (jede Nacht wird nach einer besseren Version gesucht)"],
    [{ state: "finished" }, "✅ Downloaded anyway — checked every night for a better version", "✅ Trotzdem heruntergeladen — jede Nacht wird nach einer besseren Version gesucht"],
    [{ state: "replacing" }, "🔄 A version the profile allows was found — it downloads instead", "🔄 Eine vom Profil erlaubte Version wurde gefunden — sie wird stattdessen heruntergeladen"],
    [{ state: "replaced" }, "✅ Replaced by a version the profile allows", "✅ Ersetzt durch eine vom Profil erlaubte Version"]];
  const w = load("manual.js", "", null, "https://requests.example.com/discover/movies");
  for (const [o, en, de] of states) {
    const x = Object.assign({ state: "downloading", seeds: 0, seeders: 0, protocol: "torrent" }, o);
    assert.equal(w.hgManual.overrideState(w.hgManual.TEXT.en, x), en);
    assert.equal(w.hgManual.overrideState(w.hgManual.TEXT.de, x), de);
  }
  assert.equal(w.hgManual.TEXT.de.overriddenBy("jane"), "Trotzdem heruntergeladen von jane");
});

test("VirusTotal fingerprint per file in both languages: line, hint box, card summary only for malware and flagged", async () => {
  const at = "2026-11-15T04:25:00Z";
  const cases = [[{ state: "queued" }, "VirusTotal: lookup queued, a few minutes", "VirusTotal: Abfrage in der Warteschlange, ein paar Minuten", null, null],
    [{ state: "unchecked" }, "VirusTotal: not checked yet (retried later), no effect on the import", "VirusTotal: noch nicht geprüft (wird wiederholt), kein Einfluss auf den Import", null, null],
    [{ state: "unknown", checked: at }, "VirusTotal: no reports — this exact file isn't known there yet, normal for new downloads, not a warning", "VirusTotal: keine Meldungen — diese genaue Datei ist dort noch nicht bekannt, normal bei neuen Downloads, keine Warnung", null, null],
    [{ state: "clean", malicious: 0, engines: 70, checked: at }, "VirusTotal: known file, no scanner flags it", "VirusTotal: bekannte Datei, kein Scanner meldet sie", null, null],
    [{ state: "flagged", malicious: 1, engines: 70, checked: at }, "VirusTotal: flagged by 1 scanner, often a false alarm", "VirusTotal: von 1 Scanner gemeldet, oft ein Fehlalarm", "⚠️ flagged by VirusTotal", "⚠️ von VirusTotal gemeldet"],
    [{ state: "flagged", malicious: 2, engines: 70, checked: at }, "VirusTotal: flagged by 2 scanners, often a false alarm", "VirusTotal: von 2 Scannern gemeldet, oft ein Fehlalarm", "⚠️ flagged by VirusTotal", "⚠️ von VirusTotal gemeldet"],
    [{ state: "malware", malicious: 41, engines: 70, checked: at }, "VirusTotal: known malware (41 of 70 scanners)", "VirusTotal: bekannte Schadsoftware (41 von 70 Scannern)", "❌ known malware", "❌ bekannte Schadsoftware"]];
  for (const [vt, en, de, shortEn, shortDe] of cases) {
    const virus = { state: "clean", at, vt };
    const p = e09({ details: { reason: "id-match", release: E09, message: BY_ID, virus } });
    const files = { token: "t", kind: "tv", files: [{ n: 0, name: E09 + ".mkv", size: 1.59e9, episodeIds: [2045], probable: true, rejections: [], virus }], episodes: [{ id: 2045, label: "S01E09", title: "" }] };
    for (const [body, line, short, never] of [[PAGE, en, shortEn, /the file is never uploaded/], [PAGE.replace(">Series<", ">Serien<"), de, shortDe, /die Datei wird nie hochgeladen/]]) {
      const { $, w } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } }, "/_download-now/manual/files": { status: 200, body: files } }, undefined, body);
      const summary = $("[data-summary]").textContent;
      if (short) assert.ok(summary.endsWith(" · " + short), summary);
      else assert.doesNotMatch(summary, /VirusTotal/);
      $('[data-act="files"]').click();
      await settle();
      const v = $("[data-list] [data-vt]");
      assert.equal(v.textContent, line);
      assert.equal(v.getAttribute("data-vt"), vt.state);
      assert.equal(v.getAttribute("title"), null);
      assert.match(w.hgTip.text(w.hgTip.get(v)), never);
    }
  }
});

test("no fingerprint verdict, no VirusTotal line; a flagged file refused for a user says only an admin can import it", async () => {
  const files = (vt) => ({ token: "t", kind: "tv", files: [{ n: 0, name: E09 + ".mkv", size: 1.59e9, episodeIds: [2045], probable: true, rejections: [], virus: Object.assign({ state: "clean" }, vt ? { vt } : {}) }], episodes: [{ id: 2045, label: "S01E09", title: "" }] });
  let { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [e09()], done: [], admin: false } }, "/_download-now/manual/files": { status: 200, body: files(null) } });
  $('[data-act="files"]').click();
  await settle();
  assert.equal($("[data-list] [data-vt]"), null);
  ({ $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [e09()], done: [], admin: false } }, "/_download-now/manual/files": { status: 200, body: files({ state: "flagged", malicious: 2, engines: 70 }) },
    "/_download-now/manual/action": { status: 403, body: { error: "flagged" } } }));
  $('[data-act="files"]').click();
  await settle();
  $('[data-act="import"]').click();
  await settle();
  assert.match($("[data-msg]").textContent, /only an admin can import it/);
});

// the virus block of the first file: verdict, then each source row, joined by |
const virusRows = ($) => [...$("[data-list] [data-virus]").children].map((e) => e.textContent).join("|");

test("virus block per file: one overall verdict first, then ClamAV and VirusTotal in plain words, both languages", async () => {
  const cases = [
    [{ state: "clean", vt: { state: "unknown" } }, "clean",
      "✅ No virus found|ClamAV: clean|VirusTotal: no reports — this exact file isn't known there yet, normal for new downloads, not a warning",
      "✅ Kein Virus gefunden|ClamAV: sauber|VirusTotal: keine Meldungen — diese genaue Datei ist dort noch nicht bekannt, normal bei neuen Downloads, keine Warnung"],
    [{ state: "parts-clean", minutes: 34, vt: { state: "unknown" } }, "clean",
      "✅ No virus found|ClamAV: clean (large file: attachments and subtitles scanned, genuine video)|VirusTotal: no reports — this exact file isn't known there yet, normal for new downloads, not a warning",
      "✅ Kein Virus gefunden|ClamAV: sauber (grosse Datei: Anhänge und Untertitel geprüft, echtes Video)|VirusTotal: keine Meldungen — diese genaue Datei ist dort noch nicht bekannt, normal bei neuen Downloads, keine Warnung"],
    [{ state: "parts-clean", minutes: 34, runtimeFits: true, expected: 34 }, "clean",
      "✅ No virus found|ClamAV: clean (large file: attachments and subtitles scanned, genuine video)", "✅ Kein Virus gefunden|ClamAV: sauber (grosse Datei: Anhänge und Untertitel geprüft, echtes Video)"],
    [{ state: "parts-clean", minutes: 50, runtimeFits: false, expected: 34, vt: { state: "unknown" } }, "runtime",
      "⚠️ Running time doesn't match — admins only|ClamAV: large file: attachments and subtitles clean, genuine video, but running time 50 min vs expected 34 min|VirusTotal: no reports — this exact file isn't known there yet, normal for new downloads, not a warning",
      "⚠️ Laufzeit passt nicht — nur Admins|ClamAV: grosse Datei: Anhänge und Untertitel sauber, echtes Video, aber Laufzeit 50 Min., erwartet 34 Min.|VirusTotal: keine Meldungen — diese genaue Datei ist dort noch nicht bekannt, normal bei neuen Downloads, keine Warnung"],
    [{ state: "parts-clean", minutes: 34, runtimeFits: false, expected: null }, "runtime",
      "⚠️ Running time doesn't match — admins only|ClamAV: large file: attachments and subtitles clean, genuine video, but running time 34 min, expected unknown",
      "⚠️ Laufzeit passt nicht — nur Admins|ClamAV: grosse Datei: Anhänge und Untertitel sauber, echtes Video, aber Laufzeit 34 Min., erwartet unbekannt"],
    [{ state: "parts-clean", minutes: null, runtimeFits: false, expected: 34 }, "runtime",
      "⚠️ Running time doesn't match — admins only|ClamAV: large file: attachments and subtitles clean, genuine video, but running time unknown vs expected 34 min",
      "⚠️ Laufzeit passt nicht — nur Admins|ClamAV: grosse Datei: Anhänge und Untertitel sauber, echtes Video, aber Laufzeit unbekannt, erwartet 34 Min."],
    [{ state: "clean", vt: { state: "clean", malicious: 0, engines: 70 } }, "clean",
      "✅ No virus found|ClamAV: clean|VirusTotal: known file, no scanner flags it", "✅ Kein Virus gefunden|ClamAV: sauber|VirusTotal: bekannte Datei, kein Scanner meldet sie"],
    [{ state: "clean" }, "clean", "✅ No virus found|ClamAV: clean", "✅ Kein Virus gefunden|ClamAV: sauber"],
    [{ state: "infected", signature: "Win.Test.EICAR_HDB-1", vt: { state: "unknown" } }, "malware",
      "❌ Malware — import blocked|ClamAV: malware found (Win.Test.EICAR_HDB-1)|VirusTotal: no reports — this exact file isn't known there yet, normal for new downloads, not a warning",
      "❌ Schadsoftware — Import gesperrt|ClamAV: Schadsoftware gefunden (Win.Test.EICAR_HDB-1)|VirusTotal: keine Meldungen — diese genaue Datei ist dort noch nicht bekannt, normal bei neuen Downloads, keine Warnung"],
    [{ state: "clean", vt: { state: "flagged", malicious: 2, engines: 70 } }, "flagged",
      "⚠️ Flagged — admins only|ClamAV: clean|VirusTotal: flagged by 2 scanners, often a false alarm", "⚠️ Gemeldet — nur Admins|ClamAV: sauber|VirusTotal: von 2 Scannern gemeldet, oft ein Fehlalarm"],
    [{ state: "clean", vt: { state: "malware", malicious: 41, engines: 70 } }, "malware",
      "❌ Malware — import blocked|ClamAV: clean|VirusTotal: known malware (41 of 70 scanners)", "❌ Schadsoftware — Import gesperrt|ClamAV: sauber|VirusTotal: bekannte Schadsoftware (41 von 70 Scannern)"],
    [{ state: "pending", vt: { state: "queued" } }, "checking",
      "⏳ Still being checked|ClamAV: scan queued, usually a few minutes|VirusTotal: lookup queued, a few minutes", "⏳ Wird noch geprüft|ClamAV: Scan in der Warteschlange, meist ein paar Minuten|VirusTotal: Abfrage in der Warteschlange, ein paar Minuten"],
    [{ state: "unknown" }, "checking", "⏳ Still being checked|ClamAV: not scanned yet", "⏳ Wird noch geprüft|ClamAV: noch nicht geprüft"],
    [{ state: "parts-error", why: "no video stream", vt: { state: "unknown" } }, "partial",
      "⚠️ Not fully scannable — admins only|ClamAV: too big for the full scan, parts scan not possible (no video stream)|VirusTotal: no reports — this exact file isn't known there yet, normal for new downloads, not a warning",
      "⚠️ Nicht vollständig prüfbar — nur Admins|ClamAV: zu gross für den vollen Scan, Teil-Scan nicht möglich (no video stream)|VirusTotal: keine Meldungen — diese genaue Datei ist dort noch nicht bekannt, normal bei neuen Downloads, keine Warnung"],
  ];
  for (const [virus, verdict, en, de] of cases) {
    const files = { token: "t", kind: "tv", files: [{ n: 0, name: E09 + ".mkv", size: 1.59e9, episodeIds: [2045], probable: true, rejections: [], virus }], episodes: [{ id: 2045, label: "S01E09", title: "" }] };
    for (const [body, want] of [[PAGE, en], [PAGE.replace(">Series<", ">Serien<"), de]]) {
      const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [e09()], done: [], admin: false } }, "/_download-now/manual/files": { status: 200, body: files } }, undefined, body);
      $('[data-act="files"]').click();
      await settle();
      assert.equal(virusRows($), want);
      assert.equal($("[data-list] [data-virus]").getAttribute("data-verdict"), verdict);
      assert.equal($("[data-list] [data-virus]").firstElementChild.hasAttribute("data-virus-verdict"), true);
    }
  }
});

test("running time off on a large file: the hint box explains why only admins can import, both languages", async () => {
  const virus = { state: "parts-clean", minutes: 50, parts: 1, runtimeFits: false, expected: 34 };
  const files = { token: "t", kind: "tv", files: [{ n: 0, name: E09 + ".mkv", size: 2.48e9, episodeIds: [2045], probable: true, rejections: [], virus }], episodes: [{ id: 2045, label: "S01E09", title: "" }] };
  for (const [body, want] of [[PAGE, /⚠️ Large file.*ClamAV scans at most 2 GiB.*Attachments and subtitles:.*scanned clean.*Video:.*genuine video stream \(50 min\).*Running time:.*50 min, expected 34 min.*more than 25 % off.*only an admin can import it/s],
    [PAGE.replace(">Series<", ">Serien<"), /⚠️ Grosse Datei.*ClamAV prüft höchstens 2 GiB.*Anhänge und Untertitel:.*sauber.*Video:.*echter Videostrom \(50 Min\.\).*Laufzeit:.*50 Min\., erwartet 34 Min\..*mehr als 25 % daneben.*nur ein Admin importieren/s]]) {
    const { $, w } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [e09()], done: [], admin: false } }, "/_download-now/manual/files": { status: 200, body: files } }, undefined, body);
    $('[data-act="files"]').click();
    await settle();
    assert.match(w.hgTip.text(w.hgTip.get($('[data-list] [data-virus-src="clamav"]'))), want);
  }
});

test("Next page: Seerr's 404 stays hidden and untouched until React hydrated it (no error #418, no redraw), then the page is put in", async () => {
  const s = world({ "/api/v1/auth/me": ME, "/_download-now/manual/problems": { status: 200, body: { open: [problem()], done: [], admin: false } } });
  const w = load("manual.js", `<div id="__next">${PAGE}</div>`, s.before, "https://requests.example.com/manual");
  await new Promise((r) => setTimeout(r, 80));
  assert.equal(w.document.getElementById("hg-manual"), null);
  assert.equal(w.document.querySelector("main").hasAttribute("data-hg-manual"), false);
  assert.equal(w.document.documentElement.hasAttribute("data-hg-manual-wait"), true);
  assert.match(w.document.getElementById("hg-manual-wait").textContent, /html\[data-hg-manual-wait\] main\s*\{\s*visibility:\s*hidden/);
  w.document.getElementById("__next").firstElementChild["__reactFiber$t"] = {};
  await new Promise((r) => setTimeout(r, 80));
  await settle();
  assert.ok(w.document.querySelector("main > #hg-manual"));
  assert.equal(w.document.documentElement.hasAttribute("data-hg-manual-wait"), false);
});

test("Next page elsewhere: nothing hidden while waiting for hydration", async () => {
  const w = load("manual.js", `<div id="__next">${PAGE}</div>`, world({}).before, "https://requests.example.com/discover/tv");
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(w.document.documentElement.hasAttribute("data-hg-manual-wait"), false);
});

test("the early /manual mark goes once our page is in, and on any other page", async () => {
  const mark = (win) => win.document.documentElement.setAttribute("data-hg-early", "manual");
  const s = world({ "/api/v1/auth/me": ME, "/_download-now/manual/problems": { status: 200, body: { open: [problem()], done: [], admin: false } } });
  const w = load("manual.js", PAGE, (win) => { s.before(win); mark(win); }, "https://requests.example.com/manual");
  await settle();
  assert.ok(w.document.querySelector("main #hg-manual"));
  assert.equal(w.document.documentElement.hasAttribute("data-hg-early"), false);
  const o = world({});
  const w2 = load("manual.js", PAGE, (win) => { o.before(win); mark(win); }, "https://requests.example.com/discover/movies");
  await settle();
  assert.equal(w2.document.documentElement.hasAttribute("data-hg-early"), false);
});

test("on /collections the early mark of that page is left for collections.js", async () => {
  const { w } = await page({}, "https://requests.example.com/collections");
  w.document.documentElement.setAttribute("data-hg-early", "collections");
  w.document.body.insertAdjacentHTML("beforeend", "<p>x</p>");
  await settle();
  assert.equal(w.document.documentElement.getAttribute("data-hg-early"), "collections");
});

test("every card with a download: Wrong release — block it and search again, second tap, hint box, EN and DE", async () => {
  const cards = [problem({ type: "import", offers: ["block", "import"], details: { release: "The.Tiger.2025.2160p-FLUX" } }),
    problem({ id: "bbbbbbbbbbbb", key: "movie/2", type: "wrong-film", offers: ["block"], details: { release: "Some.Film.1987", releaseYear: 1987, year: 2024 } }),
    problem({ id: "cccccccccccc", key: "movie/3", type: "failed", offers: ["block"], details: { release: "Broken.Film.2020" } })];
  for (const [body, label, again, hint] of [[PAGE, "Wrong release — block it and search again", "Tap again to block it", /removes its files from the download client/],
    [PAGE.replace(">Series<", ">Serien<"), "Falsches Release — sperren und neu suchen", "Nochmals tippen zum Sperren", /entfernt seine Dateien aus dem Download-Programm/]]) {
    const { w, $, $$, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: cards, done: [], admin: false } },
      "/_download-now/manual/action": { status: 200, body: { ok: true, result: "blocked" } } }, undefined, body);
    const btns = $$('[data-act="block"]');
    assert.equal(btns.length, 3);
    btns.forEach((b) => assert.equal(b.textContent, label));
    assert.match(w.hgTip.text(w.hgTip.get(btns[0])), hint);
    btns[0].click();
    await settle();
    assert.equal(posts(calls).length, 0);
    assert.equal(btns[0].textContent, again);
    btns[0].click();
    await settle();
    assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "block" });
  }
});

test("no download on the card, no block: removed, unmatched, nothing found", async () => {
  const open = [problem({ offers: ["readd"] }), problem({ id: "bbbbbbbbbbbb", key: "tv/4040", type: "unmatched", offers: ["link"] }),
    problem({ id: "cccccccccccc", key: "movie/77", type: "no-release", offers: ["grab", "override", "profile"], details: { lastSearch: null, seasons: [] } })];
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open, done: [], admin: false } } });
  assert.equal($('[data-act="block"]'), null);
});

test("done: a download-anyway download still running can be blocked; who blocked it, EN and DE", async () => {
  const running = Object.assign(overridden(), { offers: ["block"] });
  const blocked = problem({ id: "dddddddddddd", key: "movie/9", type: "import", resolved: { at: "2026-11-14T03:00:00Z", how: "block" },
    actions: [{ at: "2026-11-14T03:00:00Z", user: "jane", action: "block", result: "release removed and blocked, searching again" }] });
  for (const [body, by] of [[PAGE, "Blocked and searched again by jane"], [PAGE.replace(">Series<", ">Serien<"), "Gesperrt und neu gesucht von jane"]]) {
    const { $, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [], done: [running, blocked], admin: false } },
      "/_download-now/manual/action": { status: 200, body: { ok: true, result: "blocked" } } }, undefined, body);
    $('[data-tab="done"]').click();
    await settle();
    assert.match($('[data-problem="dddddddddddd"] [data-resolved]').textContent.split(" · ")[0], new RegExp("^" + by + "$"));
    assert.equal($('[data-problem="dddddddddddd"] [data-act]'), null);
    const b = $('[data-problem="aaaaaaaaaaaa"] [data-act="block"]');
    b.click();
    b.click();
    await settle();
    assert.deepEqual(posts(calls)[0].body, { id: "aaaaaaaaaaaa", action: "block" });
  }
});

test("other names of the title on the card, plain text; a release named like one of them says so, EN and DE", async () => {
  const p = problem({ type: "import", key: "movie/1252037", title: "The Tank", year: 2025, offers: ["block", "import"],
    details: { release: "Der.Tiger.2025.2160p-GRP", names: ["Der Tiger", "Тигр", "<b>x</b>"], nameMatch: { name: "Der Tiger", original: true } } });
  const other = problem({ id: "bbbbbbbbbbbb", type: "import", key: "movie/1252037", title: "The Tank", year: 2025, offers: ["block", "import"],
    details: { release: "Le.Tigre.2025.1080p-GRP", names: ["Der Tiger", "Тигр", "<b>x</b>"], nameMatch: { name: "Le Tigre", original: false } } });
  for (const [body, aka, orig, alt] of [[PAGE, "Also known as: Der Tiger · Тигр · <b>x</b>", "✅ Release name matches the original title “Der Tiger”", "✅ Release name matches another name of this title: “Le Tigre”"],
    [PAGE.replace(">Series<", ">Serien<"), "Auch bekannt als: Der Tiger · Тигр · <b>x</b>", "✅ Release-Name passt zum Originaltitel «Der Tiger»", "✅ Release-Name passt zu einem anderen Namen des Titels: «Le Tigre»"]]) {
    const { $, $$ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p, other], done: [], admin: false } } }, undefined, body);
    assert.equal($$("[data-aka]").length, 1);
    assert.equal($('[data-card="movie/1252037"] [data-aka]').textContent, aka);
    assert.equal($("[data-aka] b"), null);
    assert.equal($('[data-problem="aaaaaaaaaaaa"] [data-name-match]').textContent, orig);
    assert.equal($('[data-problem="bbbbbbbbbbbb"] [data-name-match]').textContent, alt);
  }
});

test("no other names: no line", async () => {
  const p = problem({ type: "import", offers: ["block", "import"], details: { release: "The.Tank.2025.2160p-GRP", names: [], nameMatch: null } });
  const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } } });
  assert.equal($("[data-aka]"), null);
  assert.equal($("[data-name-match]"), null);
});

const TIGER = "The.Tiger.2025.2160p.AMZN.WEB-DL.DUAL.DDP5.1.Atmos.HDR.H.265-FLUX";
const tankDone = (over) => problem(Object.assign({ id: "eeeeeeeeeeee", key: "movie/1252037", title: "The Tank", year: 2025, type: "import", offers: ["reject", "replace"],
  resolved: { at: "2026-11-15T22:21:54Z", how: "imported" }, current: { ref: "0a1b2c3d4e5f", release: TIGER, quality: "WEBDL-2160p", at: "2026-11-15T22:28:08Z", season: null },
  details: { release: TIGER } }, over));
const REPLACE_LIST = { state: "done", token: "t-rep", override: false, releases: [
  { n: 0, title: "The.Tank.2025.German.DL.1080p.WEB.H264-GRP", size: 6e9, quality: "WEBDL-1080p", score: 500, rejected: false, current: false, rejections: ["Existing file on disk is of equal or higher preference: WEBDL-2160p"], indexer: "NZBgeek", protocol: "usenet" },
  { n: 1, title: TIGER, size: 2e10, quality: "WEBDL-2160p", score: 900, rejected: true, current: true, rejections: ["Existing file on disk is of equal or higher preference: WEBDL-2160p"], indexer: "NZBgeek", protocol: "usenet" }] };
const DE = PAGE.replace(">Series<", ">Serien<");

async function doneTab(done, routes = {}, body = PAGE) {
  const pg = await page(Object.assign({ "/_download-now/manual/problems": { status: 200, body: { open: [], done, admin: false } },
    "/_download-now/manual/action": { status: 200, body: { ok: true, result: "ok" } } }, routes), undefined, body);
  pg.$('[data-tab="done"]').click();
  await settle();
  return pg;
}

test("done with a known release: the current release, Pick another release and Block this release for every user, EN and DE", async () => {
  for (const [body, cur, pick, block] of [[PAGE, "Current release:", "Pick another release", "Block this release"], [DE, "Aktuelles Release:", "Anderes Release wählen", "Dieses Release sperren"]]) {
    const { $ } = await doneTab([tankDone()], {}, body);
    assert.match($("[data-current]").textContent, new RegExp(cur + ".*FLUX"));
    assert.equal($('[data-problem="eeeeeeeeeeee"] [data-act="replace"]').textContent, pick);
    assert.equal($('[data-problem="eeeeeeeeeeee"] [data-act="reject"]').textContent, block);
  }
});

test("done without a known release: no current release line, no release actions", async () => {
  const { $ } = await doneTab([tankDone({ offers: [], current: null })]);
  assert.equal($("[data-current]"), null);
  assert.equal($('[data-act="replace"]'), null);
  assert.equal($('[data-act="reject"]'), null);
});

test("Pick another release: the same release list, the current one marked and not pickable, a confirm tap replaces it", async () => {
  for (const [body, again, warn, mark] of [[PAGE, "Tap again to replace it", /current file stays until the new one is imported/, /current release/],
    [DE, "Nochmals tippen zum Ersetzen", /aktuelle Datei bleibt, bis das neue importiert ist/, /aktuelles Release/]]) {
    const { $, $$, calls } = await doneTab([tankDone({ current: { ref: "0a1b2c3d4e5f", release: TIGER, quality: "WEBDL-2160p", season: 1 }, key: "tv/312697" })],
      { "/_download-now/manual/releases": { status: 200, body: REPLACE_LIST } }, body);
    $('[data-act="replace"]').click();
    await settle();
    assert.ok(calls.some((c) => c.url === "/_download-now/manual/releases?id=eeeeeeeeeeee&season=1"));
    const radios = $$('input[name="release-eeeeeeeeeeee"]');
    assert.equal(radios.length, 2);
    assert.equal(radios[1].disabled, true);
    assert.match($$("[data-release]")[1].querySelector("[data-current-release]").textContent, mark);
    radios[0].checked = true;
    const go = $('[data-act="grab"]');
    go.click();
    await settle();
    assert.equal(posts(calls).length, 0);
    assert.equal(go.textContent, again);
    assert.match($("[data-warning]").textContent, warn);
    go.click();
    await settle();
    assert.deepEqual(posts(calls)[0].body, { id: "eeeeeeeeeeee", action: "replace", release: 0, token: "t-rep" });
  }
});

test("Pick another release after a week: Download anyway replaces too", async () => {
  const list = Object.assign({}, REPLACE_LIST, { override: true, releases: [Object.assign({}, REPLACE_LIST.releases[0], { rejected: true, rejections: ["Custom Formats score of -5 is below the minimum of 0"] }), REPLACE_LIST.releases[1]] });
  const { $, calls } = await doneTab([tankDone()], { "/_download-now/manual/releases": { status: 200, body: list } });
  $('[data-act="replace"]').click();
  await settle();
  const rows = [...$("[data-list]").querySelectorAll("[data-release]")];
  assert.equal(rows[1].querySelector('[data-act="override"]'), null);
  rows[0].querySelector('[data-act="override"]').click();
  await settle();
  assert.deepEqual(posts(calls)[0].body, { id: "eeeeeeeeeeee", action: "replace", release: 0, token: "t-rep" });
});

test("Block this release: a confirm tap with Keep the current file (on by default), EN and DE", async () => {
  for (const [body, again, keep] of [[PAGE, "Tap again to block it", "Keep the current file until a replacement arrives"], [DE, "Nochmals tippen zum Sperren", "Aktuelle Datei behalten, bis ein Ersatz da ist"]]) {
    for (const off of [false, true]) {
      const { $, calls } = await doneTab([tankDone()], {}, body);
      const b = $('[data-act="reject"]');
      b.click();
      await settle();
      assert.equal(posts(calls).length, 0);
      assert.equal(b.textContent, again);
      const box = $("input[data-keep]");
      assert.equal(box.checked, true);
      assert.match(box.parentNode.textContent, new RegExp(keep));
      if (off) box.checked = false;
      b.click();
      await settle();
      assert.deepEqual(posts(calls)[0].body, { id: "eeeeeeeeeeee", action: "reject", confirm: "reject", ref: "0a1b2c3d4e5f", keep: !off });
    }
  }
});

test("open again while replacing: what happens instead of the problem, no buttons, EN and DE", async () => {
  const picked = tankDone({ resolved: null, offers: [], replace: { mode: "replace", release: "The.Tank.2025.German.DL.1080p.WEB.H264-GRP", from: TIGER, user: "joe", state: "downloading" } });
  const blocked = tankDone({ id: "ffffffffffff", key: "movie/9", resolved: null, offers: [], replace: { mode: "reject", from: TIGER, user: "jane", keep: true, state: "searching" } });
  for (const [body, a, b, line] of [[PAGE, "Replacing release", "Blocked, searching again", /picked by joe/], [DE, "Release wird ersetzt", "Gesperrt, wird neu gesucht", /gewählt von joe/]]) {
    const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [picked, blocked], done: [], admin: false } } }, undefined, body);
    assert.equal($('[data-problem="eeeeeeeeeeee"] [data-type]').textContent, a);
    assert.equal($('[data-problem="ffffffffffff"] [data-type]').textContent, b);
    assert.match($('[data-problem="eeeeeeeeeeee"] [data-replace]').textContent, line);
    assert.equal($("[data-act]"), null);
  }
});

test("done after a replacement: in words, EN and DE", async () => {
  const replaced = tankDone({ resolved: { at: "2026-11-16T03:00:00Z", how: "replaced" }, actions: [{ at: "2026-11-15T23:00:00Z", user: "joe", action: "replace", result: "grabbed x" }, { at: "2026-11-16T03:00:00Z", user: null, action: "replaced", result: "x imported" }] });
  const failed = tankDone({ id: "ffffffffffff", key: "movie/9", resolved: { at: "2026-11-16T03:00:00Z", how: "replace-failed" }, offers: [] });
  for (const [body, a, b] of [[PAGE, "Release replaced (picked by joe)", "Replacement failed — the current file stays"], [DE, "Release ersetzt (gewählt von joe)", "Ersetzen fehlgeschlagen — die aktuelle Datei bleibt"]]) {
    const { $ } = await doneTab([replaced, failed], {}, body);
    assert.equal($('[data-problem="eeeeeeeeeeee"] [data-resolved]').textContent.split(" · ")[0], a);
    assert.equal($('[data-problem="ffffffffffff"] [data-resolved]').textContent.split(" · ")[0], b);
  }
});

test("a refused file delete of a Keep forever title says why, EN and DE", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../../indexer-bar/www/manual.js", import.meta.url), "utf8");
  assert.match(src, /"kept forever": "Kept forever \(storage retention\): the file stays/);
  assert.match(src, /"kept forever": "Für immer behalten \(Speicher-Aufräumung\): die Datei bleibt/);
});
