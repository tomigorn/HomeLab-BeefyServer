import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { JSDOM } from "jsdom";

const dt = createRequire(import.meta.url)("./details.js");

const YT = "https://www.youtube-nocookie.com";
const v = (key, type, size, lang) => ({ key, name: "N " + key, type, size, lang });
const MOVIE = {
  k: "movie", id: 550, t: { en: "Fight Club", de: "Fight Club DE" }, o: { en: "An insomniac.", de: "" }, y: 1999,
  g: { en: ["Drama", "Thriller"], de: ["Drama", "Thriller"] }, rt: 139, ns: null, cert: "18", p: { en: "/en.jpg", de: "/de.jpg" }, b: "/back.jpg", imdb: "tt0137523",
  sc: { imdb: 8.8, rtc: 81, rta: 96, tmdb: 8.4 },
  cast: Array.from({ length: 12 }, (_, i) => ({ n: "Actor " + i, c: "Role " + i, p: i === 1 ? "https://evil.example.com/x.jpg" : "/p" + i + ".jpg" })),
  v: [v("aaaaaaaaaaa", "Teaser", 2160, "en"), v("bbbbbbbbbbb", "Trailer", 720, "en"), v("ccccccccccc", "Trailer", 1080, "en"), v("ddddddddddd", "Trailer", 1080, "de"), v("bad key", "Trailer", 2160, "en")]
};
const SHOW = { k: "tv", id: 1399, t: { en: "Game of Thrones" }, o: { en: "Seven families." }, y: 2011, g: { en: ["Sci-Fi & Fantasy"] }, rt: 57, ns: 8, cert: "16", p: { en: "/got.jpg" }, b: null, imdb: "javascript:alert(1)", sc: { imdb: null, rtc: 89, rta: 85, tmdb: 8.5 }, cast: [], v: [] };

function dom(html = "", cls = "") {
  return new JSDOM(`<html lang="en" class="${cls}"><head></head><body>${html}</body></html>`, { url: "https://jellyfin.holy-grail.ch/web/#/home" }).window;
}

test("trailers: Trailer type first, then the UI language, then English, larger first; at most 5; only well-formed YouTube keys", () => {
  assert.deepEqual(dt.trailers(MOVIE.v, "de").map((x) => x.key), ["ddddddddddd", "ccccccccccc", "bbbbbbbbbbb", "aaaaaaaaaaa"]);
  assert.deepEqual(dt.trailers(MOVIE.v, "en").map((x) => x.key), ["ccccccccccc", "bbbbbbbbbbb", "ddddddddddd", "aaaaaaaaaaa"]);
  const many = Array.from({ length: 8 }, (_, i) => v("k000000000" + i, "Trailer", i, "en"));
  assert.equal(dt.trailers(many, "en").length, 5);
  assert.deepEqual(dt.trailers(null, "en"), []);
});

test("embed: youtube-nocookie with the JS API and this origin, the key strictly checked", () => {
  const url = dt.embedUrl("ccccccccccc", "https://jellyfin.holy-grail.ch");
  assert.ok(url.startsWith(YT + "/embed/ccccccccccc?"));
  assert.match(url, /enablejsapi=1/);
  assert.match(url, /origin=https%3A%2F%2Fjellyfin\.holy-grail\.ch/);
  assert.equal(dt.embedUrl("../../evil?x", "https://a"), null);
  assert.equal(dt.embedUrl("aaaaaaaaaaaa", "https://a"), null);
});

