import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { JSDOM } from "jsdom";

const es = createRequire(import.meta.url)("./empty-sections.js");

const ENABLED = ["MyMedia", "ContinueWatching", "NextUp", "BecauseYouWatched", "HiddenGems", "MyList", "UpcomingMovies"];
const NAMES = { MyMedia: "My Media", ContinueWatching: "Continue Watching", NextUp: "Next Up", BecauseYouWatched: "Because You Watched", HiddenGems: "Hidden Gems", MyList: "My List", UpcomingMovies: "Upcoming Movies" };

function section(cls, order, items, hidden) {
  return `<div data-page="1" style="order:${order};" class="verticalSection ${cls}${hidden ? " hide" : ""}">` +
    `<div class="sectionTitleContainer sectionTitleContainer-cards padded-left"><h2 class="sectionTitle sectionTitle-cards">${cls}</h2></div>` +
    `<div is="emby-itemscontainer" class="itemsContainer">${"<div class=\"card\"></div>".repeat(items)}</div></div>`;
}

function home(inner) {
  const dom = new JSDOM(`<div class="homeSectionsContainer"><div id="hssLoadingIndicator" class="verticalSection" style="order: 2147000000;display:none;"></div>${inner}</div>`);
  return dom.window.document.querySelector(".homeSectionsContainer");
}

function order(c) {
  return [...c.children].filter((e) => e.id !== "hssLoadingIndicator" && !e.classList.contains("hide"))
    .map((e) => e.getAttribute("data-hss-placeholder") ? "_" + e.getAttribute("data-hss-placeholder") : e.classList[1]);
}

function full() {
  return section("MyMedia", 1, 2) + section("ContinueWatching", 2, 0, true) + section("NextUp", 3, 3) + section("HiddenGems", 8, 0, true) + section("MyList", 10, 1) + section("UpcomingMovies", 16, 0, true);
}

test("empty and missing sections get a placeholder in the configured order", () => {
  const c = home(full());
  es.render(c, ENABLED, NAMES, "en");
  assert.deepEqual(order(c), ["MyMedia", "_ContinueWatching", "NextUp", "_BecauseYouWatched", "_HiddenGems", "MyList", "_UpcomingMovies"]);
});

test("placeholder shows heading and the section hint", () => {
  const c = home(full());
  es.render(c, ENABLED, NAMES, "en");
  const p = c.querySelector('[data-hss-placeholder="ContinueWatching"]');
  assert.equal(p.querySelector("h2.sectionTitle.sectionTitle-cards").textContent, "Continue Watching");
  assert.equal(p.querySelector("[data-hss-hint]").textContent, "Nothing to continue — start something to see it here.");
  assert.ok(p.classList.contains("verticalSection"));
  assert.equal(p.style.order, "2");
});

test("missing section takes the order of its previous neighbour", () => {
  const c = home(full());
  es.render(c, ENABLED, NAMES, "en");
  assert.equal(c.querySelector('[data-hss-placeholder="BecauseYouWatched"]').style.order, "3");
});

test("non-empty sections get no placeholder", () => {
  const c = home(full());
  es.render(c, ENABLED, NAMES, "en");
  for (const id of ["MyMedia", "NextUp", "MyList"]) assert.equal(c.querySelector(`[data-hss-placeholder="${id}"]`), null, id);
});

test("multi-row section with one visible row counts as filled", () => {
  const c = home(section("BecauseYouWatched-Dark", 7, 2) + section("BecauseYouWatched-Heat", 7, 0, true));
  es.render(c, ["BecauseYouWatched"], NAMES, "en");
  assert.equal(c.querySelector("[data-hss-placeholder]"), null);
});

test("idempotent", () => {
  const c = home(full());
  es.render(c, ENABLED, NAMES, "en");
  const once = c.innerHTML;
  es.render(c, ENABLED, NAMES, "en");
  es.render(c, ENABLED, NAMES, "en");
  assert.equal(c.innerHTML, once);
  assert.equal(c.querySelectorAll('[data-hss-placeholder="HiddenGems"]').length, 1);
});

test("placeholder goes away when the real section fills later", () => {
  const c = home(full());
  es.render(c, ENABLED, NAMES, "en");
  const real = c.querySelector(".verticalSection.HiddenGems");
  real.querySelector(".itemsContainer").innerHTML = '<div class="card"></div>';
  real.classList.remove("hide");
  es.render(c, ENABLED, NAMES, "en");
  assert.equal(c.querySelector('[data-hss-placeholder="HiddenGems"]'), null);
});

