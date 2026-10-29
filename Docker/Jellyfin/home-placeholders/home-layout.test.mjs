import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { JSDOM } from "jsdom";

const hl = createRequire(import.meta.url)("./home-layout.js");
const es = createRequire(import.meta.url)("./empty-sections.js");

const DAY = 86400000;
const NOW = Date.UTC(2026, 9, 29);
const PLUGIN = ["ContinueWatching", "NextUp", "MyList", "RecentlyAddedMovies", "RecentlyAddedShows", "HiddenGems", "FinishTheseCollections", "CollectionsSection"];

function played(id, name, daysAgo, user = {}) {
  return { Id: id, Name: name, UserData: { Played: true, LastPlayedDate: daysAgo === null ? null : new Date(NOW - daysAgo * DAY).toISOString(), ...user } };
}

test("layout: groups with the wide rows and My List as dividers, Coming soon after Next Up, other scripts' rows stay at their anchor", () => {
  const extra = es.extraRows({ HssExtraRows: [{ id: "Top10Shows", after: "RecentlyAddedShows" }, { id: "Top10Movies", after: "RecentlyWatchedSeries" }] });
  const ids = hl.layout(es.withExtra(es.withDiscover(es.withRecent(PLUGIN)), extra));
  assert.deepEqual(ids, [
    "ContinueWatching", "NextUp", "HssComingSoon", "MyList",
    "RecentlyAddedMovies", "RecentlyAddedShows", "Top10Shows", "DiscoverStudios",
    "RecentlyWatchedMovies", "RecentlyWatchedSeries", "Top10Movies", "DiscoverNetworks",
    "HssBecauseYouWatched", "HiddenGems", "DiscoverMovieGenres",
    "FinishTheseCollections", "CollectionsSection", "DiscoverSeriesGenres"
  ]);
  assert.deepEqual(hl.layout(ids), ids);
});

test("layout: a missing target keeps the row where it is, our rows go last without one", () => {
  assert.deepEqual(hl.layout(["NextUp", "DiscoverStudios"]), ["NextUp", "DiscoverStudios", "HssComingSoon", "HssBecauseYouWatched"]);
});

test("because you watched: heading names the seed, english and german", () => {
  assert.equal(hl.bywName("Dune", "en-GB"), "Because you watched Dune");
  assert.equal(hl.bywName("Dune", "de"), "Weil du Dune geschaut hast");
  assert.equal(hl.bywName(null, "en"), "Because you watched");
});

test("because you watched: the seed pool is what the user watched (films, finished series, series with played episodes), liked = favourite, thumbs up or rating 7+", () => {
  const pool = hl.buildPool(
    [played("m1", "Dune", 3), played("m2", "Heat", 200, { Rating: 8 }), played("s1", "Dark", null), played("m3", "Cats", 10, { Likes: false, Rating: 3 })],
    [{ SeriesId: "s2", SeriesName: "The Boys", UserData: { LastPlayedDate: new Date(NOW - 2 * DAY).toISOString() } }, { SeriesId: "s1", SeriesName: "Dark", UserData: { LastPlayedDate: new Date(NOW - DAY).toISOString() } }],
    [{ Id: "s2", Name: "The Boys", UserData: { IsFavorite: true } }, { Id: "x9", Name: "Unwatched favourite", UserData: { IsFavorite: true } }]
  );
  assert.deepEqual(pool.map((p) => [p.Id, p.liked, p.at === null ? null : Math.round((NOW - p.at) / DAY)]), [["m1", false, 3], ["m2", true, 200], ["s1", false, 1], ["m3", false, 10], ["s2", true, 2]]);
});

test("because you watched: liked titles weigh 4x, watched in the last 30 days 2x", () => {
  assert.equal(hl.weightOf({ liked: true, at: NOW - DAY }, NOW), 8);
  assert.equal(hl.weightOf({ liked: true, at: NOW - 90 * DAY }, NOW), 4);
  assert.equal(hl.weightOf({ liked: false, at: NOW - DAY }, NOW), 2);
  assert.equal(hl.weightOf({ liked: false, at: null }, NOW), 1);
});

test("because you watched: the seed is a weighted random pick, never one to avoid", () => {
  const pool = [{ Id: "a", liked: false, at: null }, { Id: "b", liked: true, at: NOW }, { Id: "c", liked: false, at: null }];
  assert.equal(hl.pickSeed(pool, () => 0, [], NOW).Id, "a");
  assert.equal(hl.pickSeed(pool, () => 0.5, [], NOW).Id, "b");
  assert.equal(hl.pickSeed(pool, () => 0.99, [], NOW).Id, "c");
  assert.equal(hl.pickSeed(pool, () => 0.5, ["b"], NOW).Id, "c");
  assert.equal(hl.pickSeed(pool, () => 0, ["a", "b", "c"], NOW), null);
  assert.equal(hl.pickSeed([], () => 0, [], NOW), null);
});