test("overlay: title, meta, scores, overview with language fallback, cast with photos, links; all text as text", () => {
  const win = dom();
  const d = win.document;
  const evil = Object.assign({}, MOVIE, { t: { en: '<img src=x onerror="alert(1)">' }, cast: [{ n: "<b>x</b>", c: "<i>y</i>", p: "/a.jpg" }] });
  const o = dt.build(d, evil, { lang: "en" });
  assert.equal(o.querySelector("h1").textContent, '<img src=x onerror="alert(1)">');
  assert.equal(o.querySelectorAll("img[onerror], b, i").length, 0);
  const m = dt.build(d, MOVIE, { lang: "de" });
  assert.equal(m.querySelector("h1").textContent, "Fight Club DE");
  assert.equal(m.querySelector(".hssDetailsOverview").textContent, "An insomniac.", "no German overview: English");
  assert.deepEqual([...m.querySelectorAll(".hssDetailsMeta > span")].map((s) => s.textContent), ["1999", "2 Std. 19 Min.", "18", "Drama, Thriller"]);
  assert.deepEqual([...m.querySelectorAll(".hssScore")].map((s) => s.textContent), ["IMDb 8.8", "Rotten Tomatoes 81 %", "Publikum 96 %", "TMDB 8.4"]);
  const cast = [...m.querySelectorAll(".hssCast")];
  assert.equal(cast.length, 10);
  assert.equal(cast[0].querySelector(".hssCastName").textContent, "Actor 0");
  assert.equal(cast[0].querySelector(".hssCastRole").textContent, "Role 0");
  assert.match(cast[0].querySelector(".hssCastPhoto").style.backgroundImage, /image\.tmdb\.org\/t\/p\/w185\/p0\.jpg/);
  assert.equal(cast[1].querySelector(".hssCastPhoto").style.backgroundImage, "", "foreign image URL dropped");
  const links = [...m.querySelectorAll(".hssDetailsLinks a")].map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("target"), a.getAttribute("rel")]);
  assert.deepEqual(links, [["IMDb", "https://www.imdb.com/title/tt0137523/", "_blank", "noopener noreferrer"], ["TMDB", "https://www.themoviedb.org/movie/550", "_blank", "noopener noreferrer"]]);
  assert.match(m.querySelector(".hssDetailsBackdrop").style.backgroundImage, /w1280\/back\.jpg/);
  assert.match(m.querySelector(".hssDetailsPoster").style.backgroundImage, /w342\/de\.jpg/);
  assert.equal(m.getAttribute("role"), "dialog");
  win.close();
});

test("overlay for a series: seasons and episode runtime, Rotten Tomatoes and TMDB, no IMDb link for a bad id", () => {
  const win = dom();
  const s = dt.build(win.document, SHOW, { lang: "en", line: "S01E05 · Wed 4 Nov" });
  assert.deepEqual([...s.querySelectorAll(".hssDetailsMeta > span")].map((x) => x.textContent), ["2011", "8 seasons", "57 min per episode", "16", "Sci-Fi & Fantasy"]);
  assert.deepEqual([...s.querySelectorAll(".hssScore")].map((x) => x.textContent), ["Rotten Tomatoes 89 %", "Audience 85 %", "TMDB 8.5"]);
  assert.deepEqual([...s.querySelectorAll(".hssDetailsLinks a")].map((a) => a.textContent), ["TMDB"]);
  assert.equal(s.querySelector(".hssDetailsLine").textContent, "S01E05 · Wed 4 Nov");
  assert.equal(s.querySelector(".hssDetailsTrailer"), null, "no trailer: no trailer section");
  assert.equal(s.querySelector(".hssDetailsCastRow"), null);
  win.close();
});

test("minimal overlay without details: poster, title, year and the caller's buttons", () => {
  const win = dom();
  const d = win.document;
  const o = dt.build(d, null, { lang: "en", fallback: { title: "Unknown Show", year: 2026, poster: "https://image.tmdb.org/t/p/w342/x.jpg" }, actions: (doc) => { const a = doc.createElement("div"); a.className = "hssTopActions"; a.textContent = "buttons"; return a; } });
  assert.equal(o.querySelector("h1").textContent, "Unknown Show");
  assert.equal(o.querySelector(".hssDetailsMeta > span").textContent, "2026");
  assert.match(o.querySelector(".hssDetailsPoster").style.backgroundImage, /x\.jpg/);
  assert.equal(o.querySelector(".hssDetailsActions .hssTopActions").textContent, "buttons");
  const bad = dt.build(d, null, { lang: "en", fallback: { title: "X", poster: 'javascript:alert(1)"' } });
  assert.equal(bad.querySelector(".hssDetailsPoster").style.backgroundImage, "");
  win.close();
});