test("missing section appearing later replaces its placeholder", () => {
  const c = home(full());
  es.render(c, ENABLED, NAMES, "en");
  c.insertAdjacentHTML("beforeend", section("BecauseYouWatched-Dark", 7, 2));
  es.render(c, ENABLED, NAMES, "en");
  assert.equal(c.querySelector('[data-hss-placeholder="BecauseYouWatched"]'), null);
});

test("german hints", () => {
  const c = home(full());
  es.render(c, ENABLED, NAMES, "de-CH");
  assert.equal(c.querySelector('[data-hss-placeholder="ContinueWatching"] [data-hss-hint]').textContent, "Nichts fortzusetzen – starte etwas, dann erscheint es hier.");
});

test("every hint has english and german, others generic", () => {
  for (const id of ["ContinueWatching", "NextUp", "BecauseYouWatched", "HiddenGems", "FinishTheseCollections", "RecentlyWatchedMovies", "RecentlyWatchedSeries", "UpcomingShows", "UpcomingMovies", "MyList", "CollectionsSection", "Studio", "Starring", "DirectedBy"]) {
    assert.ok(es.hintFor(id, "en"), id);
    assert.ok(es.hintFor(id, "de"), id);
    assert.notEqual(es.hintFor(id, "en"), es.hintFor(id, "de"), id);
  }
  assert.equal(es.hintFor("MyMedia", "en"), "Nothing here yet.");
  assert.equal(es.hintFor("MyMedia", "de"), "Noch nichts hier.");
  assert.equal(es.hintFor("UpcomingMovies", "en"), "No films releasing in the next months.");
  assert.equal(es.hintFor("UpcomingShows", "en"), "No episodes airing in the next month.");
  assert.equal(es.hintFor("MyList", "en"), "Nothing here yet — mark films or series with ♥ to see them here.");
});

test("names are text, not html", () => {
  const c = home("");
  es.render(c, ["MyList"], { MyList: "<img src=x onerror=alert(1)>" }, "en");
  const p = c.querySelector('[data-hss-placeholder="MyList"]');
  assert.equal(p.querySelector("img"), null);
  assert.equal(p.querySelector("h2").textContent, "<img src=x onerror=alert(1)>");
});

test("missing name falls back to the section id", () => {
  const c = home("");
  es.render(c, ["Studio"], {}, "en");
  assert.equal(c.querySelector('[data-hss-placeholder="Studio"] h2').textContent, "Studio");
});

test("section removed from the config loses its placeholder", () => {
  const c = home(full());
  es.render(c, ENABLED, NAMES, "en");
  es.render(c, ["MyMedia"], NAMES, "en");
  assert.equal(c.querySelectorAll("[data-hss-placeholder]").length, 0);
});

test("names from the catalogue response", () => {
  assert.deepEqual(es.namesOf({ Items: [{ Section: "MyList", DisplayText: "Meine Liste" }, { Section: "x" }] }), { MyList: "Meine Liste" });
  assert.deepEqual(es.namesOf(null), {});
});

test("enabled list from the user settings response", () => {
  assert.deepEqual(es.enabledOf({ EnabledSections: ["A", "B"], DefaultEnabledSections: ["C"] }), ["A", "B"]);
  assert.deepEqual(es.enabledOf({}), []);
  assert.deepEqual(es.enabledOf(null), []);
  assert.deepEqual(es.enabledOf({ EnabledSections: ["A", 3, "<x>"] }), ["A"]);
});

test("start: without Home Screen Sections nothing happens", async () => {
  const dom = new JSDOM('<html><body><div class="homeSectionsContainer"><div class="verticalSection section0" data-page="1"></div></div></body></html>');
  const win = dom.window;
  let calls = 0;
  win.ApiClient = { getCurrentUserId: () => "u1", getUrl: () => { calls++; return ""; }, getJSON: () => Promise.resolve({}) };
  es.start(win);
  win.document.body.appendChild(win.document.createElement("div"));
  await new Promise((r) => setTimeout(r, 1500));
  assert.equal(calls, 0);
  assert.equal(win.document.querySelectorAll("[data-hss-placeholder]").length, 0);
  win.close();
});

