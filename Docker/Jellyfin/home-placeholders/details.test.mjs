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
