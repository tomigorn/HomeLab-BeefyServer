import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { JSDOM } from "jsdom";

const cr = createRequire(import.meta.url)("./continue-remove.js");

function desktopCard(id, type, name) {
  return `<div class="card overflowBackdropCard card-hoverable" data-isfolder="false" data-id="${id}" data-type="${type}"><div class="cardBox"><div class="cardScalable">` +
    '<div class="cardPadder"></div><a href="#/details?id=' + id + '" data-action="link" class="cardImageContainer cardContent itemAction"></a>' +
    '<div class="cardOverlayContainer itemAction" data-action="link"><button class="cardOverlayButton cardOverlayButton-hover itemAction paper-icon-button-light cardOverlayFab-primary" data-action="resume"></button>' +
    '<div class="cardOverlayButton-br flex"><button class="cardOverlayButton cardOverlayButton-hover itemAction paper-icon-button-light btnPlayed"></button><button class="cardOverlayButton cardOverlayButton-hover itemAction paper-icon-button-light" data-action="menu"></button></div></div>' +
    `</div><div class="cardText cardText-first"><bdi>${name || id}</bdi></div></div></div>`;
}
function touchCard(id, type) {
  return `<div class="card overflowBackdropCard" data-isfolder="false" data-id="${id}" data-type="${type}"><div class="cardBox"><div class="cardScalable">` +
    '<div class="cardPadder"></div><a href="#/details?id=' + id + '" data-action="link" class="cardImageContainer cardContent itemAction"></a>' +
    '<button class="cardOverlayButton cardOverlayButton-br itemAction" data-action="menu"></button>' +
    `</div><div class="cardText"><bdi>${id}</bdi></div></div></div>`;
}
// Home Screen Sections markup: one .verticalSection per section, its id as class.
function section(id, cards) {
  return `<div class="verticalSection ${id} section0"><h2 class="sectionTitle">${id}</h2><div is="emby-itemscontainer" class="itemsContainer scrollSlider" data-monitor="videoplayback,markplayed">${cards}</div></div>`;
}

function setup(html, opts = {}) {
  const win = new JSDOM(`<html lang="${opts.lang || "en"}" class="${opts.cls || ""}"><head></head><body>${html}</body></html>`,
    { url: "https://jellyfin.holy-grail.ch/web/#/home" }).window;
  const calls = [];
  const routes = opts.routes || {};
  win.ApiClient = { getCurrentUserId: () => "u1", accessToken: () => "tok", getUrl: (p, q) => p + (q ? "?" + new URLSearchParams(q) : "") };
  win.fetch = (url, o = {}) => {
    calls.push({ method: o.method || "GET", url, body: o.body ? JSON.parse(o.body) : null, headers: o.headers || {} });
    const key = (o.method || "GET") + " " + url;
    const hit = Object.keys(routes).find((k) => key.startsWith(k));
    let body = hit ? routes[hit] : null;
    if (typeof body === "function") body = body();
    const status = body === undefined || body === null ? 404 : typeof body === "number" ? body : 200;
    return Promise.resolve({ status, json: () => Promise.resolve(body) });
  };
  if (opts.before) opts.before(win);
  cr.start(win, { undoMs: opts.undoMs || 300 });
  const settle = (ms = 200) => new Promise((r) => setTimeout(r, ms));
  return { win, d: win.document, calls, settle };
}

const ROUTES = {
  "GET UserItems/m1/UserData": { PlaybackPositionTicks: 18000000000, Played: false },
  "POST UserItems/m1/UserData": { PlaybackPositionTicks: 0 }
};

test("only Continue watching cards get the remove icon: home row and the Movies/Shows tab row, not Next Up or other rows", async () => {
  const html = section("ContinueWatching", desktopCard("m1", "Movie", "Nosferatu") + desktopCard("e1", "Episode")) +
    section("NextUp", desktopCard("e2", "Episode")) + section("MyList", desktopCard("m2", "Movie")) +
    '<div id="resumableItems" class="itemsContainer">' + desktopCard("m3", "Movie") + "</div>";
  const s = setup(html);
  await s.settle();
  const ids = [...s.d.querySelectorAll(".hssCwRemove")].map((b) => b.closest(".card").getAttribute("data-id"));
  assert.deepEqual(ids, ["m1", "e1", "m3"]);
  const b = s.d.querySelector('[data-id="m1"] .cardOverlayContainer .cardOverlayButton-br .hssCwRemove');
  assert.ok(b, "desktop: with Jellyfin's overlay buttons");
  assert.equal(b.getAttribute("aria-label"), "Nosferatu: Remove from Continue watching");
  assert.equal(b.getAttribute("title"), "Remove from Continue watching");
  s.win.close();
});

test("German label; phones get their own tap target in the image", async () => {
  const s = setup(section("ContinueWatching", touchCard("m1", "Movie")), { lang: "de-CH", before: (w) => { w.matchMedia = (q) => ({ matches: !/hover: hover/.test(q), media: q }); } });
  await s.settle();
  const b = s.d.querySelector('[data-id="m1"] .cardScalable > .hssCwRemove.hssCwRemoveTouch');
  assert.ok(b);
  assert.equal(b.getAttribute("title"), "Aus «Weiterschauen» entfernen");
  s.win.close();
});

test("TV layout: no icon (Moonfin has its own menu there)", async () => {
  const s = setup(section("ContinueWatching", desktopCard("m1", "Movie")), { cls: "layout-tv" });
  await s.settle();
  assert.equal(s.d.querySelectorAll(".hssCwRemove").length, 0);
  s.win.close();
});