function upcoming(texts) {
  const cards = texts.map((t) => `<div class="card upcoming-show-card"><div class="cardText cardText-first"><bdi><div class="itemAction textActionButton">Show</div></bdi></div>` +
    `<div class="cardText cardText-tertiary"><bdi><div class="itemAction textActionButton" title="${t}">${t}</div></bdi></div></div>`).join("");
  return home(`<div class="verticalSection UpcomingShows" data-page="1"><div class="itemsContainer">${cards}</div></div>`);
}

function dates(c) {
  return [...c.querySelectorAll(".cardText-tertiary .textActionButton")].map((e) => e.textContent);
}

const NOW = new Date(2026, 9, 27);

test("upcoming dates show weekday, day and month", () => {
  const c = upcoming(["2 Weeks, 1 Day - 11.11.2026", "3 Weeks, 1 Day - 18.11.2026"]);
  es.upcomingDates(c, "en-US", NOW);
  assert.deepEqual(dates(c), ["Wed 11 Nov", "Wed 18 Nov"]);
});

test("upcoming dates next year carry the year", () => {
  const c = upcoming(["2 Months - 04.01.2027"]);
  es.upcomingDates(c, "en", NOW);
  assert.deepEqual(dates(c), ["Mon 4 Jan 2027"]);
});

test("upcoming dates in german", () => {
  const c = upcoming(["2 Weeks, 1 Day - 11.11.2026"]);
  es.upcomingDates(c, "de-CH", NOW);
  assert.match(dates(c)[0], /^Mi.*11.*Nov/);
});

test("upcoming dates: idempotent, unknown text untouched, movies too", () => {
  const c = upcoming(["2 Weeks, 1 Day - 11.11.2026", "TBA"]);
  c.querySelector(".card").className = "card upcoming-movie-card";
  es.upcomingDates(c, "en", NOW);
  es.upcomingDates(c, "en", NOW);
  assert.deepEqual(dates(c), ["Wed 11 Nov", "TBA"]);
  assert.equal(c.querySelector(".cardText-tertiary .textActionButton").title, "2 Weeks, 1 Day - 11.11.2026");
});

function shows(list) {
  const cards = list.map(([name, ep, date]) => `<div class="card upcoming-show-card"><div class="cardText cardText-first"><bdi><div class="itemAction textActionButton" title="${name}">${name}</div></bdi></div>` +
    `<div class="cardText cardText-secondary"><bdi><div class="itemAction textActionButton" title="${ep}">${ep}</div></bdi></div>` +
    `<div class="cardText cardText-tertiary"><bdi><div class="itemAction textActionButton" title="${date}">${date}</div></bdi></div></div>`).join("");
  return home(`<div class="verticalSection UpcomingShows" data-page="1"><div class="itemsContainer">${cards}</div></div>`);
}

function visible(c) {
  return [...c.querySelectorAll(".upcoming-show-card")].filter((e) => e.style.display !== "none")
    .map((e) => [...e.querySelectorAll(".textActionButton")].map((t) => t.textContent).join(" | "));
}

test("upcoming shows: one card per series, the next episode's season and date", () => {
  const c = shows([["Rings", "S03E01 - TBA", "2 Weeks - 11.11.2026"], ["Rings", "S03E02 - TBA", "2 Weeks - 11.11.2026"], ["Frieren", "S02E05 - Title", "3 Weeks - 18.11.2026"], ["Rings", "S03E05 - TBA", "3 Weeks - 18.11.2026"]]);
  es.upcomingSeries(c);
  es.upcomingDates(c, "en", NOW);
  assert.deepEqual(visible(c), ["Rings | S03 | Wed 11 Nov", "Frieren | S02 | Wed 18 Nov"]);
});

test("upcoming shows: idempotent and movies untouched", () => {
  const c = shows([["Rings", "S03E01 - TBA", "x - 11.11.2026"], ["Rings", "S03E02 - TBA", "x - 11.11.2026"]]);
  c.querySelector(".itemsContainer").insertAdjacentHTML("beforeend", '<div class="card upcoming-movie-card"><div class="cardText cardText-first"><bdi><div class="itemAction textActionButton">Film</div></bdi></div></div><div class="card upcoming-movie-card"><div class="cardText cardText-first"><bdi><div class="itemAction textActionButton">Film</div></bdi></div></div>');
  es.upcomingSeries(c);
  es.upcomingSeries(c);
  assert.deepEqual(visible(c), ["Rings | S03 | x - 11.11.2026"]);
  assert.equal([...c.querySelectorAll(".upcoming-movie-card")].filter((e) => e.style.display !== "none").length, 2);
});

