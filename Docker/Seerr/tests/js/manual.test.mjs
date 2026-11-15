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
  for (const code of ["id-match", "no-eligible", "not-upgrade", "sample", "unparsable", "not-in-release", "wrong-film", "dub-only", "failed", "no-release", "removed", "unmatched", "unknown"]) {
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
  assert.match($("[data-list] [data-virus]").textContent, /^✅ Virus scan: clean/);
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

test("too big for the full virus scan: parts scan shown as it is", async () => {
  const cases = [[{ state: "parts-clean", minutes: 34, parts: 1 }, "⚪ Too big for the full virus scan · ✅ attachments and subtitles scanned clean · ✅ genuine video", "✅ parts scanned clean",
    "⚪ Zu gross für den vollen Virenscan · ✅ Anhänge und Untertitel sauber · ✅ echtes Video", "✅ Teile sauber"],
    [{ state: "parts-error", why: "no video stream" }, "⚪ Too big for the full virus scan · ⚠️ parts scan not possible (no video stream): admins only", "⚠️ only partly scannable",
    "⚪ Zu gross für den vollen Virenscan · ⚠️ Teil-Scan nicht möglich (no video stream): nur Admins", "⚠️ nur teilweise prüfbar"]];
  for (const [virus, full, short, fullDe, shortDe] of cases) {
    const p = e09({ details: { reason: "id-match", release: E09, message: BY_ID, virus } });
    const files = { token: "t", kind: "tv", files: [{ n: 0, name: E09 + ".mkv", size: 2.48e9, episodeIds: [2045], probable: true, rejections: [], virus }], episodes: [{ id: 2045, label: "S01E09", title: "" }] };
    for (const [body, f, sh] of [[PAGE, full, short], [PAGE.replace(">Series<", ">Serien<"), fullDe, shortDe]]) {
      const { $ } = await page({ "/_download-now/manual/problems": { status: 200, body: { open: [p], done: [], admin: false } }, "/_download-now/manual/files": { status: 200, body: files } }, undefined, body);
      assert.equal($("[data-summary]").textContent, sh);
      $('[data-act="files"]').click();
      await settle();
      assert.equal($("[data-list] [data-virus]").textContent, f);
    }
  }
});