test("because you watched: similar titles are unwatched films and series other than the seed, at most 20", () => {
  const items = [{ Id: "seed", Type: "Movie" }, { Id: "a", Type: "Movie" }, { Id: "b", Type: "Series", UserData: { Played: true } }, { Id: "c", Type: "Trailer" }, { Id: "d", Type: "Series", UserData: { Played: false } }];
  assert.deepEqual(hl.similarOf(items, "seed").map((i) => i.Id), ["a", "d"]);
  assert.equal(hl.similarOf(Array.from({ length: 30 }, (_, i) => ({ Id: "x" + i, Type: "Movie" })), "seed").length, 20);
});

function fakeApi(answers, urls = []) {
  return {
    getCurrentUserId: () => "u1",
    getUrl: (p, q) => { const u = p + (q ? "?" + new URLSearchParams(q) : ""); urls.push(u); return u; },
    getJSON: (u) => Promise.resolve(answers(u)),
    getScaledImageUrl: (id, o) => `https://jf.example.com/Items/${id}/Images/${o.type}?tag=${o.tag}`
  };
}

test("because you watched: the pool loads in three parallel requests with the user's session", async () => {
  const urls = [];
  const api = fakeApi((u) => ({ Items: u.includes("IncludeItemTypes=Episode") ? [{ SeriesId: "s1", SeriesName: "Dark", UserData: {} }] : u.includes("IsFavorite=true") ? [] : [played("m1", "Dune", 1)] }), urls);
  const pool = await hl.loadPool(api, "u1");
  assert.deepEqual(pool.map((p) => p.Id), ["m1", "s1"]);
  assert.equal(urls.length, 3);
  assert.ok(urls.every((u) => u.startsWith("Users/u1/Items?") && u.includes("Recursive=true")));
  assert.ok(urls.some((u) => u.includes("IncludeItemTypes=Movie%2CSeries") && u.includes("IsPlayed=true") && u.includes("SortBy=DatePlayed")));
});

test("because you watched: a seed with too few unwatched similar titles is replaced, the last seed is not repeated, a single watched title may repeat", async () => {
  const pool = [{ Id: "a", Name: "A", liked: false, at: null }, { Id: "b", Name: "B", liked: false, at: null }, { Id: "c", Name: "C", liked: false, at: null }];
  const asked = [];
  const api = fakeApi((u) => {
    const id = /^Items\/(\w+)\/Similar/.exec(u)[1];
    asked.push(id);
    return { Items: id === "b" ? [{ Id: "x", Type: "Movie" }] : Array.from({ length: 5 }, (_, i) => ({ Id: id + i, Type: "Movie", Name: "n" })) };
  });
  const row = await hl.loadRow(api, "u1", pool, () => 0, "a", NOW);
  assert.deepEqual(asked, ["b", "c"]);
  assert.equal(row.seed.Id, "c");
  assert.equal(row.items.length, 5);
  asked.length = 0;
  const one = await hl.loadRow(api, "u1", [pool[0]], () => 0, "a", NOW);
  assert.equal(one.seed.Id, "a");
  const none = await hl.loadRow(api, "u1", [], () => 0, null, NOW);
  assert.deepEqual(none, { seed: null, items: [] });
});

function ep(series, season, date, extra = {}) {
  return { SeriesName: series, Name: series, ParentIndexNumber: season, ProviderIds: { SonarrSeriesId: series.length + series[0], FormattedDate: "x - " + date, SonarrPoster: "/HomeScreen/CachedImage/abc123" }, ...extra };
}

test("coming soon: one card per series (its first episode) and the films, soonest week first, the most popular first within a week", () => {
  const shows = [ep("Wednesday", 2, "04.11.2026"), ep("Wednesday", 2, "11.11.2026"), ep("Rings", 3, "06.11.2026"), ep("Later", 1, "20.11.2026")];
  const movies = [{ Name: "Dune", ProviderIds: { RadarrMovieId: "5", FormattedDate: "2 Weeks - 03.11.2026", RadarrPoster: "/HomeScreen/CachedImage/ff" } }];
  const pop = { ["s:" + "Rings".length + "R"]: 300, ["s:" + "Wednesday".length + "W"]: 100, "m:5": 50 };
  const cards = hl.comingSoon(shows, movies, pop);
  assert.deepEqual(cards.map((c) => c.name), ["Rings", "Wednesday", "Dune", "Later"]);
  assert.equal(cards[1].season, 2);
  assert.equal(new Date(cards[1].at).getDate(), 4);
  assert.equal(cards[2].movie, true);
});