function prefsHome() {
  const sel = (n) => `<div class="selectContainer"><label>Home screen section ${n}</label><select is="emby-select" id="selectHomeSection${n}"></select></div>`;
  return new JSDOM(`<html><head></head><body><div class="homeScreenSettingsContainer"><form>` +
    `<div class="verticalSection"><label class="checkboxContainer"><input class="chkHidePlayedFromLatest" type="checkbox"/></label></div>` +
    `${[1, 2, 10].map(sel).join("")}<div class="selectContainer"><select class="selectLanding"></select></div></form></div></body></html>`);
}

test("lock: Jellyfin's own home section pickers are hidden, the rest of Settings → Home stays", () => {
  const dom = prefsHome();
  es.lockHomeSettings(dom.window.document);
  const hidden = [...dom.window.document.querySelectorAll(es.LOCKED_SELECTOR)];
  assert.deepEqual(hidden.map((e) => e.querySelector("select").id), ["selectHomeSection1", "selectHomeSection2", "selectHomeSection10"]);
  assert.match(dom.window.document.getElementById("hss-lock-style").textContent, /display:\s*none\s*!important/);
});

test("lock: style is added once", () => {
  const dom = prefsHome();
  es.lockHomeSettings(dom.window.document);
  es.lockHomeSettings(dom.window.document);
  assert.equal(dom.window.document.querySelectorAll("#hss-lock-style").length, 1);
});

test("start: locks the home settings even without Home Screen Sections on the page", () => {
  const dom = new JSDOM("<html><head></head><body></body></html>");
  es.start(dom.window);
  assert.equal(dom.window.document.querySelectorAll("#hss-lock-style").length, 1);
  dom.window.close();
});

test("because you watched says what the plugin needs", () => {
  assert.equal(es.hintFor("BecauseYouWatched", "en"), "Nothing here yet — finish a film to get recommendations.");
  assert.equal(es.hintFor("BecauseYouWatched", "de"), "Noch nichts hier – schau einen Film fertig, dann gibt es Empfehlungen.");
});

const RNOW = new Date("2026-10-27T22:00:00Z");
const daysAgo = (d) => d === null ? null : new Date(RNOW.getTime() - d * 86400000).toISOString();
const movie = (id, d, extra) => ({ Id: id, Name: "Film " + id, Type: "Movie", ServerId: "s1", ProductionYear: 2025, ImageTags: { Thumb: "t" + id }, UserData: { Played: true, LastPlayedDate: daysAgo(d) }, ...extra });
const episode = (series, d, season = 1, id) => ({ Id: id || "e" + series + d + "-" + season, Type: "Episode", SeriesId: series, ParentIndexNumber: season, UserData: { Played: true, LastPlayedDate: daysAgo(d) } });
const show = (id, unplayed, extra) => ({ Id: id, Name: "Show " + id, Type: "Series", ServerId: "s1", ImageTags: {}, BackdropImageTags: ["b" + id], UserData: { UnplayedItemCount: unplayed, Played: unplayed === 0 }, ...extra });
const boxset = (id, unplayed, count = 3) => ({ Id: id, Name: "Collection " + id, Type: "BoxSet", ServerId: "s1", ImageTags: { Primary: "p" + id }, RecursiveItemCount: count, UserData: { UnplayedItemCount: unplayed, Played: unplayed === 0 } });
const ids = (items) => items.map((i) => i.Id);

test("recent movies: played films by last play, newest first, last 60 days, at most 20", () => {
  assert.deepEqual(ids(es.pickMovies([movie("m1", 0.1), movie("m2", 10), movie("m3", 61), movie("m4", null), movie("m5", 59)], RNOW)), ["m1", "m2", "m5"]);
  assert.deepEqual(ids(es.pickMovies([movie("m2", 10), movie("m1", 0.1)], RNOW)), ["m1", "m2"]);
  assert.equal(es.pickMovies(Array.from({ length: 30 }, (_, i) => movie("m" + i, i)), RNOW).length, 20);
  assert.equal(es.RECENT_DAYS, 60);
  assert.equal(es.RECENT_LIMIT, 20);
});

test("recent series: candidates are series with a regular episode played in the last 60 days, newest play wins", () => {
  const c = es.seriesCandidates([episode("s1", 1), episode("s1", 5), episode("s2", 20), episode("s3", 70), episode("s4", 2, 0), episode("s5", null)], RNOW);
  assert.deepEqual(Object.keys(c).sort(), ["s1", "s2"]);
  assert.equal(c.s1, Date.parse(daysAgo(1)));
});

