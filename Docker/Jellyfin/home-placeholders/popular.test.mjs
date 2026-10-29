import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { JSDOM } from "jsdom";

const pp = createRequire(import.meta.url)("./popular.js");

const OWNED = { k: "movie", id: 121, s: null, r: 1, y: 2003, t: { en: "The Return of the King", de: "Die Rückkehr des Königs" }, p: { en: "/rotk.jpg", de: "/rotk-de.jpg" }, o: true, f: null };
const FOUND = { k: "tv", id: 202, s: 4, r: 2, y: 2023, t: { en: "The Diplomat", de: "Diplomatische Beziehungen" }, p: { en: "/d.jpg", de: "/d-de.jpg" }, o: false, f: "2160p", x: "tokTV" };
const HIDDEN = { k: "movie", id: 303, s: null, r: 3, y: 2020, t: { en: "Owned elsewhere", de: "x" }, p: { en: "/x.jpg" }, o: true, f: null };
const NONE = { k: "movie", id: 404, s: null, r: 4, y: 2026, t: { en: "No Release", de: "Kein Release" }, p: { en: "/n.jpg" }, o: false, f: null, n: true, c: "2026-10-21" };
const UNKNOWN = { k: "tv", id: null, s: null, r: 5, y: null, t: { en: "Instadocs", de: "Instadocs" }, p: { en: null, de: null }, o: false, f: null, n: true, c: null, u: true };
const DATA = { week: "2026-10-18", home: { shows: [FOUND], movies: [OWNED, HIDDEN] }, rows: [
  { id: "service-8", type: "service", name: { en: "Netflix", de: "Netflix" }, logo: "/nf.png", items: [OWNED, FOUND] },
  { id: "genre-horror", type: "genre", name: { en: "Horror", de: "Horror" }, logo: null, items: [HIDDEN] }
] };
const LIB = pp.libraryMap([
  { Type: "Movie", Id: "jf121", ServerId: "srv", ProviderIds: { Tmdb: "121" } },
  { Type: "Series", Id: "jf999", ServerId: "srv", ProviderIds: { Tmdb: "999" } },
  { Type: "Movie", Id: "nope", ServerId: "srv", ProviderIds: {} }
]);

function doc(html = "") {
  return new JSDOM(`<html><body>${html}</body></html>`, { url: "https://jellyfin.holy-grail.ch/web/" }).window.document;
}

test("library map by TMDB id and type", () => {
  assert.deepEqual(LIB, { "movie/121": { id: "jf121", server: "srv" }, "tv/999": { id: "jf999", server: "srv" } });
});

test("a row shows titles the user has or that can be requested, nothing else", () => {
  assert.deepEqual(pp.visible([OWNED, FOUND, HIDDEN, NONE, UNKNOWN], LIB).map((e) => [e.item.r, e.jf && e.jf.id]), [[1, "jf121"], [2, null], [4, null], [5, null]]);
});

test("owned card links to the Jellyfin item, big outlined rank, German title", () => {
  const d = doc();
  const card = pp.card(d, { item: OWNED, jf: LIB["movie/121"] }, "de", false, null);
  assert.equal(card.tagName, "A");
  assert.equal(card.getAttribute("href"), "#/details?id=jf121&serverId=srv");
  assert.equal(card.querySelector(".hssTopRank").textContent, "1");
  assert.equal(card.querySelector(".hssTopRank").getAttribute("aria-hidden"), "true");
  assert.equal(card.getAttribute("aria-label"), "1. Die Rückkehr des Königs");
  assert.match(card.querySelector(".hssTopPoster").style.backgroundImage, /image\.tmdb\.org\/t\/p\/w342\/rotk-de\.jpg/);
  assert.equal(card.querySelector("button"), null);
  assert.ok(!card.classList.contains("hssTopDim"));
});

