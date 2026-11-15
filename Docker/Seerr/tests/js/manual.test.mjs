import { test } from "node:test";
import assert from "node:assert/strict";
import { load, tick } from "./fixture.mjs";

const PAGE = `<div id="hg-manual"></div>`;

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

async function page(routes, url = "https://requests.example.com/_manual/") {
  const s = world(Object.assign({ "/api/v1/auth/me": ME }, routes));
  const w = load("manual.js", PAGE, s.before, url);
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

test("one card per title: each download inside with its own actions; tabs count titles", async () => {
  const a = problem({ type: "import", key: "tv/312697", title: "Company Retreat", details: { release: "Show.S01E09-GRP", episodes: 1 } });
  const b = problem({ id: "dddddddddddd", type: "import", key: "tv/312697", title: "Company Retreat", requesters: ["joe"], details: { release: "Show.S01E10-GRP", episodes: 1 } });
  const { $, $$ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [a, b], done: [], admin: false } } });
  assert.equal($$("[data-card]").length, 1);
  assert.equal($$('[data-card="tv/312697"] [data-problem]').length, 2);
  assert.match($('[data-problem="dddddddddddd"] [data-details]').textContent, /S01E10/);
  assert.equal($$('[data-card] [data-act="files"]').length, 2);
  assert.match($("[data-requesters]").textContent, /jane, joe/);
  assert.match($('[data-tab="open"]').textContent, /Open \(1\)/);
});

test("Show files toggles to Hide files, is disabled while loading, opens again", async () => {
  const p = problem({ type: "import", details: { release: "Some.Film.2024", message: "" } });
  let release;
  const files = { token: "t", kind: "movie", files: [{ n: 0, name: "film.mkv", size: 9e9, episodeIds: [], probable: true, rejections: [] }], episodes: [] };
  const { $, $$, calls } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } },
    "/_download-now/manual/files": () => ({ status: 200, body: files }) });
  const btn = $('[data-act="files"]');
  assert.equal(btn.textContent, "Show files");
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
  assert.equal(btn.textContent, "Show files");
  btn.click();
  await settle();
  assert.equal($$("input[data-file]").length, 1);
  assert.equal(calls.filter((c) => c.url.startsWith("/_download-now/manual/files")).length, 2);
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

test("German: from the menu link's lang=de or the user's Seerr language", async () => {
  const body = { status: 200, body: { open: [problem()], done: [], admin: false } };
  let { $ } = await page({ "/_download-now/manual/problems": body }, "https://requests.example.com/_manual/?lang=de");
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
  const p = problem({ type: "wrong-film", details: { release: "Some.Film.1987", releaseYear: 1987, year: 2024 } });
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
    "/_download-now/manual/releases": { status: 200, body: RELEASES } }, "https://requests.example.com/_manual/?lang=de");
  const btn = $('[data-act="releases"]');
  assert.equal(btn.textContent, "Releases zeigen");
  btn.click();
  await settle();
  assert.equal(btn.textContent, "Releases ausblenden");
  btn.click();
  assert.equal(btn.textContent, "Releases zeigen");
});