test("recent series: only series watched completely, one episode of The Boys is not enough", () => {
  const cand = es.seriesCandidates([episode("boys", 0.1), episode("done", 3)], RNOW);
  assert.deepEqual(ids(es.pickSeries([show("boys", 15), show("done", 0)], cand, [], [], RNOW)), ["done"]);
});

test("recent series: unwatched specials and unaired episodes do not count", () => {
  const cand = es.seriesCandidates([episode("a", 1), episode("b", 2), episode("c", 3)], RNOW);
  const left = [
    { Id: "x1", SeriesId: "a", ParentIndexNumber: 0 }, { Id: "x2", SeriesId: "a", ParentIndexNumber: 0 },
    { Id: "x3", SeriesId: "b", ParentIndexNumber: 3 },
    { Id: "x3", SeriesId: "b", ParentIndexNumber: 3 },
    { Id: "x4", SeriesId: "c", ParentIndexNumber: 0 }
  ];
  assert.deepEqual(ids(es.pickSeries([show("a", 2), show("b", 1), show("c", 2)], cand, left, [], RNOW)), ["a", "b"]);
});

test("recent series: complete collections join the row by their last film play, newest first, at most 20", () => {
  const cand = es.seriesCandidates([episode("s1", 5), episode("s2", 1)], RNOW);
  const cols = [{ item: boxset("c1", 0), at: Date.parse(daysAgo(3)) }, { item: boxset("c2", 0), at: Date.parse(daysAgo(61)) }];
  assert.deepEqual(ids(es.pickSeries([show("s1", 0), show("s2", 0)], cand, [], cols, RNOW)), ["s2", "c1", "s1"]);
  const many = Array.from({ length: 25 }, (_, i) => "s" + i);
  const manyCand = es.seriesCandidates(many.map((s, i) => episode(s, i)), RNOW);
  assert.equal(es.pickSeries(many.map((s) => show(s, 0)), manyCand, [], [], RNOW).length, 20);
});

test("recent series: a series missing from the answer is left out", () => {
  const cand = es.seriesCandidates([episode("gone", 1)], RNOW);
  assert.deepEqual(es.pickSeries([], cand, [], [], RNOW), []);
});

test("recent: collection is complete when every film is played", () => {
  assert.equal(es.completeCollection(boxset("c", 0)), true);
  assert.equal(es.completeCollection(boxset("c", 1)), false);
  assert.equal(es.completeCollection(boxset("c", 0, 0)), false);
  assert.equal(es.completeCollection({ Id: "c", Type: "BoxSet" }), false);
});

test("recent: both rows sit right after Recently Added Shows, films first, else at the end", () => {
  assert.deepEqual(es.withRecent(["NextUp", "RecentlyAddedShows", "BecauseYouWatched"]), ["NextUp", "RecentlyAddedShows", "RecentlyWatchedMovies", "RecentlyWatchedSeries", "BecauseYouWatched"]);
  assert.deepEqual(es.withRecent(["NextUp", "MyList"]), ["NextUp", "MyList", "RecentlyWatchedMovies", "RecentlyWatchedSeries"]);
  assert.deepEqual(es.withRecent(["RecentlyAddedShows", "RecentlyWatchedMovies", "RecentlyWatchedSeries"]), ["RecentlyAddedShows", "RecentlyWatchedMovies", "RecentlyWatchedSeries"]);
});

test("recent: names and hints in english and german", () => {
  assert.equal(es.recentName("RecentlyWatchedMovies", "en"), "Recently Watched Movies");
  assert.equal(es.recentName("RecentlyWatchedMovies", "de-CH"), "Kürzlich angesehene Filme");
  assert.equal(es.recentName("RecentlyWatchedSeries", "en"), "Recently Watched TV Series & Collections");
  assert.equal(es.recentName("RecentlyWatchedSeries", "de"), "Kürzlich angesehene Serien & Filmreihen");
  assert.equal(es.hintFor("RecentlyWatchedMovies", "en"), "Nothing here yet — films you finish show up here.");
  assert.equal(es.hintFor("RecentlyWatchedMovies", "de"), "Noch nichts hier – Filme, die du fertig schaust, erscheinen hier.");
  assert.equal(es.hintFor("RecentlyWatchedSeries", "en"), "Nothing here yet — series you finish completely and complete film collections show up here.");
  assert.equal(es.hintFor("RecentlyWatchedSeries", "de"), "Noch nichts hier – Serien, die du ganz fertig schaust, und komplett geschaute Filmreihen erscheinen hier.");
});