test("requestable card is dimmed and has the two buttons with the entry id", () => {
  const d = doc();
  const card = pp.card(d, { item: FOUND, jf: null }, "en", false, null);
  assert.ok(card.classList.contains("hssTopDim"));
  const b = [...card.querySelectorAll("button[data-hss-x]")];
  assert.deepEqual(b.map((x) => [x.getAttribute("data-hss-x"), x.getAttribute("data-hss-mode")]), [["tokTV", "night"], ["tokTV", "now"]]);
  assert.match(b[0].textContent, /🌙/);
  assert.match(b[1].textContent, /⚡/);
  assert.equal(card.querySelector(".hssTopBadge").textContent, "2160p");
  assert.match(card.querySelector(".hssTopBadge").getAttribute("title"), /available/);
  assert.match(card.querySelector(".hssTopSeason").textContent, /S4/);
});

test("card state: requested shows the badge, limit reached disables the buttons", () => {
  const d = doc();
  const c1 = pp.card(d, { item: FOUND, jf: null }, "en", false, { entries: { tokTV: { state: "requested", badge: "night" } }, left: 5 });
  assert.equal(c1.querySelector("button"), null);
  assert.match(c1.querySelector(".hssTopState").textContent, /🌙/);
  const c2 = pp.card(d, { item: FOUND, jf: null }, "de", false, { entries: { tokTV: { state: "requestable" } }, left: 0, limit: 10, days: 7 });
  assert.ok([...c2.querySelectorAll("button")].every((b) => b.disabled));
  assert.match(c2.querySelector(".hssTopHint").textContent, /10.*7/);
  const c3 = pp.card(d, { item: FOUND, jf: null }, "en", false, { entries: { tokTV: { state: "owned" } }, left: 3 });
  assert.equal(c3.querySelector("button"), null);
});

test("no release yet: greyed out, badge with the check date, buttons disabled and without an entry id", () => {
  const d = doc();
  const c = pp.card(d, { item: NONE, jf: null }, "en", false, { entries: {}, left: 5 });
  assert.ok(c.classList.contains("hssTopNone"));
  assert.equal(c.querySelector(".hssTopBadge").textContent, "No release yet");
  assert.match(c.querySelector(".hssTopHint").textContent, /checked 21 Oct · rechecked daily/);
  const b = [...c.querySelectorAll("button")];
  assert.equal(b.length, 2);
  assert.ok(b.every((x) => x.disabled && !x.hasAttribute("data-hss-x")));
  const de = pp.card(d, { item: NONE, jf: null }, "de", false, null);
  assert.equal(de.querySelector(".hssTopBadge").textContent, "Noch kein Release");
  assert.match(de.querySelector(".hssTopHint").textContent, /geprüft 21\. Okt\. · täglich neu geprüft/);
  const u = pp.card(d, { item: UNKNOWN, jf: null }, "en", false, null);
  assert.match(u.querySelector(".hssTopHint").textContent, /not identified/);
  assert.equal(u.querySelector(".hssTopNoPoster").textContent, "Instadocs");
});

test("two-digit ranks get their own width so the poster does not hide the 0", () => {
  const d = doc();
  assert.ok(pp.card(d, { item: { ...OWNED, r: 10 }, jf: LIB["movie/121"] }, "en", false, null).querySelector(".hssTopRank").classList.contains("hssTopRank-2"));
  assert.ok(!pp.card(d, { item: OWNED, jf: LIB["movie/121"] }, "en", false, null).querySelector(".hssTopRank").classList.contains("hssTopRank-2"));
});

test("ten cards fit the row width from 1280 px up, below that the row scrolls", () => {
  const p = pp.fitPoster(1800, 14, 1920);
  assert.ok(p > 100 && p < 120, String(p));
  assert.ok(15.3 * p + 9 * 14 <= 1800);
  assert.ok(pp.fitPoster(2400, 14, 2560) > p);
  assert.equal(pp.fitPoster(1100, 14, 1200), null);
  assert.equal(pp.fitPoster(1200, 14, 1280), null, "posters under 90 px: scroll instead");
  assert.equal(pp.fitPoster(0, 14, 1920), null);
});

