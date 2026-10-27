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
      : { Items: [{ Section: "MyMedia", DisplayText: "My Media" }, { Section: "NextUp", DisplayText: "Next Up" }] })
  };
  es.start(win);
  const c = win.document.querySelector(".homeSectionsContainer");
  c.innerHTML = section("MyMedia", 1, 2) + section("NextUp", 3, 0, true);
  await new Promise((r) => setTimeout(r, 1500));
  assert.deepEqual(urls.sort(), ["ModularHomeViews/Sections", "ModularHomeViews/UserSettings"]);
  assert.equal(c.querySelector('[data-hss-placeholder="NextUp"] h2').textContent, "Next Up");
  assert.equal(c.querySelectorAll("[data-hss-placeholder]").length, 1);
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