const fakeApi = { getScaledImageUrl: (id, o) => `https://jf.example.com/Items/${id}/Images/${o.type}?tag=${o.tag}&fillWidth=${o.fillWidth}` };
const MOV = "RecentlyWatchedMovies";
const SER = "RecentlyWatchedSeries";

test("recent: cards link to the item, show name and year, use the thumb, names are text", () => {
  const c = home(section("RecentlyAddedShows", 6, 0, true));
  es.syncRecent(c, MOV, [movie("m1", 0, { Name: "<b>Sirāt</b>" })], "Recently Watched Movies", fakeApi);
  es.syncRecent(c, SER, [show("s1", 0, { ProductionYear: 2019 }), boxset("c1", 0)], "Recently Watched TV Series & Collections", fakeApi);
  const sec = c.querySelector(".verticalSection.RecentlyWatchedMovies[data-hss-client]");
  assert.equal(sec.querySelector("h2.sectionTitle").textContent, "Recently Watched Movies");
  const card = sec.querySelector(".itemsContainer > .card");
  assert.equal(card.getAttribute("data-id"), "m1");
  assert.equal(card.getAttribute("data-type"), "Movie");
  assert.equal(card.getAttribute("data-serverid"), "s1");
  assert.equal(card.querySelector("a.cardImageContainer").getAttribute("href"), "#/details?id=m1&serverId=s1");
  assert.match(card.querySelector(".cardImageContainer").style.backgroundImage, /Images\/Thumb\?tag=tm1/);
  assert.equal(card.querySelector(".cardText-first bdi").textContent, "<b>Sirāt</b>");
  assert.equal(card.querySelector("b"), null);
  assert.equal(card.querySelector(".cardText-secondary bdi").textContent, "2025");
  assert.ok(card.querySelector('[data-action="resume"]'));
  const cards = c.querySelectorAll(".verticalSection.RecentlyWatchedSeries .itemsContainer > .card");
  assert.deepEqual([...cards].map((e) => [e.getAttribute("data-type"), e.getAttribute("data-isfolder")]), [["Series", "true"], ["BoxSet", "true"]]);
  assert.match(cards[0].querySelector(".cardImageContainer").style.backgroundImage, /Images\/Backdrop\?tag=bs1/);
  assert.match(cards[1].querySelector(".cardImageContainer").style.backgroundImage, /Images\/Primary\?tag=pc1/);
});

test("recent: TV layout gets focusable button cards like Jellyfin's own", () => {
  const c = home("");
  c.ownerDocument.documentElement.classList.add("layout-tv");
  es.syncRecent(c, MOV, [movie("m1", 0)], "Recently Watched Movies", fakeApi);
  const card = c.querySelector(".RecentlyWatchedMovies .card");
  assert.equal(card.tagName, "BUTTON");
  assert.equal(card.getAttribute("data-action"), "link");
  assert.ok(card.classList.contains("itemAction") && card.classList.contains("show-focus"));
  assert.equal(card.querySelector("a"), null);
});

test("recent: no items = no row (placeholder instead), same items = untouched, rows independent", () => {
  const c = home("");
  es.syncRecent(c, MOV, [movie("m1", 0)], "Recently Watched Movies", fakeApi);
  es.syncRecent(c, SER, [show("s1", 0)], "Series", fakeApi);
  const first = c.querySelector(".RecentlyWatchedMovies");
  es.syncRecent(c, MOV, [movie("m1", 0)], "Recently Watched Movies", fakeApi);
  assert.equal(c.querySelector(".RecentlyWatchedMovies"), first);
  es.syncRecent(c, MOV, [], "Recently Watched Movies", fakeApi);
  assert.equal(c.querySelector(".RecentlyWatchedMovies"), null);
  assert.ok(c.querySelector(".RecentlyWatchedSeries"));
});

const RNAMES = { NextUp: "Next Up", RecentlyAddedShows: "Recently Added Shows", BecauseYouWatched: "Because You Watched", RecentlyWatchedMovies: "Recently Watched Movies", RecentlyWatchedSeries: "Recently Watched TV Series & Collections" };