function opened(html, cls) {
  const win = dom(html, cls);
  const fetched = [];
  win.ApiClient = { getCurrentUserId: () => "u1", accessToken: () => "tok", getUrl: (p, q) => p + (q ? "?" + new URLSearchParams(q) : "") };
  win.fetch = (url, opts) => {
    fetched.push([url, opts && opts.headers && opts.headers["X-Emby-Token"]]);
    const ok = url.includes("id=good");
    return Promise.resolve({ status: ok ? 200 : 404, json: () => Promise.resolve(ok ? { details: MOVIE } : { error: "unknown" }) });
  };
  const api = dt.start(win);
  const tick = () => new Promise((r) => setTimeout(r, 20));
  const key = (k, code) => win.dispatchEvent(new win.KeyboardEvent("keydown", { key: k, keyCode: code || 0, bubbles: true, cancelable: true }));
  return { win, d: win.document, api, fetched, tick, key };
}

test("open: fetched by id with the user's token, Escape closes it and gives the focus back", async () => {
  const { win, d, api, fetched, tick, key } = opened('<button id="from" data-hss-key="movie/550">card</button>');
  const from = d.getElementById("from");
  from.focus();
  api.open({ id: "good", key: "movie/550", from, fallback: { title: "Fight Club" } });
  assert.equal(win.HssDetails, api);
  await tick();
  assert.deepEqual(fetched, [["hss-popular/details?id=good", "tok"]]);
  assert.equal(d.querySelector(".hssDetails h1").textContent, "Fight Club");
  assert.equal(d.querySelector(".hssDetails .hssDetailsOverview").textContent, "An insomniac.");
  assert.ok(d.getElementById("hss-details-style"));
  key("Escape", 27);
  assert.equal(d.querySelector(".hssDetails"), null);
  assert.equal(d.activeElement, from);
  win.close();
});

test("open: unknown id or a failed fetch keeps the minimal overlay; no id = no fetch", async () => {
  const { win, d, api, fetched, tick } = opened();
  api.open({ id: "nope", fallback: { title: "Nope" } });
  await tick();
  assert.equal(d.querySelector(".hssDetails h1").textContent, "Nope");
  api.open({ id: null, fallback: { title: "Other" } });
  await tick();
  assert.equal(d.querySelectorAll(".hssDetails").length, 1);
  assert.equal(d.querySelector(".hssDetails h1").textContent, "Other");
  assert.equal(fetched.length, 1);
  win.close();
});

test("back keys (Escape, Backspace, TV back) close only the overlay; nothing below sees the key", async () => {
  const { win, d, api, tick, key } = opened();
  let below = 0;
  win.addEventListener("keydown", () => { below++; });
  for (const [k, code] of [["Escape", 27], ["Backspace", 8], ["GoBack", 10009], ["", 461]]) {
    api.open({ id: null, fallback: { title: "X" } });
    await tick();
    key(k, code);
    assert.equal(d.querySelector(".hssDetails"), null, k + code);
  }
  assert.equal(below, 0);
  key("Escape", 27);
  assert.equal(below, 1, "closed: keys go through again");
  win.close();
});

test("a route change closes the overlay", async () => {
  const { win, d, api, tick } = opened();
  api.open({ id: null, fallback: { title: "X" } });
  await tick();
  win.history.pushState({}, "", "#/details?id=1");
  await new Promise((r) => setTimeout(r, 600));
  assert.equal(d.querySelector(".hssDetails"), null);
  win.close();
});

test("focus returns to the card with the same key when the page was rebuilt meanwhile", async () => {
  const { win, d, api, tick, key } = opened('<div class="page"><button data-hss-key="movie/550">old</button></div>');
  const old = d.querySelector("[data-hss-key]");
  api.open({ id: null, key: "movie/550", from: old, fallback: { title: "X" } });
  await tick();
  d.querySelector(".page").innerHTML = '<button data-hss-key="movie/9">other</button><button data-hss-key="movie/550">new</button>';
  key("Escape", 27);
  assert.equal(d.activeElement.textContent, "new");
  win.close();
});

test("TV layout: focus moves into the overlay, which is a focus container", async () => {
  const { win, d, api, tick } = opened("", "layout-tv");
  api.open({ id: "good", fallback: { title: "X" }, actions: (doc) => { const a = doc.createElement("div"); a.className = "hssTopActions"; a.innerHTML = '<button class="hssTopBtn">n</button>'; return a; } });
  await tick();
  const o = d.querySelector(".hssDetails");
  assert.ok(o.classList.contains("focuscontainer"));
  assert.ok(o.contains(d.activeElement));
  assert.ok(d.activeElement.classList.contains("hssTopBtn"));
  assert.ok(o.querySelector(".hssCastRow").classList.contains("focuscontainer-x"));
  assert.equal(o.querySelectorAll("button.hssCast").length, 10, "cast cards are focusable so the row scrolls on a TV");
  win.close();
});