test("poster paths are checked before they reach CSS", () => {
  assert.equal(pp.posterUrl({ p: { en: "/a b\").jpg" } }, "en"), null);
  assert.equal(pp.posterUrl({ p: { en: "/ok_1-2.jpg" } }, "en"), "https://image.tmdb.org/t/p/w342/ok_1-2.jpg");
  assert.equal(pp.posterUrl({ p: null }, "en"), null);
});

test("home rows: Top 10 sections built inside the home container, empty ones removed", () => {
  const d = doc('<div class="homeSectionsContainer"><div class="verticalSection RecentlyAddedShows" data-page="1" style="order: 4"></div></div>');
  const c = d.querySelector(".homeSectionsContainer");
  pp.syncHome(c, DATA, LIB, "en", null);
  const shows = c.querySelector(".verticalSection.Top10Shows[data-hss-client]");
  assert.equal(shows.querySelector("h2").textContent, "Top 10 Shows in Switzerland");
  assert.equal(shows.querySelectorAll(".hssTopScroll > .hssTopCard").length, 1);
  assert.equal(c.querySelectorAll(".itemsContainer").length, 0, "Jellyfin calls pause() on every .itemsContainer of the home tab");
  assert.equal(c.querySelectorAll(".verticalSection.Top10Movies .hssTopCard").length, 1);
  const key = shows.getAttribute("data-hss-client");
  pp.syncHome(c, DATA, LIB, "en", null);
  assert.equal(c.querySelector(".verticalSection.Top10Shows"), shows, "unchanged data keeps the element");
  assert.equal(shows.getAttribute("data-hss-client"), key);
  pp.syncHome(c, { home: { shows: [], movies: [] }, rows: [] }, LIB, "de", null);
  assert.equal(c.querySelector(".verticalSection.Top10Shows"), null);
});

test("extra rows are registered for the home layout only when there is data", () => {
  assert.deepEqual(pp.extraRows(DATA).map((r) => [r.id, r.after, r.name.de]), [["Top10Shows", "RecentlyAddedShows", "Top 10 Serien in der Schweiz"], ["Top10Movies", "RecentlyWatchedSeries", "Top 10 Filme in der Schweiz"]]);
  assert.deepEqual(pp.extraRows({ home: { shows: [], movies: [OWNED] } }).map((r) => r.id), ["Top10Movies"]);
  assert.deepEqual(pp.extraRows(undefined), []);
});

test("nav entries in the top bar and the drawer, once", () => {
  const d = doc('<div class="MuiStack-root"><a href="#/">Holy Grail</a><a class="MuiButton-root x" href="#/tv?topParentId=1&amp;collectionType=tvshows"><span class="MuiButton-startIcon"><svg><path d="M0"></path></svg></span>Shows</a></div>' +
    '<div class="mainDrawer"><div class="customMenuOptions"></div></div>');
  pp.navLinks(d, "de");
  pp.navLinks(d, "de");
  const top = d.querySelectorAll(".hssPopularNav");
  assert.equal(top.length, 2);
  const bar = d.querySelector(".MuiStack-root .hssPopularNav");
  assert.equal(bar.textContent, "Gerade beliebt");
  assert.equal(bar.className, "MuiButton-root x hssPopularNav");
  assert.equal(bar.previousElementSibling.textContent, "Shows");
  const drawer = d.querySelector(".customMenuOptions .hssPopularNav");
  assert.match(drawer.className, /navMenuOption/);
  assert.match(drawer.textContent, /Gerade beliebt/);
});