test("recent: render places both rows right after Recently Added Shows, empty = placeholder with hint", () => {
  const enabled = es.withRecent(["NextUp", "RecentlyAddedShows", "BecauseYouWatched"]);
  const c = home(section("NextUp", 3, 1) + section("RecentlyAddedShows", 6, 0, true) + section("BecauseYouWatched-x", 7, 2));
  es.syncRecent(c, MOV, [movie("m1", 0)], RNAMES[MOV], fakeApi);
  es.render(c, enabled, RNAMES, "en");
  assert.deepEqual(order(c), ["NextUp", "_RecentlyAddedShows", MOV, "_" + SER, "BecauseYouWatched-x"]);
  assert.equal(c.querySelector("." + MOV).style.order, "6");
  assert.equal(c.querySelector(`[data-hss-placeholder="${SER}"] [data-hss-hint]`).textContent, "Nothing here yet — series you finish completely and complete film collections show up here.");
  es.syncRecent(c, MOV, [], RNAMES[MOV], fakeApi);
  es.syncRecent(c, SER, [show("s1", 0)], RNAMES[SER], fakeApi);
  es.render(c, enabled, RNAMES, "en");
  assert.deepEqual(order(c), ["NextUp", "_RecentlyAddedShows", "_" + MOV, SER, "BecauseYouWatched-x"]);
});

test("recent: a new row is inserted where its placeholder is, so Jellyfin's scroller is attached once", () => {
  const enabled = es.withRecent(["NextUp", "RecentlyAddedShows", "BecauseYouWatched"]);
  const c = home(section("NextUp", 3, 1) + section("RecentlyAddedShows", 6, 0, true) + section("BecauseYouWatched-x", 7, 2));
  es.render(c, enabled, {}, "en");
  const moved = [];
  new c.ownerDocument.defaultView.MutationObserver((ms) => ms.forEach((m) => m.removedNodes.forEach((n) => n.classList && /^RecentlyWatched/.test(n.classList[1]) && moved.push(n)))).observe(c, { childList: true });
  es.syncRecent(c, MOV, [movie("m1", 0)], "Movies", fakeApi);
  es.syncRecent(c, SER, [show("s1", 0)], "Series", fakeApi);
  assert.equal(c.querySelector("." + MOV).nextSibling, c.querySelector(`[data-hss-placeholder="${MOV}"]`));
  es.render(c, enabled, {}, "en");
  assert.deepEqual(order(c), ["NextUp", "_RecentlyAddedShows", MOV, SER, "BecauseYouWatched-x"]);
  return new Promise((r) => setTimeout(r, 0)).then(() => assert.equal(moved.length, 0));
});

function recordingApi(answer) {
  const calls = [];
  return {
    calls,
    getUrl: (p, q) => { calls.push([p, q]); return p + "?" + new URLSearchParams(q); },
    getJSON: (u) => Promise.resolve({ Items: answer(u) || [] })
  };
}

test("recent: loads both rows with batched queries", async () => {
  const api = recordingApi((u) => {
    const q = new URLSearchParams(u.split("?")[1]);
    if (q.get("IncludeItemTypes") === "Movie") return [movie("m1", 0)];
    if (q.get("IncludeItemTypes") === "Episode" && q.get("IsPlayed") === "true") return [episode("boys", 0.05), episode("done", 2), episode("spec", 4)];
    if (q.get("IncludeItemTypes") === "BoxSet") return [boxset("c1", 0), boxset("c2", 2)];
    if (q.get("Ids")) return [show("boys", 15), show("done", 0), show("spec", 1)];
    if (q.get("ParentIndexNumber") === "0") return [{ Id: "x1", SeriesId: "spec", ParentIndexNumber: 0 }];
    if (q.get("IsUnaired") === "true") return [];
    if (q.get("ParentId") === "c1") return [movie("m7", 1)];
    return null;
  });
  const rows = await es.loadRecent(api, "u1", RNOW);
  assert.deepEqual(ids(rows[MOV]), ["m1"]);
  assert.deepEqual(ids(rows[SER]), ["c1", "done", "spec"]);
  const qs = api.calls.map(([p, q]) => { assert.equal(p, "Users/u1/Items"); return q; });
  assert.equal(qs.length, 7);
  const series = qs.filter((q) => q.Ids);
  assert.equal(series.length, 1);
  assert.deepEqual(series[0].Ids.split(",").sort(), ["boys", "done", "spec"]);
  assert.equal(qs.filter((q) => q.ParentId).length, 1);
  for (const q of qs.filter((q) => q.IncludeItemTypes === "Movie" || (q.IncludeItemTypes === "Episode" && q.IsPlayed === true))) {
    assert.equal(q.SortBy, "DatePlayed");
    assert.equal(q.SortOrder, "Descending");
    assert.equal(q.Recursive, true);
  }
  for (const q of qs.filter((q) => q.IsPlayed === false)) assert.equal(q.ExcludeLocationTypes, "Virtual");
});

