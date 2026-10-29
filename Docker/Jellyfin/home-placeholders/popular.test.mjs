import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { JSDOM } from "jsdom";

const pp = createRequire(import.meta.url)("./popular.js");
const dt = createRequire(import.meta.url)("./details.js");

const OWNED = { k: "movie", id: 121, s: null, r: 1, y: 2003, t: { en: "The Return of the King", de: "Die Rückkehr des Königs" }, p: { en: "/rotk.jpg", de: "/rotk-de.jpg" }, o: true, f: null };
const FOUND = { k: "tv", id: 202, s: 4, r: 2, y: 2023, t: { en: "The Diplomat", de: "Diplomatische Beziehungen" }, p: { en: "/d.jpg", de: "/d-de.jpg" }, o: false, f: "2160p", x: "tokTV", d: "detTVxxxxxxxxxxxxxxxx" };
const HIDDEN = { k: "movie", id: 303, s: null, r: 3, y: 2020, t: { en: "Owned elsewhere", de: "x" }, p: { en: "/x.jpg" }, o: true, f: null };
const NONE = { k: "movie", id: 404, s: null, r: 4, y: 2026, t: { en: "No Release", de: "Kein Release" }, p: { en: "/n.jpg" }, o: false, f: null, n: true, c: "2026-10-21", d: "detNONExxxxxxxxxxxxxx" };
const UNKNOWN = { k: "tv", id: null, s: null, r: 5, y: null, t: { en: "Instadocs", de: "Instadocs" }, p: { en: null, de: null }, o: false, f: null, n: true, c: null, u: true };
const DATA = { week: "2026-10-18", home: { shows: [FOUND], movies: [OWNED, HIDDEN] }, rows: [
  { id: "service-8-movie", kind: "movie", type: "service", name: { en: "Netflix", de: "Netflix" }, logo: "/nf.png", items: [OWNED] },
  { id: "service-8-tv", kind: "tv", type: "service", name: { en: "Netflix", de: "Netflix" }, logo: "/nf.png", items: [FOUND] },
  { id: "genre-comedy-tv", kind: "tv", type: "genre", name: { en: "Comedy", de: "Komödie" }, logo: null, items: [FOUND] },
  { id: "genre-horror-movie", kind: "movie", type: "genre", name: { en: "Horror", de: "Horror" }, logo: null, items: [HIDDEN] },
  { id: "genre-drama-movie", kind: "movie", type: "genre", name: { en: "Drama", de: "Drama" }, logo: null, items: [NONE] },
  { id: "genre-docs-tv", kind: "tv", type: "genre", name: { en: "Docs", de: "Doku" }, logo: null, items: [UNKNOWN] }
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
  assert.equal(b[0].querySelector(".hssBtnMain").textContent, "🌙 Download tonight");
  assert.equal(b[1].querySelector(".hssBtnMain").textContent, "⚡ Download now");
  assert.equal(b[0].querySelector(".hssBtnSub").textContent, "lower electricity cost");
  assert.equal(b[1].querySelector(".hssBtnSub").textContent, "daytime electricity price");
  const bde = [...pp.card(d, { item: FOUND, jf: null }, "de", false, null).querySelectorAll("button[data-hss-x]")];
  assert.deepEqual(bde.map((x) => [x.querySelector(".hssBtnMain").textContent, x.querySelector(".hssBtnSub").textContent]), [["🌙 Heute Nacht laden", "günstigerer Strom"], ["⚡ Jetzt laden", "Tagesstrompreis"]]);
  assert.equal(b[0].getAttribute("title"), "Download tonight (lower electricity cost)");
  assert.match(pp.CSS, /\.hssBtnSub \{[^}]*display: none/);
  assert.match(pp.CSS, /@container hssAct \(min-width: 150px\) \{ \.hssBtnSub \{ display: block/);
  assert.equal(card.querySelector(".hssTopBadge").textContent, "2160p");
  assert.match(card.querySelector(".hssTopBadge").getAttribute("title"), /available/);
  assert.match(card.querySelector(".hssTopSeason").textContent, /S4/);
});

test("card state: requested shows the badge, limit reached disables the buttons", () => {
  const d = doc();
  const c1 = pp.card(d, { item: FOUND, jf: null }, "en", false, { entries: { tokTV: { state: "requested", badge: "night" } }, left: 5 });
  assert.equal(c1.querySelector(".hssTopBtn"), null);
  assert.match(c1.querySelector(".hssTopState").textContent, /🌙/);
  const c2 = pp.card(d, { item: FOUND, jf: null }, "de", false, { entries: { tokTV: { state: "requestable" } }, left: 0, limit: 10, days: 7 });
  assert.ok([...c2.querySelectorAll(".hssTopBtn")].every((b) => b.disabled));
  assert.match(c2.querySelector(".hssTopHint").textContent, /10.*7/);
  const c3 = pp.card(d, { item: FOUND, jf: null }, "en", false, { entries: { tokTV: { state: "owned" } }, left: 3 });
  assert.equal(c3.querySelector(".hssTopBtn"), null);
});

test("no release yet: greyed out, badge with the check date, buttons disabled and without an entry id", () => {
  const d = doc();
  const c = pp.card(d, { item: NONE, jf: null }, "en", false, { entries: {}, left: 5 });
  assert.ok(c.classList.contains("hssTopNone"));
  assert.equal(c.querySelector(".hssTopBanner").textContent, "Not able to download");
  assert.equal(c.querySelector(".hssTopBadge"), null, "the banner replaces the old chip");
  assert.ok(c.querySelector(".hssTopPoster .hssTopBanner"));
  assert.match(c.querySelector(".hssTopHint").textContent, /checked 21 Oct · rechecked daily/);
  const b = [...c.querySelectorAll(".hssTopBtn")];
  assert.equal(b.length, 2);
  assert.ok(b.every((x) => x.disabled && !x.hasAttribute("data-hss-x")));
  const de = pp.card(d, { item: NONE, jf: null }, "de", false, null);
  assert.equal(de.querySelector(".hssTopBanner").textContent, "Nicht herunterladbar");
  assert.equal(de.querySelector(".hssTopBadge"), null);
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

test("posters are as wide as Jellyfin's own portrait cards (Hidden Gems), from 700 px up; below that the row keeps its default and scrolls", () => {
  assert.equal(pp.fitPoster(1920), 200);
  assert.equal(pp.fitPoster(1280), 198);
  assert.equal(pp.fitPoster(1600), 186);
  assert.equal(pp.fitPoster(2560), 238);
  assert.equal(pp.fitPoster(800), 148);
  assert.equal(pp.fitPoster(699), null);
  assert.equal(pp.fitPoster(0), null);
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

test("nav entries in the top bar and the drawer: movies then shows (the library order), once each", () => {
  const d = doc('<div class="MuiStack-root"><a href="#/">Holy Grail</a><a class="MuiButton-root x" href="#/movies?topParentId=2&amp;collectionType=movies">Movies</a><a class="MuiButton-root x" href="#/tv?topParentId=1&amp;collectionType=tvshows"><span class="MuiButton-startIcon"><svg><path d="M0"></path></svg></span>Shows</a></div>' +
    '<div class="mainDrawer"><div class="customMenuOptions"></div></div>');
  pp.navLinks(d, "de");
  pp.navLinks(d, "de");
  assert.equal(d.querySelectorAll(".hssPopularNav").length, 4);
  const bar = [...d.querySelectorAll(".MuiStack-root .hssPopularNav")];
  assert.deepEqual(bar.map((a) => [a.textContent, a.getAttribute("data-hss-kind")]), [["Beliebte Filme", "movie"], ["Beliebte Serien", "tv"]]);
  assert.equal(bar[0].className, "MuiButton-root x hssPopularNav");
  assert.equal(bar[0].previousElementSibling.textContent, "Shows");
  assert.equal(bar[1].previousElementSibling, bar[0]);
  const drawer = [...d.querySelectorAll(".customMenuOptions .hssPopularNav")];
  assert.match(drawer[0].className, /navMenuOption/);
  assert.deepEqual(drawer.map((a) => [a.textContent, a.getAttribute("data-hss-kind")]), [["Beliebte Filme", "movie"], ["Beliebte Serien", "tv"]]);
});

test("nav: a missing entry of one kind is added next to the other, a stale entry without a kind is removed", () => {
  const d = doc('<div class="mainDrawer"><div class="customMenuOptions"><a class="navMenuOption hssPopularNav" href="#/home"><span class="navMenuOptionText">Currently popular</span></a></div></div>');
  pp.navLinks(d, "en");
  assert.deepEqual([...d.querySelectorAll(".hssPopularNav")].map((a) => a.getAttribute("data-hss-kind")), ["movie", "tv"]);
  d.querySelector('.hssPopularNav[data-hss-kind="movie"]').remove();
  pp.navLinks(d, "en");
  assert.deepEqual([...d.querySelectorAll(".hssPopularNav")].map((a) => a.textContent), ["Popular movies", "Popular shows"]);
});

test("nav: a language change relabels both entries everywhere", () => {
  const d = doc('<div class="MuiStack-root"><a class="MuiButton-root" href="#/tv?topParentId=1&amp;collectionType=tvshows">Shows</a></div><div class="mainDrawer"><div class="customMenuOptions"></div></div>' +
    '<div class="skinHeader"><div class="headerRight"><button class="headerSearchButton"></button></div></div>');
  d.documentElement.classList.add("layout-tv");
  pp.navLinks(d, "en");
  pp.navLinks(d, "de");
  const labels = (kind) => [...d.querySelectorAll(`.hssPopularNav[data-hss-kind="${kind}"]`)].map((a) => a.tagName === "BUTTON" ? a.getAttribute("aria-label") + "|" + a.getAttribute("title") : a.textContent);
  assert.deepEqual(labels("movie"), ["Beliebte Filme", "Beliebte Filme", "Beliebte Filme|Beliebte Filme"]);
  assert.deepEqual(labels("tv"), ["Beliebte Serien", "Beliebte Serien", "Beliebte Serien|Beliebte Serien"]);
});

const PLACES = '<div class="MuiStack-root"><a class="MuiButton-root" href="#/tv?topParentId=1&amp;collectionType=tvshows">Shows</a></div><div class="mainDrawer"><div class="customMenuOptions"><a class="navMenuOption navMenuOption-selected" href="#/home">Home</a></div></div>' +
  '<div class="MuiDrawer-paper"><ul><li><a class="MuiListItemButton-root Mui-selected" aria-current="page" href="#/home">Home</a></li><li><a class="MuiListItemButton-root" href="#/home?tab=1"><span class="MuiListItemText-primary">Favourites</span></a></li></ul></div>' +
  '<div class="skinHeader"><div class="headerRight"><button class="headerSearchButton"></button></div></div>';

test("nav: an open page marks its own entries selected in every place (TV button excepted), the other kind not", () => {
  const d = doc(PLACES);
  d.documentElement.classList.add("layout-tv");
  pp.navLinks(d, "en", "movie");
  const on = (kind) => [...d.querySelectorAll(`.hssPopularNav[data-hss-kind="${kind}"]`)].map((e) =>
    [e.closest(".MuiStack-root") ? "bar" : e.closest(".customMenuOptions") ? "drawer" : e.closest(".MuiDrawer-paper") ? "mui" : "tv", e.classList.contains("Mui-selected"), e.classList.contains("navMenuOption-selected"), e.getAttribute("aria-current")]);
  assert.deepEqual(on("movie"), [["bar", true, false, "page"], ["drawer", false, true, "page"], ["mui", true, false, "page"], ["tv", false, false, null]]);
  assert.deepEqual(on("tv"), [["bar", false, false, null], ["drawer", false, false, null], ["mui", false, false, null], ["tv", false, false, null]]);
  assert.ok(d.body.classList.contains("hssPopularOpen"));
  pp.navLinks(d, "en", "tv");
  assert.deepEqual(on("movie").map((x) => x.slice(1)), [[false, false, null], [false, false, null], [false, false, null], [false, false, null]]);
  assert.deepEqual(on("tv").map((x) => x[3]), ["page", "page", "page", null]);
  pp.navLinks(d, "en", null);
  assert.equal(d.querySelectorAll(".hssPopularNav.Mui-selected, .hssPopularNav.navMenuOption-selected, .hssPopularNav[aria-current]").length, 0);
  assert.ok(!d.body.classList.contains("hssPopularOpen"));
  assert.ok(d.querySelector('.MuiDrawer-paper a[href="#/home"]').classList.contains("Mui-selected"), "Jellyfin's own state is left alone");
  assert.equal(d.querySelector('.MuiDrawer-paper a[href="#/home"]').getAttribute("aria-current"), "page");
});

test("nav: while a page is open, the entry Jellyfin marks selected (Home) loses its look by CSS on the body class, which survives React re-renders", () => {
  const rule = (sel) => pp.CSS.split("\n").find((l) => l.startsWith(sel)) || "";
  assert.match(rule("body.hssPopularOpen .MuiDrawer-paper .Mui-selected:not(.hssPopularNav)"), /background-color: transparent !important/);
  assert.match(rule("body.hssPopularOpen .MuiStack-root > .Mui-selected:not(.hssPopularNav)"), /background-color: transparent !important/);
  assert.match(rule("body.hssPopularOpen .navMenuOption-selected:not(.hssPopularNav)"), /background: transparent !important/);
});

test("phone layout: an entry in the app drawer after Favourites", () => {
  const d = doc('<div class="MuiDrawer-root"><div class="MuiDrawer-paper"><ul><li class="it"><a class="MuiListItemButton-root" href="#/home"><span class="MuiListItemText-primary">Home</span></a></li>' +
    '<li class="it"><a class="MuiListItemButton-root Mui-selected" href="#/home?tab=1"><svg><path d="M1"></path></svg><span class="MuiListItemText-primary">Favourites</span></a></li></ul></div></div>');
  pp.navLinks(d, "de");
  pp.navLinks(d, "de");
  const items = d.querySelectorAll(".MuiDrawer-paper .hssPopularNav");
  assert.equal(items.length, 2);
  assert.deepEqual([...items].map((a) => [a.querySelector(".MuiListItemText-primary").textContent, a.getAttribute("data-hss-kind")]), [["Beliebte Filme", "movie"], ["Beliebte Serien", "tv"]]);
  assert.equal(items[0].getAttribute("href"), "#/home");
  assert.ok(!items[0].classList.contains("Mui-selected"));
  assert.equal(items[0].querySelector("path").getAttribute("d").slice(0, 4), "M16 ");
  assert.equal(items[0].closest("li").previousElementSibling.textContent, "Favourites");
  assert.equal(items[1].closest("li").previousElementSibling, items[0].closest("li"));
});

test("TV layout: a header button next to search (TV has no drawer button)", () => {
  const d = doc('<div class="skinHeader"><div class="headerRight"><button class="headerSearchButton"></button></div></div>');
  pp.navLinks(d, "en");
  assert.equal(d.querySelector(".headerRight .hssPopularNav"), null);
  d.documentElement.classList.add("layout-tv");
  pp.navLinks(d, "en");
  pp.navLinks(d, "en");
  const b = d.querySelectorAll(".headerRight .hssPopularNav");
  assert.equal(b.length, 2);
  assert.deepEqual([...b].map((x) => [x.tagName, x.getAttribute("data-hss-kind"), x.getAttribute("title"), x.getAttribute("aria-label"), x.querySelector(".material-icons").className]),
    [["BUTTON", "movie", "Popular movies", "Popular movies", "material-icons movie"], ["BUTTON", "tv", "Popular shows", "Popular shows", "material-icons live_tv"]]);
  assert.equal(b[1].nextElementSibling.className, "headerSearchButton");
});

test("the shows page: the Netflix Top 10 Shows, then only the series rows with something to show", () => {
  const d = doc();
  const page = pp.buildPage(d, DATA, LIB, "en", false, null, "tv");
  assert.deepEqual([...page.querySelectorAll(".hssTopRow")].map((r) => r.getAttribute("data-hss-row")), ["Top10Shows", "service-8-tv", "genre-comedy-tv", "genre-docs-tv"]);
  assert.deepEqual([...page.querySelectorAll(".hssTopRow h2")].map((h) => h.textContent), ["Top 10 Shows in Switzerland", "Netflix", "Comedy", "Docs"]);
  assert.match(page.querySelector(".hssTopRow img.hssTopLogo").getAttribute("src"), /w92\/nf\.png$/);
  assert.equal(page.querySelector("h1").textContent, "Popular shows");
  assert.equal(page.getAttribute("data-hss-kind"), "tv");
  assert.match(page.querySelector(".hssPopularSource").textContent, /Netflix.*TMDB.*JustWatch/);
});

test("the movies page: the Netflix Top 10 Movies, then only the film rows; German title", () => {
  const d = doc();
  const page = pp.buildPage(d, DATA, LIB, "de", false, null, "movie");
  assert.deepEqual([...page.querySelectorAll(".hssTopRow")].map((r) => r.getAttribute("data-hss-row")), ["Top10Movies", "service-8-movie", "genre-drama-movie"]);
  assert.equal(page.querySelector("h1").textContent, "Beliebte Filme");
  assert.equal(page.querySelectorAll(".hssTopCard").length, 3);
});

test("rows without a kind (lists published before the split) appear on both pages", () => {
  const d = doc();
  const old = { home: { shows: [], movies: [] }, rows: [{ id: "service-8", type: "service", name: { en: "Netflix", de: "Netflix" }, logo: null, items: [OWNED, FOUND] }] };
  for (const kind of ["movie", "tv"]) assert.deepEqual([...pp.buildPage(d, old, LIB, "en", false, null, kind).querySelectorAll(".hssTopRow")].map((r) => r.getAttribute("data-hss-row")), ["service-8"]);
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
  assert.equal(d.querySelector(".hssTopScroll").style.getPropertyValue("--hss-p"), "200px");
});

function started() {
  const dom = new JSDOM('<html lang="en"><body><header><a class="logo" href="#/">Holy Grail</a><a class="hssPopularNav" data-hss-kind="movie" href="#/home">Popular movies</a><a class="hssPopularNav" data-hss-kind="tv" href="#/home">Popular shows</a></header>' +
    '<div class="MuiDrawer-paper"><ul><li><a class="MuiListItemButton-root Mui-selected" href="#/home">Home</a></li><li><a class="MuiListItemButton-root" href="#/home?tab=1"><span class="MuiListItemText-primary">Favourites</span></a></li></ul></div></body></html>', { url: "https://jellyfin.holy-grail.ch/web/#/home" });
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

test("each nav entry opens its own page; the other entry switches to the other kind", async () => {
  const { win, d, tick, click } = started();
  click(d.querySelector('.hssPopularNav[data-hss-kind="tv"]'));
  await tick();
  assert.equal(d.querySelector(".hssPopularPage h1").textContent, "Popular shows");
  assert.deepEqual([...d.querySelectorAll(".hssPopularPage .hssTopRow")].map((r) => r.getAttribute("data-hss-row")), ["Top10Shows", "service-8-tv", "genre-comedy-tv", "genre-docs-tv"]);
  assert.equal(d.querySelector('.MuiDrawer-paper .hssPopularNav[data-hss-kind="tv"]').getAttribute("aria-current"), "page");
  assert.ok(d.body.classList.contains("hssPopularOpen"));
  click(d.querySelector('.hssPopularNav[data-hss-kind="movie"]'));
  await tick();
  await tick();
  assert.ok(d.querySelector('.MuiDrawer-paper .hssPopularNav[data-hss-kind="movie"]').classList.contains("Mui-selected"));
  assert.ok(!d.querySelector('.MuiDrawer-paper .hssPopularNav[data-hss-kind="tv"]').classList.contains("Mui-selected"));
  click(d.querySelector("a.logo"));
  await tick();
  assert.equal(d.querySelectorAll(".hssPopularNav.Mui-selected, .hssPopularNav[aria-current]").length, 0, "closed: no stale highlight");
  assert.ok(!d.body.classList.contains("hssPopularOpen"));
  click(d.querySelector('.hssPopularNav[data-hss-kind="movie"]'));
  await tick();
  assert.equal(d.querySelectorAll(".hssPopularPage").length, 1);
  assert.equal(d.querySelector(".hssPopularPage h1").textContent, "Popular movies");
  assert.deepEqual([...d.querySelectorAll(".hssPopularPage .hssTopRow")].map((r) => r.getAttribute("data-hss-row")), ["Top10Movies", "service-8-movie", "genre-drama-movie"]);
  win.close();
});

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

test("no release yet: a red diagonal banner over the poster, kept red (grey only underneath)", () => {
  const d = doc();
  const c = pp.card(d, { item: NONE, jf: null }, "en", false, null);
  assert.ok(c.classList.contains("hssTopNone"));
  assert.ok(c.querySelector(".hssTopPoster"));
  const css = pp.CSS;
  const rule = (sel) => (css.split("\n").find((l) => l.startsWith(sel + " {")) || "");
  assert.equal(rule(".hssTopNone .hssTopPoster::after"), "", "the thin line is gone");
  assert.match(rule(".hssTopBanner"), /rotate\(-30deg\)/);
  assert.match(rule(".hssTopBanner"), /background: #d32f2f/);
  assert.match(rule(".hssTopBanner"), /font-size: calc\(var\(--hss-p\) \* 0\.09\)/);
  assert.match(rule(".hssTopBanner"), /width: 1[5-9]\d%/);
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
  assert.equal(c.querySelector(".hssTopBtn-night .hssBtnMain").textContent, "🌙 Download tonight");
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
  assert.equal(night.getAttribute("aria-label"), "The Diplomat: Download tonight (lower electricity cost)");
  assert.equal(night.getAttribute("title"), "Download tonight (lower electricity cost)");
  const off = pp.card(d, { item: NONE, jf: null }, "de", true, null);
  assert.equal(off.querySelector(".hssTopBtn-night").textContent, "🌙");
  const desk = pp.card(d, { item: FOUND, jf: null }, "de", false, { entries: {}, left: 5 });
  assert.equal(desk.querySelector(".hssTopBtn-night .hssBtnMain").textContent, "🌙 Heute Nacht laden");
  assert.equal(desk.querySelector(".hssTopBtn-now .hssBtnMain").textContent, "⚡ Jetzt laden");
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
  assert.equal(lg.textContent, "🌙 Download tonight (lower electricity cost) · ⚡ Download now (daytime electricity price)");
  pp.fillRow(sec, [{ item: FOUND, jf: null }], "en", true, { entries: {}, left: 5 });
  assert.equal(sec.querySelectorAll(".hssTopLegend").length, 1);
  pp.fillRow(sec, [{ item: OWNED, jf: { id: "x", server: "s" } }], "en", true, null);
  assert.equal(sec.querySelector(".hssTopLegend"), null, "no buttons, no legend");
  pp.fillRow(sec, [{ item: FOUND, jf: null }], "en", false, { entries: {}, left: 5 });
  assert.equal(sec.querySelector(".hssTopLegend"), null, "desktop keeps its labels");
});

test("a page opened again at the same width gets its poster size before it is attached (no measuring layout); another width is measured", () => {
  const d = doc('<div class="page"><div class="hssTopScroll"></div><div class="hssTopScroll"></div></div>');
  const page = d.querySelector(".page");
  assert.equal(pp.prefit(page, null, 1920), false);
  assert.equal(pp.prefit(page, { width: 1280, p: 150 }, 1920), false);
  assert.equal(page.querySelector(".hssTopScroll").style.getPropertyValue("--hss-p"), "");
  assert.equal(pp.prefit(page, { width: 1920, p: 150 }, 1920), true);
  for (const l of page.querySelectorAll(".hssTopScroll")) assert.equal(l.style.getPropertyValue("--hss-p"), "150px");
  assert.equal(pp.prefit(page, { width: 1920, p: null }, 1920), true);
  assert.equal(page.querySelector(".hssTopScroll").style.getPropertyValue("--hss-p"), "", "too narrow to fit: the rows scroll");
});

test("fitAll answers the size it set, so the page can remember it", () => {
  const d = doc('<div class="page"><div class="hssTopScroll"></div></div>');
  const win = d.defaultView;
  Object.defineProperty(win, "innerWidth", { value: 1920, configurable: true });
  win.getComputedStyle = () => ({ paddingLeft: "0", paddingRight: "0", columnGap: "14" });
  Object.defineProperty(d.querySelector(".hssTopScroll"), "clientWidth", { get: () => 1800 });
  const p = pp.fitAll(d.querySelector(".page"));
  assert.equal(p, 200);
  assert.equal(pp.fitAll(doc('<div class="page"></div>').querySelector(".page")), undefined);
});

test("daily cap used up for everyone: buttons disabled with an honest hint (no silent no-op)", () => {
  const d = doc();
  const c = pp.card(d, { item: FOUND, jf: null }, "en", false, { entries: { tokTV: { state: "requestable" } }, left: 4, limit: 10, days: 7, dailyFull: true });
  assert.ok([...c.querySelectorAll(".hssTopBtn")].every((b) => b.disabled));
  assert.match(c.querySelector(".hssTopHint").textContent, /today/i);
  const de = pp.card(d, { item: FOUND, jf: null }, "de", false, { entries: {}, left: 4, limit: 10, days: 7, dailyFull: true });
  assert.match(de.querySelector(".hssTopHint").textContent, /heute/i);
});

test("a double tap while the first library load is in flight opens one page, not two", async () => {
  const { win, d, tick, click } = started();
  let release;
  const gate = new Promise((r) => { release = r; });
  win.ApiClient.getJSON = () => gate.then(() => ({ Items: [] }));
  click(d.querySelector(".hssPopularNav"));
  click(d.querySelector(".hssPopularNav"));
  release();
  await tick();
  await tick();
  assert.equal(d.querySelectorAll(".hssPopularPage").length, 1);
  win.close();
});

test("closing while the first library load is in flight leaves no page behind", async () => {
  const { win, d, tick, click } = started();
  let release;
  const gate = new Promise((r) => { release = r; });
  win.ApiClient.getJSON = () => gate.then(() => ({ Items: [] }));
  click(d.querySelector(".hssPopularNav"));
  click(d.querySelector("a.logo"));
  release();
  await tick();
  await tick();
  assert.equal(d.querySelectorAll(".hssPopularPage").length, 0);
  win.close();
});

test("cards not in the library open the details overlay: the poster is a button with the title's key, owned cards keep their link", () => {
  const d = doc();
  const found = pp.card(d, { item: FOUND, jf: null }, "en", false, null);
  const open = found.querySelector(".hssTopPoster");
  assert.equal(open.tagName, "BUTTON");
  assert.ok(open.classList.contains("hssTopOpen"));
  assert.equal(open.getAttribute("type"), "button");
  assert.equal(open.getAttribute("data-hss-key"), "tv/202");
  assert.equal(open.getAttribute("aria-label"), "The Diplomat: details");
  assert.equal(pp.card(d, { item: NONE, jf: null }, "en", false, null).querySelector(".hssTopOpen").getAttribute("data-hss-key"), "movie/404");
  assert.equal(pp.card(d, { item: UNKNOWN, jf: null }, "en", false, null).querySelector(".hssTopOpen").getAttribute("data-hss-key"), "rank/tv/5");
  const owned = pp.card(d, { item: OWNED, jf: LIB["movie/121"] }, "en", false, null);
  assert.equal(owned.querySelector(".hssTopOpen"), null);
  assert.equal(owned.querySelector(".hssTopPoster").tagName, "DIV");
  assert.match(pp.CSS, /button\.hssTopPoster \{[^}]*border: 0/);
});

const INFO = { k: "tv", id: 202, t: { en: "The Diplomat" }, o: { en: "A diplomat." }, y: 2023, g: { en: ["Drama"] }, rt: 50, ns: 4, cert: null, p: { en: "/d.jpg" }, b: "/db.jpg", imdb: "tt1", sc: {}, cast: [], v: [] };

function withDetails(states) {
  const s = started();
  const posts = [];
  let st = states || { entries: { tokTV: { state: "requestable" } }, left: 5, limit: 10, days: 7 };
  s.win.fetch = (url, opts) => {
    if (String(url).startsWith("hss-popular/details")) return Promise.resolve({ status: String(url).includes("detTV") ? 200 : 404, json: () => Promise.resolve({ details: INFO }) });
    if (opts && opts.method === "POST") {
      posts.push(JSON.parse(opts.body));
      st = { entries: { tokTV: { state: "requested", badge: "night" } }, left: 4, limit: 10, days: 7 };
      return Promise.resolve({ status: 200, json: () => Promise.resolve({ ok: true }) });
    }
    return Promise.resolve({ status: 200, json: () => Promise.resolve(st) });
  };
  s.win.ApiClient.getUrl = (p, q) => p + (q ? "?" + new URLSearchParams(q) : "");
  dt.start(s.win);
  s.key = (k, code) => s.win.dispatchEvent(new s.win.KeyboardEvent("keydown", { key: k, keyCode: code, bubbles: true, cancelable: true }));
  s.posts = posts;
  return s;
}

test("popular page: a card opens the overlay on top with the same request buttons; Back closes only the overlay, then the page", async () => {
  const { win, d, tick, click, key } = withDetails();
  click(d.querySelector('.hssPopularNav[data-hss-kind="tv"]'));
  await tick();
  const card = d.querySelector('.hssPopularPage .hssTopOpen[data-hss-key="tv/202"]');
  card.focus();
  click(card);
  await tick();
  await tick();
  const o = d.querySelector(".hssDetails");
  assert.ok(o);
  assert.equal(o.querySelector("h1").textContent, "The Diplomat");
  assert.equal(o.querySelector(".hssDetailsOverview").textContent, "A diplomat.");
  assert.deepEqual([...o.querySelectorAll(".hssDetailsActions button[data-hss-x]")].map((b) => [b.getAttribute("data-hss-x"), b.getAttribute("data-hss-mode"), b.querySelector(".hssBtnMain").textContent, b.querySelector(".hssBtnSub").textContent]), [["tokTV", "night", "\uD83C\uDF19 Download tonight", "lower electricity cost"], ["tokTV", "now", "⚡ Download now", "daytime electricity price"]]);
  const ode = pp.actionsFor(d, FOUND, "de", false, null, true);
  assert.deepEqual([...ode.querySelectorAll("button")].map((b) => [b.querySelector(".hssBtnMain").textContent, b.querySelector(".hssBtnSub").textContent]), [["🌙 Heute Nacht herunterladen", "günstigerer Strom"], ["⚡ Jetzt herunterladen", "Tagesstrompreis"]]);
  assert.ok(d.querySelector(".hssPopularPage"), "the page stays below");
  key("Escape", 27);
  assert.equal(d.querySelector(".hssDetails"), null);
  assert.ok(d.querySelector(".hssPopularPage"), "only the top layer closed");
  assert.equal(d.activeElement.getAttribute("data-hss-key"), "tv/202");
  key("Escape", 27);
  assert.equal(d.querySelector(".hssPopularPage"), null);
  win.close();
});

test("overlay buttons request like the card's and the overlay then shows the new state", async () => {
  const { win, d, tick, click, posts } = withDetails();
  click(d.querySelector('.hssPopularNav[data-hss-kind="tv"]'));
  await tick();
  click(d.querySelector('.hssPopularPage .hssTopOpen[data-hss-key="tv/202"]'));
  await tick();
  click(d.querySelector('.hssDetails .hssTopBtn[data-hss-mode="night"]'));
  await tick();
  await tick();
  assert.deepEqual(posts, [{ id: "tokTV", mode: "night" }]);
  assert.equal(d.querySelector(".hssDetails .hssTopState").textContent, "\uD83C\uDF19 Tonight");
  assert.equal(d.querySelector('.hssPopularPage .hssTopOpen[data-hss-key="tv/202"]').closest(".hssTopCard").querySelector(".hssTopState").textContent, "\uD83C\uDF19 Tonight");
  win.close();
});

test("no release and unidentified titles open the overlay too: disabled buttons, or only the name", async () => {
  const { win, d, tick, click } = withDetails();
  click(d.querySelector('.hssPopularNav[data-hss-kind="movie"]'));
  await tick();
  click(d.querySelector('.hssPopularPage .hssTopOpen[data-hss-key="movie/404"]'));
  await tick();
  let o = d.querySelector(".hssDetails");
  assert.equal(o.querySelector("h1").textContent, "No Release");
  assert.ok([...o.querySelectorAll(".hssDetailsActions button")].every((b) => b.disabled));
  assert.match(o.querySelector(".hssDetailsActions .hssTopHint").textContent, /checked 21 Oct/);
  click(d.querySelector('.hssPopularNav[data-hss-kind="tv"]'));
  await tick();
  click(d.querySelector('.hssPopularPage .hssTopOpen[data-hss-key="rank/tv/5"]'));
  await tick();
  o = d.querySelector(".hssDetails");
  assert.equal(o.querySelector("h1").textContent, "Instadocs");
  assert.match(o.querySelector(".hssDetailsActions .hssTopHint").textContent, /not identified/);
  win.close();
});

test("a link in the overlay (IMDb, TMDB) does not close the page below", async () => {
  const { win, d, tick, click } = withDetails();
  click(d.querySelector('.hssPopularNav[data-hss-kind="tv"]'));
  await tick();
  click(d.querySelector('.hssPopularPage .hssTopOpen[data-hss-key="tv/202"]'));
  await tick();
  await tick();
  d.querySelector(".hssDetails .hssDetailsLink").addEventListener("click", (e) => e.preventDefault());
  click(d.querySelector(".hssDetails .hssDetailsLink"));
  await tick();
  assert.ok(d.querySelector(".hssPopularPage"));
  assert.ok(d.querySelector(".hssDetails"));
  win.close();
});

test("a nav link closes page and overlay alike (route change pattern)", async () => {
  const { win, d, tick, click } = withDetails();
  click(d.querySelector('.hssPopularNav[data-hss-kind="tv"]'));
  await tick();
  click(d.querySelector('.hssPopularPage .hssTopOpen[data-hss-key="tv/202"]'));
  await tick();
  win.HssDetails.close(false);
  click(d.querySelector("a.logo"));
  await tick();
  assert.equal(d.querySelector(".hssPopularPage"), null);
  win.close();
});

test("rank digits: opaque page-colour fill under a doubled outer stroke, so overlapping glyph contours draw no inner lines", () => {
  const rule = pp.CSS.split("\n").find((l) => l.startsWith(".hssTopRank {")) || "";
  assert.match(rule, /paint-order: stroke fill/);
  assert.match(rule, /(^|[ ;])color: var\(--hss-rank-bg, #101010\)/);
  assert.match(rule, /-webkit-text-stroke: 0\.06em/);
  assert.doesNotMatch(rule, /color: transparent/);
  assert.match(rule, /z-index: 0/);
  assert.match(pp.CSS, /\.hssTopPoster \{[^}]*z-index: 1/);
  assert.match(pp.CSS, /\.hssTopRank-2 \{[^}]*font-size/);
});