test("coming soon: no popularity = by date; the premiere date counts when the plugin's date text is missing", () => {
  const a = { SeriesName: "A", ParentIndexNumber: 1, PremiereDate: "2026-11-10T12:00:00Z", ProviderIds: { SonarrSeriesId: "1" } };
  const b = ep("B", 1, "09.11.2026");
  assert.deepEqual(hl.comingSoon([a, b], [], undefined).map((c) => c.name), ["B", "A"]);
  assert.equal(hl.weekOf(new Date(2026, 10, 9)), hl.weekOf(new Date(2026, 10, 15)));
  assert.notEqual(hl.weekOf(new Date(2026, 10, 15)), hl.weekOf(new Date(2026, 10, 16)));
});

test("coming soon: second line is the season or Movie, and the short date as before, english and german", () => {
  const at = new Date(2026, 10, 11).getTime();
  const now = new Date(2026, 9, 29);
  assert.equal(hl.comingLine({ season: 3, at }, "en", now), "S03 · Wed 11 Nov");
  assert.equal(hl.comingLine({ movie: true, at }, "en", now), "Movie · Wed 11 Nov");
  assert.equal(hl.comingLine({ movie: true, at }, "de", now), "Film · Mi., 11. Nov.");
  assert.equal(hl.comingLine({ season: 1, at: new Date(2027, 0, 6).getTime() }, "en", now), "S01 · Wed 6 Jan 2027");
});

test("coming soon: cards are portrait posters from the plugin's image cache, names are text, a TV button card, nothing to open", () => {
  const doc = new JSDOM("").window.document;
  const api = { getUrl: (p) => "https://jf.example.com/" + p };
  const make = hl.comingCard("en", new Date(2026, 9, 29));
  const card = make(doc, { name: "<b>X</b>", season: 2, at: new Date(2026, 10, 11).getTime(), poster: "/HomeScreen/CachedImage/abc123" }, api, false);
  assert.ok(card.classList.contains("overflowPortraitCard"));
  assert.match(card.querySelector(".cardImageContainer").style.backgroundImage, /jf\.example\.com\/HomeScreen\/CachedImage\/abc123/);
  assert.equal(card.querySelector(".cardText-first bdi").textContent, "<b>X</b>");
  assert.equal(card.querySelector(".cardText-secondary bdi").textContent, "S02 · Wed 11 Nov");
  assert.equal(card.querySelector("[data-action]"), null);
  const odd = make(doc, { name: "Y", movie: true, at: Date.now(), poster: "https://evil.example.com/x.png" }, api, false);
  assert.equal(odd.querySelector(".cardImageContainer").style.backgroundImage, "");
  assert.equal(make(doc, { name: "Y", movie: true, at: Date.now(), poster: null }, api, true).tagName, "BUTTON");
});

test("coming soon: loads the plugin's two Upcoming results with the user's session, ranked by the server's popularity", async () => {
  const urls = [];
  const api = fakeApi((u) => ({ Items: u.startsWith("HomeScreen/Section/UpcomingShows") ? [ep("A", 1, "04.11.2026"), ep("B", 1, "05.11.2026")] : [] }), urls);
  const cards = await hl.loadComing(api, "u1", { "s:1B": 9 });
  assert.deepEqual(cards.map((c) => c.name), ["B", "A"]);
  assert.deepEqual(urls.sort(), ["HomeScreen/Section/UpcomingMovies?UserId=u1", "HomeScreen/Section/UpcomingShows?UserId=u1"]);
});

function homeWin() {
  const dom = new JSDOM('<html lang="en"><head></head><body><div class="homeSectionsContainer"></div></body></html>', { url: "https://jf.example.com/web/" });
  return dom.window;
}