test("recent: nothing played = only the three base queries", async () => {
  const api = recordingApi(() => []);
  const rows = await es.loadRecent(api, "u1", RNOW);
  assert.deepEqual(rows, { [MOV]: [], [SER]: [] });
  assert.equal(api.calls.length, 3);
});

test("recent: all candidate series without unwatched episodes = no specials or unaired queries", async () => {
  const api = recordingApi((u) => {
    const q = new URLSearchParams(u.split("?")[1]);
    if (q.get("IncludeItemTypes") === "Episode") return [episode("done", 2)];
    if (q.get("Ids")) return [show("done", 0)];
    return null;
  });
  const rows = await es.loadRecent(api, "u1", RNOW);
  assert.deepEqual(ids(rows[SER]), ["done"]);
  assert.equal(api.calls.length, 4);
});

test("start: fetches settings, names and rows, then renders after the page settles", async () => {
  const dom = new JSDOM('<html lang="en"><body><div class="homeSectionsContainer"></div></body></html>');
  const win = dom.window;
  const urls = [];
  win.HssPageMeta = { Page: 1 };
  win.ApiClient = {
    getCurrentUserId: () => "u1",
    getUrl: (p, q) => { urls.push(p); return p + "?" + new URLSearchParams(q); },
    getJSON: (u) => Promise.resolve(u.startsWith("ModularHomeViews/UserSettings")
      ? { EnabledSections: ["MyMedia", "NextUp"] }
      : u.startsWith("Users/") ? { Items: [] }
      : { Items: [{ Section: "MyMedia", DisplayText: "My Media" }, { Section: "NextUp", DisplayText: "Next Up" }] })
  };
  es.start(win);
  const c = win.document.querySelector(".homeSectionsContainer");
  c.innerHTML = section("MyMedia", 1, 2) + section("NextUp", 3, 0, true);
  await new Promise((r) => setTimeout(r, 1500));
  assert.deepEqual(urls.sort(), ["ModularHomeViews/Sections", "ModularHomeViews/UserSettings", "Users/u1/Items", "Users/u1/Items", "Users/u1/Items"]);
  assert.equal(c.querySelector('[data-hss-placeholder="NextUp"] h2').textContent, "Next Up");
  assert.equal(c.querySelector('[data-hss-placeholder="RecentlyWatchedMovies"] h2').textContent, "Recently Watched Movies");
  assert.equal(c.querySelector('[data-hss-placeholder="RecentlyWatchedSeries"] h2').textContent, "Recently Watched TV Series & Collections");
  assert.equal(c.querySelectorAll("[data-hss-placeholder]").length, 3);
  win.close();
});

test("start: a ticking clock (TV layout) does not hold the run back", async () => {
  const dom = new JSDOM('<html lang="en"><body><div class="currentTimeText"></div><div class="homeSectionsContainer"></div></body></html>');
  const win = dom.window;
  win.HssPageMeta = { Page: 1 };
  win.ApiClient = {
    getCurrentUserId: () => "u1",
    getUrl: (p, q) => p + "?" + new URLSearchParams(q),
    getJSON: (u) => Promise.resolve(u.startsWith("ModularHomeViews/UserSettings") ? { EnabledSections: ["NextUp"] }
      : u.startsWith("Users/") ? { Items: [] } : { Items: [{ Section: "NextUp", DisplayText: "Next Up" }] })
  };
  es.start(win);
  const c = win.document.querySelector(".homeSectionsContainer");
  c.innerHTML = section("NextUp", 3, 0, true);
  const clock = win.document.querySelector(".currentTimeText");
  const tick = setInterval(() => { clock.textContent = String(Date.now()); }, 400);
  await new Promise((r) => setTimeout(r, 3200));
  clearInterval(tick);
  assert.ok(c.querySelector('[data-hss-placeholder="NextUp"]'));
  win.close();
});