test("phone layout: an entry in the app drawer after Favourites", () => {
  const d = doc('<div class="MuiDrawer-root"><div class="MuiDrawer-paper"><ul><li class="it"><a class="MuiListItemButton-root" href="#/home"><span class="MuiListItemText-primary">Home</span></a></li>' +
    '<li class="it"><a class="MuiListItemButton-root Mui-selected" href="#/home?tab=1"><svg><path d="M1"></path></svg><span class="MuiListItemText-primary">Favourites</span></a></li></ul></div></div>');
  pp.navLinks(d, "de");
  pp.navLinks(d, "de");
  const items = d.querySelectorAll(".MuiDrawer-paper .hssPopularNav");
  assert.equal(items.length, 1);
  assert.equal(items[0].querySelector(".MuiListItemText-primary").textContent, "Gerade beliebt");
  assert.equal(items[0].getAttribute("href"), "#/home");
  assert.ok(!items[0].classList.contains("Mui-selected"));
  assert.equal(items[0].querySelector("path").getAttribute("d").slice(0, 4), "M16 ");
  assert.equal(items[0].closest("li").previousElementSibling.textContent, "Favourites");
});

test("TV layout: a header button next to search (TV has no drawer button)", () => {
  const d = doc('<div class="skinHeader"><div class="headerRight"><button class="headerSearchButton"></button></div></div>');
  pp.navLinks(d, "en");
  assert.equal(d.querySelector(".headerRight .hssPopularNav"), null);
  d.documentElement.classList.add("layout-tv");
  pp.navLinks(d, "en");
  pp.navLinks(d, "en");
  const b = d.querySelectorAll(".headerRight .hssPopularNav");
  assert.equal(b.length, 1);
  assert.equal(b[0].tagName, "BUTTON");
  assert.equal(b[0].getAttribute("title"), "Currently popular");
  assert.equal(b[0].nextElementSibling.className, "headerSearchButton");
});

test("the page lists the Netflix rows, every service and genre row with something to show", () => {
  const d = doc();
  const page = pp.buildPage(d, DATA, LIB, "en", false, null);
  assert.deepEqual([...page.querySelectorAll(".hssTopRow h2")].map((h) => h.textContent), ["Top 10 Shows in Switzerland", "Top 10 Movies in Switzerland", "Netflix"]);
  assert.match(page.querySelector(".hssTopRow img.hssTopLogo").getAttribute("src"), /w92\/nf\.png$/);
  assert.equal(page.querySelector("h1").textContent, "Currently popular");
  assert.match(page.querySelector(".hssPopularSource").textContent, /Netflix.*TMDB.*JustWatch/);
});

test("request posts only the entry id and mode with the user's token", async () => {
  const calls = [];
  const win = { fetch: async (url, opts) => { calls.push([url, opts]); return { status: 200, json: async () => ({ ok: true, state: "requested", left: 9 }) }; } };
  const api = { accessToken: () => "T".repeat(32), getUrl: (p) => "https://jellyfin.holy-grail.ch/" + p };
  const r = await pp.request(win, api, "tokTV", "now");
  assert.deepEqual(r, { status: 200, body: { ok: true, state: "requested", left: 9 } });
  assert.equal(calls[0][0], "https://jellyfin.holy-grail.ch/hss-popular/request");
  assert.equal(calls[0][1].method, "POST");
  assert.equal(calls[0][1].headers["Content-Type"], "application/json");
  assert.equal(calls[0][1].headers["X-Emby-Token"], "T".repeat(32));
  assert.deepEqual(JSON.parse(calls[0][1].body), { id: "tokTV", mode: "now" });
});