test("rows: skeleton until loaded, then built through empty-sections' tools; a new home load picks again, Coming soon paints from the browser cache", async () => {
  const win = homeWin();
  const c = win.document.querySelector(".homeSectionsContainer");
  const ext = hl.create(win);
  const api = fakeApi((u) => {
    if (u.startsWith("HomeScreen/Section/UpcomingShows")) return { Items: [ep("A", 1, "04.11.2026")] };
    if (u.startsWith("HomeScreen/Section")) return { Items: [] };
    if (/^Items\/\w+\/Similar/.test(u)) return { Items: Array.from({ length: 4 }, (_, i) => ({ Id: "sim" + i, Type: "Movie", Name: "S" + i })) };
    return { Items: u.includes("IsPlayed=true") && u.includes("Movie") ? [played("m1", "Dune", 1), played("m2", "Heat", 1)] : [] };
  });
  let repaints = 0;
  const synced = [];
  const tools = {
    repaint: () => { repaints++; },
    syncRow: (cont, id, items, name, a, make) => synced.push([id, name, items.length, make(win.document, items[0], a, false).querySelector(".cardText-secondary bdi").textContent]),
    syncRecent: (cont, id, items, name) => synced.push([id, name, items.length])
  };
  assert.deepEqual(ext.ids, [hl.COMING, hl.BYW]);
  assert.equal(ext.loading(hl.BYW, c), true);
  ext.sync(c, api, "en", tools);
  assert.equal(ext.loading(hl.COMING, c), true);
  assert.equal(ext.name(hl.BYW, "en", c), "Because you watched");
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(repaints >= 2);
  assert.equal(ext.loading(hl.BYW, c), false);
  ext.sync(c, api, "en", tools);
  const byw = synced.find((s) => s[0] === hl.BYW);
  assert.match(byw[1], /^Because you watched (Dune|Heat)$/);
  assert.equal(byw[2], 4);
  assert.deepEqual(synced.find((s) => s[0] === hl.COMING), [hl.COMING, "Coming soon", 1, "S01 · Wed 4 Nov"]);
  assert.equal(ext.name(hl.COMING, "de", c), "Demnächst");
  assert.equal(ext.hint(hl.COMING, "en"), "Nothing coming in the next months.");
  const first = win.localStorage.getItem("hss-byw:u1");
  const c2 = win.document.createElement("div");
  win.document.body.appendChild(c2);
  ext.sync(c2, api, "en", tools);
  assert.equal(ext.loading(hl.COMING, c2), false, "fresh cache paints at once");
  await new Promise((r) => setTimeout(r, 20));
  assert.notEqual(win.localStorage.getItem("hss-byw:u1"), first, "the next load picks the other title");
  assert.ok(win.document.getElementById("hss-layout-style"));
  win.close();
});

test("styles: My List is a tinted full-width band with compact posters, the wide rows get a divider line, added once", () => {
  const win = homeWin();
  hl.styles(win.document);
  hl.styles(win.document);
  const all = win.document.querySelectorAll("#hss-layout-style");
  assert.equal(all.length, 1);
  const css = all[0].textContent;
  assert.match(css, /\.verticalSection\.MyList:not\(\.hide\), \[data-hss-placeholder="MyList"\] \{[^}]*linear-gradient/);
  assert.match(css, /\.verticalSection\.MyList \.overflowPortraitCard \{ width: clamp/);
  for (const id of ["DiscoverStudios", "DiscoverNetworks", "DiscoverMovieGenres", "DiscoverSeriesGenres"]) assert.ok(css.includes(".verticalSection." + id), id);
  win.close();
});

test("start: empty-sections lays out, names and fills the extension rows in place, a broken extension never breaks the home", async () => {
  const win = homeWin();
  win.HssPageMeta = { Page: 1 };
  win.HssHomeExt = [
    { ids: ["Broken"], layout: () => { throw new Error("x"); }, name: () => { throw new Error("x"); }, hint: () => null, loading: () => { throw new Error("x"); }, sync: () => { throw new Error("x"); } },
    {
      ids: ["MyRow"],
      layout: (ids) => ["MyRow"].concat(ids.filter((i) => i !== "MyRow")),
      name: (id, lang) => (lang === "en" ? "Mine" : "Meins"),
      hint: () => "No mine yet.",
      loading: () => false,
      sync: (cont, api, lang, tools) => tools.syncRecent(cont, "MyRow", [{ Id: "i1", Name: "One", Type: "Movie" }], "Mine", api)
    }
  ];
  win.ApiClient = fakeApi((u) => u.startsWith("ModularHomeViews/UserSettings") ? { EnabledSections: ["NextUp"] }
    : u.startsWith("ModularHomeViews/Sections") ? { Items: [{ Section: "NextUp", DisplayText: "Next Up" }] } : { Items: [] });
  es.start(win);
  const c = win.document.querySelector(".homeSectionsContainer");
  c.innerHTML = '<div data-page="1" style="order:1;" class="verticalSection NextUp"><div class="sectionTitleContainer"><h2 class="sectionTitle">Next Up</h2></div><div is="emby-itemscontainer" class="itemsContainer"><div class="card"></div></div></div>';
  await new Promise((r) => setTimeout(r, 1500));
  const rows = [...c.children].filter((e) => e.classList.contains("verticalSection")).map((e) => e.getAttribute("data-hss-placeholder") || e.classList[1]);
  assert.equal(rows[0], "MyRow");
  assert.equal(c.querySelector(".verticalSection.MyRow h2").textContent, "Mine");
  assert.deepEqual(rows.slice(1, 4), ["NextUp", "RecentlyWatchedMovies", "RecentlyWatchedSeries"]);
  assert.equal(c.querySelector('[data-hss-placeholder="Broken"]'), null);
  win.close();
});