test("remove: only the resume position goes to 0, with the user's token; the card leaves the row; the click never reaches the card", async () => {
  const s = setup(section("ContinueWatching", desktopCard("m0", "Movie") + desktopCard("m1", "Movie") + desktopCard("m2", "Movie")), { routes: ROUTES });
  await s.settle();
  let below = 0;
  s.d.querySelector(".itemsContainer").addEventListener("click", () => below++);
  s.d.querySelector('[data-id="m1"] .hssCwRemove').click();
  await s.settle();
  assert.equal(below, 0);
  const post = s.calls.filter((c) => c.method === "POST");
  assert.equal(post.length, 1);
  assert.equal(post[0].url, "UserItems/m1/UserData");
  assert.deepEqual(post[0].body, { PlaybackPositionTicks: 0 }, "nothing else changes: played, play count, last played, favourite stay");
  assert.equal(post[0].headers.Authorization, 'MediaBrowser Token="tok"');
  assert.deepEqual([...s.d.querySelectorAll(".card")].map((c) => c.getAttribute("data-id")), ["m0", "m2"]);
  const t = s.d.querySelector(".hssCwToast");
  assert.match(t.textContent, /Removed from Continue watching/);
  assert.ok(t.querySelector("button.hssCwUndo"));
  s.win.close();
});

test("undo within the toast time puts the old position back and the card back in its place", async () => {
  const s = setup(section("ContinueWatching", desktopCard("m0", "Movie") + desktopCard("m1", "Movie") + desktopCard("m2", "Movie")), { routes: ROUTES, undoMs: 2000 });
  await s.settle();
  s.d.querySelector('[data-id="m1"] .hssCwRemove').click();
  await s.settle();
  s.d.querySelector(".hssCwToast .hssCwUndo").click();
  await s.settle();
  const post = s.calls.filter((c) => c.method === "POST");
  assert.deepEqual(post.map((c) => c.body), [{ PlaybackPositionTicks: 0 }, { PlaybackPositionTicks: 18000000000 }]);
  assert.deepEqual([...s.d.querySelectorAll(".card")].map((c) => c.getAttribute("data-id")), ["m0", "m1", "m2"]);
  assert.equal(s.d.querySelector(".hssCwToast"), null);
  s.win.close();
});

test("undo after Jellyfin refreshed the row (card already back): no duplicate", async () => {
  const s = setup(section("ContinueWatching", desktopCard("m1", "Movie")), { routes: ROUTES, undoMs: 2000 });
  await s.settle();
  s.d.querySelector('[data-id="m1"] .hssCwRemove').click();
  await s.settle();
  s.d.querySelector(".itemsContainer").innerHTML = desktopCard("m1", "Movie");
  s.d.querySelector(".hssCwToast .hssCwUndo").click();
  await s.settle();
  assert.equal(s.d.querySelectorAll('.card[data-id="m1"]').length, 1);
  assert.equal(s.d.querySelectorAll(".hssCwRemove").length, 1);
  s.win.close();
});

test("the toast leaves after its time; a later undo is gone", async () => {
  const s = setup(section("ContinueWatching", desktopCard("m1", "Movie")), { routes: ROUTES, undoMs: 100 });
  await s.settle();
  s.d.querySelector('[data-id="m1"] .hssCwRemove').click();
  await s.settle(400);
  assert.equal(s.d.querySelector(".hssCwToast"), null);
  assert.equal(s.calls.filter((c) => c.method === "POST").length, 1);
  s.win.close();
});

test("a failed write keeps the card and says so", async () => {
  const s = setup(section("ContinueWatching", desktopCard("m1", "Movie")), { routes: { "GET UserItems/m1/UserData": { PlaybackPositionTicks: 5 }, "POST UserItems/m1/UserData": 500 } });
  await s.settle();
  s.d.querySelector('[data-id="m1"] .hssCwRemove').click();
  await s.settle();
  assert.ok(s.d.querySelector('.card[data-id="m1"]'));
  assert.match(s.d.querySelector(".hssCwToast").textContent, /Could not remove/);
  assert.equal(s.d.querySelector(".hssCwToast .hssCwUndo"), null);
  s.win.close();
});

test("an unreadable position writes nothing (no undo point, no blind change)", async () => {
  const s = setup(section("ContinueWatching", desktopCard("m1", "Movie")), { routes: { "GET UserItems/m1/UserData": 500 } });
  await s.settle();
  s.d.querySelector('[data-id="m1"] .hssCwRemove').click();
  await s.settle();
  assert.equal(s.calls.filter((c) => c.method === "POST").length, 0);
  assert.ok(s.d.querySelector('.card[data-id="m1"]'));
  s.win.close();
});

test("re-rendered rows get the icon again, once per card; a reused card follows its new id", async () => {
  const s = setup(section("ContinueWatching", desktopCard("m1", "Movie")));
  await s.settle();
  const list = s.d.querySelector(".itemsContainer");
  list.innerHTML = desktopCard("m1", "Movie") + desktopCard("m2", "Movie");
  await s.settle();
  assert.equal(s.d.querySelectorAll(".hssCwRemove").length, 2);
  const c = s.d.querySelector('[data-id="m2"]');
  c.setAttribute("data-id", "m9");
  c.appendChild(s.d.createElement("span"));
  await s.settle();
  assert.equal(c.querySelector(".hssCwRemove").getAttribute("data-hss-cw"), "m9");
  assert.equal(s.d.querySelectorAll(".hssCwRemove").length, 2);
  s.win.close();
});
