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
  for (const id of ["ContinueWatching", "NextUp", "WatchAgain", "BecauseYouWatched", "HiddenGems", "FinishTheseCollections", "UpcomingShows", "UpcomingMovies", "MyList", "CollectionsSection", "Studio", "Starring", "DirectedBy"]) {
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

test("start: fetches settings and names, then renders after the page settles", async () => {
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
  assert.deepEqual(urls.sort(), ["ModularHomeViews/Sections", "ModularHomeViews/UserSettings", "Users/u1/Items", "Users/u1/Items"]);
  assert.equal(c.querySelector('[data-hss-placeholder="NextUp"] h2').textContent, "Next Up");
  assert.equal(c.querySelector('[data-hss-placeholder="RecentlyWatched"] h2').textContent, "Recently Watched");
  assert.equal(c.querySelectorAll("[data-hss-placeholder]").length, 2);
  win.close();
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

test("watch again and because you watched say what the plugin needs", () => {
  assert.equal(es.hintFor("WatchAgain", "en"), "Nothing here yet — films and series you finished more than 4 weeks ago show up here.");
  assert.equal(es.hintFor("WatchAgain", "de"), "Noch nichts hier – Filme und Serien, die du vor mehr als 4 Wochen fertig geschaut hast, erscheinen hier.");
  assert.equal(es.hintFor("BecauseYouWatched", "en"), "Nothing here yet — finish a film to get recommendations.");
  assert.equal(es.hintFor("BecauseYouWatched", "de"), "Noch nichts hier – schau einen Film fertig, dann gibt es Empfehlungen.");
});

const RNOW = new Date("2026-10-27T22:00:00Z");
const daysAgo = (d) => new Date(RNOW.getTime() - d * 86400000).toISOString();
const movie = (id, d, extra) => ({ Id: id, Name: "Film " + id, Type: "Movie", ServerId: "s1", ProductionYear: 2025, ImageTags: { Thumb: "t" + id }, UserData: { Played: true, LastPlayedDate: d }, ...extra });
const episode = (series, d) => ({ Id: "e" + series + d, Type: "Episode", SeriesId: series, UserData: { Played: true, LastPlayedDate: d } });

test("recent: played films and series by last play, newest first, one card per series", () => {
  const picks = es.pickRecent(
    [movie("m1", daysAgo(0.1)), movie("m2", daysAgo(10))],
    [episode("s1", daysAgo(1)), episode("s1", daysAgo(5)), episode("s2", daysAgo(20)), episode("s1", daysAgo(30))],
    RNOW);
  assert.deepEqual(picks, [{ Id: "m1", Type: "Movie" }, { Id: "s1", Type: "Series" }, { Id: "m2", Type: "Movie" }, { Id: "s2", Type: "Series" }]);
});

test("recent: only the last 60 days, no date = left out, at most 20", () => {
  const old = es.pickRecent([movie("m1", daysAgo(61)), movie("m2", null), movie("m3", daysAgo(59))], [episode("s1", daysAgo(70))], RNOW);
  assert.deepEqual(old, [{ Id: "m3", Type: "Movie" }]);
  const many = Array.from({ length: 30 }, (_, i) => movie("m" + i, daysAgo(i)));
  assert.equal(es.pickRecent(many, [], RNOW).length, 20);
  assert.equal(es.RECENT_DAYS, 60);
  assert.equal(es.RECENT_LIMIT, 20);
});

test("recent: sits right after Watch Again, else at the end", () => {
  assert.deepEqual(es.withRecent(["NextUp", "WatchAgain", "BecauseYouWatched"]), ["NextUp", "WatchAgain", "RecentlyWatched", "BecauseYouWatched"]);
  assert.deepEqual(es.withRecent(["NextUp", "MyList"]), ["NextUp", "MyList", "RecentlyWatched"]);
  assert.deepEqual(es.withRecent(["WatchAgain", "RecentlyWatched"]), ["WatchAgain", "RecentlyWatched"]);
});

test("recent: name and hint in english and german", () => {
  assert.equal(es.recentName("en"), "Recently Watched");
  assert.equal(es.recentName("de-CH"), "Kürzlich angesehen");
  assert.equal(es.hintFor("RecentlyWatched", "en"), "Nothing here yet — films and series you finish show up here.");
  assert.equal(es.hintFor("RecentlyWatched", "de"), "Noch nichts hier – Filme und Serien, die du fertig schaust, erscheinen hier.");
});

const fakeApi = { getScaledImageUrl: (id, o) => `https://jf.example.com/Items/${id}/Images/${o.type}?tag=${o.tag}&fillWidth=${o.fillWidth}` };

test("recent: cards link to the item, show name and year, use the thumb, names are text", () => {
  const c = home(section("WatchAgain", 6, 0, true));
  const items = [movie("m1", daysAgo(0), { Name: "<b>Sirāt</b>" }), { Id: "s1", Name: "Show", Type: "Series", ServerId: "s1", ProductionYear: 2019, BackdropImageTags: ["b1"], ImageTags: {} }];
  es.syncRecent(c, items, "Recently Watched", fakeApi);
  const sec = c.querySelector(".verticalSection.RecentlyWatched[data-hss-client]");
  assert.equal(sec.querySelector("h2.sectionTitle").textContent, "Recently Watched");
  const cards = sec.querySelectorAll(".itemsContainer > .card");
  assert.equal(cards.length, 2);
  assert.equal(cards[0].getAttribute("data-id"), "m1");
  assert.equal(cards[0].getAttribute("data-type"), "Movie");
  assert.equal(cards[0].getAttribute("data-serverid"), "s1");
  assert.equal(cards[0].querySelector("a.cardImageContainer").getAttribute("href"), "#/details?id=m1&serverId=s1");
  assert.match(cards[0].querySelector(".cardImageContainer").style.backgroundImage, /Images\/Thumb\?tag=tm1/);
  assert.equal(cards[0].querySelector(".cardText-first bdi").textContent, "<b>Sirāt</b>");
  assert.equal(cards[0].querySelector("b"), null);
  assert.equal(cards[0].querySelector(".cardText-secondary bdi").textContent, "2025");
  assert.equal(cards[0].querySelector('[data-action="resume"]') !== null, true);
  assert.match(cards[1].querySelector(".cardImageContainer").style.backgroundImage, /Images\/Backdrop\?tag=b1/);
  assert.equal(cards[1].getAttribute("data-isfolder"), "true");
});

test("recent: TV layout gets focusable button cards like Jellyfin's own", () => {
  const c = home("");
  c.ownerDocument.documentElement.classList.add("layout-tv");
  es.syncRecent(c, [movie("m1", daysAgo(0))], "Recently Watched", fakeApi);
  const card = c.querySelector(".RecentlyWatched .card");
  assert.equal(card.tagName, "BUTTON");
  assert.equal(card.getAttribute("data-action"), "link");
  assert.ok(card.classList.contains("itemAction") && card.classList.contains("show-focus"));
  assert.equal(card.querySelector("a"), null);
});

test("recent: no items = no row (placeholder instead), same items = untouched", () => {
  const c = home("");
  es.syncRecent(c, [movie("m1", daysAgo(0))], "Recently Watched", fakeApi);
  const first = c.querySelector(".RecentlyWatched");
  es.syncRecent(c, [movie("m1", daysAgo(0))], "Recently Watched", fakeApi);
  assert.equal(c.querySelector(".RecentlyWatched"), first);
  es.syncRecent(c, [], "Recently Watched", fakeApi);
  assert.equal(c.querySelector(".RecentlyWatched"), null);
});

test("recent: render places the row right after Watch Again, empty = placeholder with hint", () => {
  const enabled = es.withRecent(["NextUp", "WatchAgain", "BecauseYouWatched"]);
  const names = { NextUp: "Next Up", WatchAgain: "Watch Again", BecauseYouWatched: "Because You Watched", RecentlyWatched: "Recently Watched" };
  const c = home(section("NextUp", 3, 1) + section("WatchAgain", 6, 0, true) + section("BecauseYouWatched-x", 7, 2));
  es.syncRecent(c, [movie("m1", daysAgo(0))], "Recently Watched", fakeApi);
  es.render(c, enabled, names, "en");
  assert.deepEqual(order(c), ["NextUp", "_WatchAgain", "RecentlyWatched", "BecauseYouWatched-x"]);
  assert.equal(c.querySelector(".RecentlyWatched").style.order, "6");
  es.syncRecent(c, [], "Recently Watched", fakeApi);
  es.render(c, enabled, names, "en");
  assert.deepEqual(order(c), ["NextUp", "_WatchAgain", "_RecentlyWatched", "BecauseYouWatched-x"]);
  assert.equal(c.querySelector('[data-hss-placeholder="RecentlyWatched"] [data-hss-hint]').textContent, "Nothing here yet — films and series you finish show up here.");
});

test("recent: loads the user's played films and episodes, then the series", async () => {
  const urls = [];
  const api = {
    getUrl: (p, q) => { urls.push([p, q]); return p + "?" + new URLSearchParams(q); },
    getJSON: (u) => Promise.resolve(u.includes("IncludeItemTypes=Movie") ? { Items: [movie("m1", daysAgo(0))] }
      : u.includes("IncludeItemTypes=Episode") ? { Items: [episode("s9", daysAgo(2))] }
      : { Items: [{ Id: "s9", Name: "Show", Type: "Series", ServerId: "s1" }] })
  };
  const items = await es.loadRecent(api, "u1", RNOW);
  assert.deepEqual(items.map((i) => i.Id), ["m1", "s9"]);
  assert.deepEqual(urls.map((u) => u[0]), ["Users/u1/Items", "Users/u1/Items", "Users/u1/Items"]);
  for (const [, q] of urls.slice(0, 2)) {
    assert.equal(q.IsPlayed, true);
    assert.equal(q.SortBy, "DatePlayed");
    assert.equal(q.SortOrder, "Descending");
    assert.equal(q.Recursive, true);
  }
  assert.equal(urls[2][1].Ids, "s9");
});

test("recent: no series played = no series request", async () => {
  let calls = 0;
  const api = { getUrl: (p, q) => p + "?" + new URLSearchParams(q), getJSON: (u) => { calls++; return Promise.resolve({ Items: [] }); } };
  assert.deepEqual(await es.loadRecent(api, "u1", RNOW), []);
  assert.equal(calls, 2);
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

test("recent: a new row is inserted where its placeholder is, so Jellyfin's scroller is attached once", () => {
  const enabled = es.withRecent(["NextUp", "WatchAgain", "BecauseYouWatched"]);
  const c = home(section("NextUp", 3, 1) + section("WatchAgain", 6, 0, true) + section("BecauseYouWatched-x", 7, 2));
  es.render(c, enabled, {}, "en");
  const moved = [];
  new c.ownerDocument.defaultView.MutationObserver((ms) => ms.forEach((m) => m.removedNodes.forEach((n) => n.classList && n.classList.contains("RecentlyWatched") && moved.push(n)))).observe(c, { childList: true });
  es.syncRecent(c, [movie("m1", daysAgo(0))], "Recently Watched", fakeApi);
  const sec = c.querySelector(".RecentlyWatched");
  assert.equal(sec.nextSibling, c.querySelector('[data-hss-placeholder="RecentlyWatched"]'));
  assert.equal(sec.style.order, "6");
  es.render(c, enabled, {}, "en");
  assert.deepEqual(order(c), ["NextUp", "_WatchAgain", "RecentlyWatched", "BecauseYouWatched-x"]);
  return new Promise((r) => setTimeout(r, 0)).then(() => assert.equal(moved.length, 0));
});