test("fitting the rows measures every row before it changes any (one layout, not one per row)", () => {
  const d = doc('<div class="page"><div class="hssTopScroll"></div><div class="hssTopScroll"></div><div class="hssTopScroll"></div></div>');
  const win = d.defaultView;
  const log = [];
  Object.defineProperty(win, "innerWidth", { value: 1920, configurable: true });
  win.getComputedStyle = () => { log.push("read"); return { paddingLeft: "0", paddingRight: "0", columnGap: "14" }; };
  for (const l of d.querySelectorAll(".hssTopScroll")) {
    Object.defineProperty(l, "clientWidth", { get: () => { log.push("read"); return 1800; } });
    const set = l.style.setProperty.bind(l.style);
    l.style.setProperty = (k, v) => { log.push("write"); set(k, v); };
  }
  pp.fitAll(d.querySelector(".page"));
  assert.equal(log.filter((x) => x === "write").length, 3);
  assert.equal(log.lastIndexOf("read") < log.indexOf("write"), true, log.join(","));
  assert.match(d.querySelector(".hssTopScroll").style.getPropertyValue("--hss-p"), /^1\d\dpx$/);
});

function started() {
  const dom = new JSDOM('<html lang="en"><body><header><a class="logo" href="#/">Holy Grail</a><a class="hssPopularNav" href="#/home">Currently popular</a></header></body></html>', { url: "https://jellyfin.holy-grail.ch/web/#/home" });
  const win = dom.window;
  let stateCalls = 0;
  win.HssPopular = DATA;
  win.ApiClient = {
    getCurrentUserId: () => "u1", accessToken: () => "tok", getUrl: (p) => p,
    getJSON: () => Promise.resolve({ Items: [{ Type: "Movie", Id: "jf121", ServerId: "srv", ProviderIds: { Tmdb: "121" } }] })
  };
  win.fetch = () => { stateCalls++; return Promise.resolve({ status: 200, json: () => Promise.resolve({ entries: {}, left: 5, limit: 5, days: 7 }) }); };
  win.document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (a && !e.defaultPrevented) { e.preventDefault(); win.history.pushState({}, "", "#/home"); }
  });
  pp.start(win);
  const tick = () => new Promise((r) => setTimeout(r, 20));
  const click = (el) => el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  return { win, d: win.document, tick, click, calls: () => stateCalls };
}

test("the logo (or any nav link) closes the page even when the route stays the same", async () => {
  const { win, d, tick, click } = started();
  click(d.querySelector(".hssPopularNav"));
  await tick();
  assert.ok(d.querySelector(".hssPopularPage"));
  click(d.querySelector("a.logo"));
  await tick();
  assert.equal(d.querySelector(".hssPopularPage"), null);
  win.close();
});

test("reopening within the state TTL builds the page once, no second rebuild", async () => {
  const { win, d, tick, click } = started();
  click(d.querySelector(".hssPopularNav"));
  await tick();
  let built = 0;
  new win.MutationObserver((rs) => rs.forEach((r) => r.addedNodes.forEach((n) => { if (n.classList && n.classList.contains("hssPopularPage")) built++; }))).observe(d.body, { childList: true });
  click(d.querySelector(".hssPopularNav"));
  await tick();
  assert.equal(built, 1, "unchanged request states keep the page");
  win.close();
});