test("refresh rebuilds only the caller's buttons", async () => {
  const { win, d, api, tick } = opened();
  let n = 0;
  api.open({ id: "good", fallback: { title: "X" }, actions: (doc) => { const a = doc.createElement("div"); a.className = "hssTopActions"; a.textContent = "v" + (++n); return a; } });
  await tick();
  api.refresh();
  assert.equal(d.querySelector(".hssDetailsActions").textContent, "v" + n);
  assert.equal(d.querySelectorAll(".hssDetailsActions .hssTopActions").length, 1);
  win.close();
});

test("trailer: click to play (thumbnail first, no iframe), a player error tries the next trailer, all failing shows the YouTube link", async () => {
  const { win, d, api, tick } = opened();
  api.open({ id: "good", fallback: { title: "X" } });
  await tick();
  const box = d.querySelector(".hssDetailsTrailer");
  assert.equal(box.querySelector("iframe"), null, "no autoplay, nothing loaded from YouTube before the click");
  assert.match(box.querySelector(".hssTrailerThumb").style.backgroundImage, /image\.tmdb\.org\/t\/p\/w780\/back\.jpg/);
  box.querySelector(".hssTrailerPlay").click();
  const frame = () => box.querySelector("iframe");
  assert.match(frame().getAttribute("src"), /\/embed\/ccccccccccc\?/);
  assert.equal(frame().getAttribute("referrerpolicy"), "strict-origin-when-cross-origin");
  const err = (code, origin = YT, source = frame().contentWindow) => win.dispatchEvent(new win.MessageEvent("message", { data: JSON.stringify({ event: "onError", info: code }), origin, source }));
  err(150, "https://evil.example.com");
  assert.match(frame().getAttribute("src"), /ccccccccccc/, "messages from other origins are ignored");
  err(150);
  assert.match(frame().getAttribute("src"), /bbbbbbbbbbb/);
  err(101);
  assert.match(frame().getAttribute("src"), /ddddddddddd/);
  err(5);
  assert.match(frame().getAttribute("src"), /ddddddddddd/, "other errors keep the player");
  err(100);
  assert.match(frame().getAttribute("src"), /aaaaaaaaaaa/);
  err(150);
  assert.equal(frame(), null);
  assert.ok(box.querySelector(".hssTrailerThumb"));
  const yt = box.querySelector("a.hssTrailerYoutube");
  assert.equal(yt.getAttribute("href"), "https://www.youtube.com/watch?v=ccccccccccc");
  assert.equal(yt.getAttribute("target"), "_blank");
  assert.equal(yt.getAttribute("rel"), "noopener noreferrer");
  win.close();
});