test("no release yet: a red diagonal strike over the poster, kept red (grey only underneath), badge readable above it", () => {
  const d = doc();
  const c = pp.card(d, { item: NONE, jf: null }, "en", false, null);
  assert.ok(c.classList.contains("hssTopNone"));
  assert.ok(c.querySelector(".hssTopPoster"));
  const css = pp.CSS;
  const rule = (sel) => (css.split("\n").find((l) => l.startsWith(sel + " {")) || "");
  assert.match(rule(".hssTopNone .hssTopPoster::after"), /linear-gradient\(to top right, transparent calc\(50% - [\d.]+em\), rgba\(229, 57, 53/);
  assert.match(rule(".hssTopNone .hssTopPoster::before"), /filter: grayscale\(1\)/);
  assert.match(rule(".hssTopNone .hssTopPoster::before"), /background: inherit/);
  assert.doesNotMatch(css, /\.hssTopNone[^{]*\.hssTopPoster \{[^}]*filter: grayscale/, "a filter on the poster itself would grey the red line too");
  assert.match(rule(".hssTopNone .hssTopPoster > *"), /z-index: 2/);
  assert.doesNotMatch(rule(".hssTopNone .hssTopPoster > *"), /position/, "badge, season and title stay absolutely placed");
  const owned = pp.card(d, { item: FOUND, jf: null }, "en", false, null);
  assert.equal(owned.classList.contains("hssTopNone"), false);
});

test("the source has no characters outside the BMP: the injector config drops them (🌙 became ' Tonight'), escapes survive", async () => {
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("./popular.js", import.meta.url), "utf8");
  assert.equal(/[\u{10000}-\u{10FFFF}]/u.test(src), false);
  const d = doc();
  const c = pp.card(d, { item: FOUND, jf: null }, "en", false, { entries: {}, left: 5 });
  assert.equal(c.querySelector(".hssTopBtn-night").textContent, "🌙 Tonight");
});

test("phone/tablet (no fitted width): the default poster width is not in em, the rank's big font would multiply it (cards 520 px wide on a 390 px phone)", () => {
  const def = /\.hssTopScroll \{[^}]*--hss-p: ([^;]+);/.exec(pp.CSS);
  assert.ok(def);
  assert.doesNotMatch(def[1], /\d(em|%)$/);
  assert.match(def[1], /^\d+(\.\d+)?(rem|px)$/);
});

test("TV layout: icon-only request buttons side by side (labels were cut to 'To…'), the full hint stays in aria-label/title", () => {
  const d = doc();
  const c = pp.card(d, { item: FOUND, jf: null }, "en", true, { entries: {}, left: 5 });
  const night = c.querySelector(".hssTopBtn-night"), now = c.querySelector(".hssTopBtn-now");
  assert.equal(night.textContent, "🌙");
  assert.equal(now.textContent, "⚡");
  assert.ok(night.classList.contains("hssTopBtn-icon"));
  assert.equal(night.getAttribute("aria-label"), "The Diplomat: Download tonight (cheaper power)");
  assert.equal(night.getAttribute("title"), "Download tonight (cheaper power)");
  const off = pp.card(d, { item: NONE, jf: null }, "de", true, null);
  assert.equal(off.querySelector(".hssTopBtn-night").textContent, "🌙");
  const desk = pp.card(d, { item: FOUND, jf: null }, "de", false, { entries: {}, left: 5 });
  assert.equal(desk.querySelector(".hssTopBtn-night").textContent, "🌙 Heute Nacht");
  assert.equal(desk.querySelector(".hssTopBtn-now").textContent, "⚡ Jetzt");
  const rule = pp.CSS.split("\n").find((l) => l.startsWith(".hssTopBtn-icon {")) || "";
  assert.match(rule, /flex: 1 1 0/);
});

test("TV layout: a row with request buttons explains the icons next to its title (no hover tooltips on a TV)", () => {
  const d = doc();
  const sec = d.createElement("div");
  sec.innerHTML = '<div class="sectionTitleContainer"><h2></h2></div><div class="hssTopScroll"></div>';
  pp.fillRow(sec, [{ item: FOUND, jf: null }], "en", true, { entries: {}, left: 5 });
  const lg = sec.querySelector(".sectionTitleContainer .hssTopLegend");
  assert.ok(lg);
  assert.equal(lg.textContent, "🌙 Download tonight (cheaper power) · ⚡ Download now (daytime power price)");
  pp.fillRow(sec, [{ item: FOUND, jf: null }], "en", true, { entries: {}, left: 5 });
  assert.equal(sec.querySelectorAll(".hssTopLegend").length, 1);
  pp.fillRow(sec, [{ item: OWNED, jf: { id: "x", server: "s" } }], "en", true, null);
  assert.equal(sec.querySelector(".hssTopLegend"), null, "no buttons, no legend");
  pp.fillRow(sec, [{ item: FOUND, jf: null }], "en", false, { entries: {}, left: 5 });
  assert.equal(sec.querySelector(".hssTopLegend"), null, "desktop keeps its labels");
});