test("phone: one column", () => {
  assert.match(dt.CSS, /@media \(max-width: 700px\) \{[^\n]*\.hssDetailsMain \{[^}]*grid-template-columns: 1fr/);
});

test("the source has no characters outside the BMP (the injector config drops them)", async () => {
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("./details.js", import.meta.url), "utf8");
  assert.equal(/[\u{10000}-\u{10FFFF}]/u.test(src), false);
  assert.doesNotMatch(src, /innerHTML\s*=\s*[^'"]/, "innerHTML only for fixed markup");
});

const pt = dt.playTv;
const SESSIONS = [
  { Id: "sOwn", UserId: "u1", DeviceId: "dev-browser", DeviceName: "Firefox", Client: "Jellyfin Web", SupportsRemoteControl: true, IsActive: true },
  { Id: "sAtv", UserId: "u1", DeviceId: "dev-atv", DeviceName: "Apple TV", Client: "Moonfin", SupportsRemoteControl: true, IsActive: true },
  { Id: "sSony", UserId: "u1", DeviceId: "dev-sony", DeviceName: "Sony BRAVIA", Client: "Moonfin", SupportsRemoteControl: true, IsActive: true, NowPlayingItem: { Name: "Ice Age" } },
  { Id: "sPhone", UserId: "u1", DeviceId: "dev-phone", DeviceName: "Pixel", Client: "Jellyfin Android", SupportsRemoteControl: false, IsActive: true },
  { Id: "sOld", UserId: "u1", DeviceId: "dev-old", DeviceName: "Old TV", Client: "Moonfin", SupportsRemoteControl: true, IsActive: false },
  { Id: "sOther", UserId: "u2", DeviceId: "dev-other", DeviceName: "Samsung TV", Client: "Moonfin", SupportsRemoteControl: true, IsActive: true, NowPlayingItem: { Name: "Secret" } }
];

test("play on TV: button in English and German with the item id and type; none without an id", () => {
  const d = dom().document;
  const b = pt.button(d, { id: "jf121", type: "Movie", name: "Ikiru" }, "en");
  assert.equal(b.tagName, "BUTTON");
  assert.equal(b.getAttribute("type"), "button");
  assert.equal(b.textContent, "▶ Play on TV");
  assert.equal(b.getAttribute("data-hss-play"), "jf121");
  assert.equal(b.getAttribute("data-hss-play-type"), "Movie");
  assert.equal(b.getAttribute("aria-label"), "Ikiru: Play on TV");
  assert.equal(pt.button(d, { id: "jf9", type: "Series" }, "de-CH").textContent, "▶ Auf TV abspielen");
  assert.equal(pt.button(d, null, "en"), null);
  assert.equal(pt.button(d, { type: "Movie" }, "en"), null);
  assert.ok(d.getElementById("hss-details-style"), "styles come with the button");
  assert.match(dt.CSS, /\.layout-tv \.hssPlayTv \{ display: none !important; \}/);
});

test("play on TV: only the user's own other active sessions that take remote control, labelled device · client", () => {
  const t = pt.targets(SESSIONS, "dev-browser", "u1");
  assert.deepEqual(t.map((s) => s.Id), ["sAtv", "sSony"]);
  assert.deepEqual(t.map(pt.label), ["Apple TV · Moonfin", "Sony BRAVIA · Moonfin"]);
  assert.deepEqual(pt.targets(null, "x", "u1"), []);
  assert.deepEqual(pt.targets(SESSIONS, "dev-browser", "u2").map((s) => s.Id), ["sOther"], "another user's TVs are never listed for u1");
});

function playWin(cls, routes, before) {
  const win = dom('<div id="card"></div>', cls);
  const calls = [];
  win.ApiClient = { getCurrentUserId: () => "u1", accessToken: () => "tok", deviceId: () => "dev-browser", getUrl: (p, q) => p + (q ? "?" + new URLSearchParams(q) : "") };
  win.fetch = (url, opts) => {
    calls.push([(opts && opts.method) || "GET", url, opts && opts.headers && opts.headers["X-Emby-Token"]]);
    const hit = Object.keys(routes).find((k) => url.startsWith(k));
    const body = hit ? routes[hit] : null;
    const status = body === undefined || body === null ? 404 : typeof body === "number" ? body : 200;
    return Promise.resolve({ status, json: () => Promise.resolve(body) });
  };
  if (before) before(win);
  dt.start(win);
  const tick = () => new Promise((r) => setTimeout(r, 20));
  return { win, d: win.document, calls, tick };
}

const MOVIE_ROUTES = { "Sessions?": SESSIONS, "Users/u1/Items/jf121": { Id: "jf121", UserData: { PlaybackPositionTicks: 4200000000 } }, "Sessions/sAtv/Playing": 204 };

test("play on TV: the button lists the TVs, picking one starts the film where the user stopped, then confirms", async () => {
  const { win, d, calls, tick } = playWin("", MOVIE_ROUTES);
  const b = pt.button(d, { id: "jf121", type: "Movie", name: "Ikiru" }, "en");
  d.getElementById("card").appendChild(b);
  let below = 0;
  d.addEventListener("click", () => below++);
  b.click();
  assert.equal(below, 0, "nothing below sees the click (no card link followed)");
  assert.ok(d.querySelector(".hssPlayMenu"));
  await tick();
  assert.deepEqual(calls[0], ["GET", "Sessions?ControllableByUserId=u1", "tok"]);
  const opts = [...d.querySelectorAll(".hssPlayMenu .hssPlayTarget")];
  assert.deepEqual(opts.map((o) => o.querySelector(".hssPlayTargetName").textContent), ["Apple TV · Moonfin", "Sony BRAVIA · Moonfin"]);
  assert.equal(opts[1].querySelector(".hssPlayTargetSub").textContent, "now playing: Ice Age");
  assert.equal(d.activeElement, opts[0]);
  opts[0].click();
  await tick();
  assert.deepEqual(calls.slice(1), [
    ["GET", "Users/u1/Items/jf121", "tok"],
    ["POST", "Sessions/sAtv/Playing?playCommand=PlayNow&itemIds=jf121&startPositionTicks=4200000000", "tok"]
  ]);
  assert.equal(d.querySelector(".hssPlayMenuStatus").textContent, "Playing on Apple TV · Moonfin");
  win.close();
});

test("play on TV: a series resumes its in-progress episode, else Next Up, else the first episode", async () => {
  const resume = playWin("", { "Sessions?": SESSIONS, "Users/u1/Items/Resume": { Items: [{ Id: "ep5", UserData: { PlaybackPositionTicks: 900 } }] }, "Sessions/sAtv/Playing": 204 });
  let b = pt.button(resume.d, { id: "jfShow", type: "Series" }, "en");
  resume.d.body.appendChild(b);
  b.click();
  await resume.tick();
  resume.d.querySelector(".hssPlayTarget").click();
  await resume.tick();
  assert.deepEqual(resume.calls.map((c) => c[1]).slice(1), ["Users/u1/Items/Resume?ParentId=jfShow&Limit=1&IncludeItemTypes=Episode&Recursive=true", "Sessions/sAtv/Playing?playCommand=PlayNow&itemIds=ep5&startPositionTicks=900"]);
  resume.win.close();
  const next = playWin("", { "Sessions?": SESSIONS, "Users/u1/Items/Resume": { Items: [] }, "Shows/NextUp": { Items: [{ Id: "ep6" }] }, "Sessions/sAtv/Playing": 204 });
  b = pt.button(next.d, { id: "jfShow", type: "Series" }, "en");
  next.d.body.appendChild(b);
  b.click();
  await next.tick();
  next.d.querySelector(".hssPlayTarget").click();
  await next.tick();
  assert.deepEqual(next.calls.map((c) => c[1]).slice(2), ["Shows/NextUp?SeriesId=jfShow&UserId=u1&Limit=1", "Sessions/sAtv/Playing?playCommand=PlayNow&itemIds=ep6"]);
  next.win.close();
  const firstEp = playWin("", { "Sessions?": SESSIONS, "Users/u1/Items/Resume": { Items: [] }, "Shows/NextUp": { Items: [] }, "Shows/jfShow/Episodes": { Items: [{ Id: "sp1", ParentIndexNumber: 0 }, { Id: "ep1", ParentIndexNumber: 1 }] }, "Sessions/sAtv/Playing": 204 });
  b = pt.button(firstEp.d, { id: "jfShow", type: "Series" }, "en");
  firstEp.d.body.appendChild(b);
  b.click();
  await firstEp.tick();
  firstEp.d.querySelector(".hssPlayTarget").click();
  await firstEp.tick();
  assert.deepEqual(firstEp.calls.map((c) => c[1]).slice(3), ["Shows/jfShow/Episodes?UserId=u1&Limit=50&IsMissing=false", "Sessions/sAtv/Playing?playCommand=PlayNow&itemIds=ep1"]);
  firstEp.win.close();
});

test("play on TV: no TV online = the hint to open Moonfin first (German too); a failed start says so", async () => {
  const none = playWin("", { "Sessions?": [SESSIONS[0], SESSIONS[3]] });
  none.d.documentElement.lang = "de";
  const b = pt.button(none.d, { id: "jf121", type: "Movie" }, "de");
  none.d.body.appendChild(b);
  b.click();
  await none.tick();
  assert.equal(none.d.querySelectorAll(".hssPlayTarget").length, 0);
  assert.equal(none.d.querySelector(".hssPlayMenuStatus").textContent, "Kein TV online. Zuerst Moonfin auf dem TV öffnen.");
  none.win.close();
  const en = playWin("", { "Sessions?": [] });
  const e = pt.button(en.d, { id: "jf121", type: "Movie" }, "en");
  en.d.body.appendChild(e);
  e.click();
  await en.tick();
  assert.equal(en.d.querySelector(".hssPlayMenuStatus").textContent, "No TV online. Open Moonfin on the TV first.");
  en.win.close();
  const fail = playWin("", { "Sessions?": SESSIONS, "Users/u1/Items/jf121": { Id: "jf121" }, "Sessions/sAtv/Playing": 500 });
  const f = pt.button(fail.d, { id: "jf121", type: "Movie" }, "en");
  fail.d.body.appendChild(f);
  f.click();
  await fail.tick();
  fail.d.querySelector(".hssPlayTarget").click();
  await fail.tick();
  assert.equal(fail.calls[2][1], "Sessions/sAtv/Playing?playCommand=PlayNow&itemIds=jf121", "no resume point = from the start");
  assert.equal(fail.d.querySelector(".hssPlayMenuStatus").textContent, "Could not start playback on Apple TV · Moonfin");
  assert.equal(fail.d.querySelector(".hssPlayTarget").disabled, false, "can try again");
  fail.win.close();
});

test("play on TV: Escape closes only the menu and gives the focus back; a click elsewhere closes it", async () => {
  const { win, d, tick } = playWin("", MOVIE_ROUTES);
  const api = win.HssDetails;
  api.open({ fallback: { title: "X" }, play: { id: "jf121", type: "Movie" } });
  const b = d.querySelector(".hssDetails .hssDetailsActions .hssPlayTv");
  assert.ok(b, "the overlay shows the button for an owned title");
  b.focus();
  b.click();
  await tick();
  win.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", keyCode: 27, bubbles: true, cancelable: true }));
  assert.equal(d.querySelector(".hssPlayMenu"), null);
  assert.ok(d.querySelector(".hssDetails"), "the overlay stays");
  assert.equal(d.activeElement, b);
  b.click();
  await tick();
  d.querySelector(".hssDetailsTitle").click();
  assert.equal(d.querySelector(".hssPlayMenu"), null);
  win.close();
});

test("play on TV: the overlay shows no button without an owned title or in the TV layout", () => {
  const d = dom().document;
  assert.equal(dt.build(d, MOVIE, { lang: "en" }).querySelector(".hssPlayTv"), null);
  assert.ok(dt.build(d, MOVIE, { lang: "en", play: { id: "jf1", type: "Movie" } }).querySelector(".hssDetailsActions .hssPlayTv"));
  const tv = dom("", "layout-tv").document;
  assert.equal(dt.build(tv, MOVIE, { lang: "en", play: { id: "jf1", type: "Movie" } }).querySelector(".hssPlayTv"), null);
});

test("play on TV: a second press closes the menu, a route change without hashchange closes it, Backspace in a field is left alone", async () => {
  const { win, d, tick } = playWin("", MOVIE_ROUTES);
  const b = pt.button(d, { id: "jf121", type: "Movie" }, "en");
  d.body.appendChild(b);
  b.click();
  await tick();
  b.click();
  assert.equal(d.querySelector(".hssPlayMenu"), null);
  b.click();
  await tick();
  const input = d.createElement("input");
  d.body.appendChild(input);
  input.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Backspace", keyCode: 8, bubbles: true, cancelable: true }));
  assert.ok(d.querySelector(".hssPlayMenu"));
  win.history.pushState({}, "", "#/details?id=x");
  await new Promise((r) => setTimeout(r, 300));
  assert.equal(d.querySelector(".hssPlayMenu"), null);
  win.close();
});

test("play on TV on a Popular page (popular.js first, as served): the page stays open, Escape closes only the menu", async () => {
  const pp = createRequire(import.meta.url)("./popular.js");
  const { win, d, tick } = playWin("", MOVIE_ROUTES, (w) => pp.start(w));
  const page = d.createElement("div");
  page.className = "hssPopularPage";
  const a = d.createElement("a");
  a.className = "hssTopCard";
  a.setAttribute("href", "#/details?id=jf121");
  page.appendChild(a);
  d.body.appendChild(page);
  const b = pt.button(d, { id: "jf121", type: "Movie" }, "en");
  a.appendChild(b);
  b.click();
  await tick();
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(d.querySelector(".hssPopularPage"), "page still open after the click");
  assert.ok(d.querySelector(".hssPlayMenu"));
  win.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", keyCode: 27, bubbles: true, cancelable: true }));
  assert.equal(d.querySelector(".hssPlayMenu"), null);
  assert.ok(d.querySelector(".hssPopularPage"), "Escape closed only the menu");
  win.close();
});
