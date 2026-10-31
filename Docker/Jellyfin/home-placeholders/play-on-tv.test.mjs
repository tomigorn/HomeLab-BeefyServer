import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { JSDOM } from "jsdom";

const dt = createRequire(import.meta.url)("./details.js");
const pt = dt.playTv;

const TVS = [
  { Id: "sOwn", UserId: "u1", DeviceId: "dev-browser", DeviceName: "Firefox", Client: "Jellyfin Web", SupportsRemoteControl: true, IsActive: true, NowPlayingItem: { Id: "ep7", Name: "Pilot" } },
  { Id: "sAtv", UserId: "u1", DeviceId: "dev-atv", DeviceName: "Apple TV", Client: "Moonfin", SupportsRemoteControl: true, IsActive: true },
  { Id: "sSony", UserId: "u1", DeviceId: "dev-sony", DeviceName: "Sony BRAVIA", Client: "Moonfin", SupportsRemoteControl: true, IsActive: true, NowPlayingItem: { Name: "Ice Age" } }
];

const DETAIL_BUTTONS = '<div class="mainDetailButtons focuscontainer-x">' +
  '<button class="button-flat btnPlay detailButton" data-action="resume"></button>' +
  '<button class="button-flat btnReplay hide detailButton" data-action="play"></button>' +
  '<button class="button-flat btnDownload hide detailButton"></button>' +
  '<button class="button-flat btnPlayTrailer detailButton"></button>' +
  '<button class="button-flat btnPlaystate detailButton"></button>' +
  '<button class="button-flat btnUserRating detailButton"></button>' +
  '<button class="button-flat btnMoreCommands detailButton"></button></div>';
const DETAIL_PAGE = '<div id="itemDetailPage" data-role="page" class="page libraryPage itemDetailPage">' + DETAIL_BUTTONS + "</div>";

// Jellyfin cardBuilder markup: desktop has the hover overlay, phones do not (cardOverlayButton-br buttons straight in cardScalable, often none).
function desktopCard(id, type, name) {
  return `<div class="card overflowBackdropCard card-hoverable" data-isfolder="false" data-id="${id}" data-type="${type}"><div class="cardBox"><div class="cardScalable">` +
    '<div class="cardPadder"></div><a href="#/details?id=' + id + '" data-action="link" class="cardImageContainer cardContent itemAction"></a>' +
    '<div class="cardOverlayContainer itemAction" data-action="link"><button class="cardOverlayButton cardOverlayButton-hover itemAction paper-icon-button-light cardOverlayFab-primary" data-action="resume"></button>' +
    '<div class="cardOverlayButton-br flex"><button class="cardOverlayButton cardOverlayButton-hover itemAction paper-icon-button-light btnPlayed"></button><button class="cardOverlayButton cardOverlayButton-hover itemAction paper-icon-button-light" data-action="menu"></button></div></div>' +
    `</div><div class="cardText cardText-first"><bdi>${name || id}</bdi></div></div></div>`;
}
function touchCard(id, type, more) {
  return `<div class="card overflowBackdropCard" data-isfolder="false" data-id="${id}" data-type="${type}"><div class="cardBox"><div class="cardScalable">` +
    '<div class="cardPadder"></div><a href="#/details?id=' + id + '" data-action="link" class="cardImageContainer cardContent itemAction"></a>' +
    (more ? '<button class="cardOverlayButton cardOverlayButton-br itemAction" data-action="menu"></button>' : "") +
    `</div><div class="cardText"><bdi>${id}</bdi></div></div></div>`;
}
function listItem(id, type) {
  return `<div class="listItem listItem-largeImage" data-id="${id}" data-type="${type}"><div class="listItemBody"><div class="listItemBodyText">${id} title</div></div>` +
    '<div class="listViewUserDataButtons"><button class="listItemButton paper-icon-button-light btnPlayed"></button><button class="listItemButton itemAction" data-action="menu"></button></div></div>';
}

function setup(html, opts = {}) {
  const win = new JSDOM(`<html lang="${opts.lang || "en"}" class="${opts.cls || ""}"><head></head><body><div class="skinHeader"><button class="headerButton headerBackButton"></button></div>${html}</body></html>`,
    { url: "https://jellyfin.holy-grail.ch/web/" + (opts.hash || "#/home") }).window;
  const calls = [];
  const routes = opts.routes || {};
  win.ApiClient = { getCurrentUserId: () => "u1", accessToken: () => "tok", deviceId: () => "dev-browser", getUrl: (p, q) => p + (q ? "?" + new URLSearchParams(q) : "") };
  win.fetch = (url, o) => {
    calls.push([(o && o.method) || "GET", url]);
    const hit = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => url.startsWith(k));
    const body = hit ? routes[hit] : null;
    const status = body === undefined || body === null ? 404 : typeof body === "number" ? body : 200;
    return Promise.resolve({ status, json: () => Promise.resolve(body) });
  };
  if (opts.hide) win.HssTrendingHide = opts.hide;
  if (opts.before) opts.before(win);
  dt.start(win);
  const settle = () => new Promise((r) => setTimeout(r, 250));
  return { win, d: win.document, calls, settle };
}

const pick = async (s, btn, tv = 0) => {
  btn.click();
  await s.settle();
  s.d.querySelectorAll(".hssPlayMenu .hssPlayTarget")[tv].click();
  await s.settle();
};

test("details page: a TV button with label after play/replay, for the item in the URL, once", async () => {
  const s = setup(DETAIL_PAGE, { hash: "#/details?id=jf121&serverId=x", routes: { "Users/u1/Items/jf121": { Id: "jf121", Type: "Movie", Name: "Ikiru" } } });
  await s.settle();
  const b = s.d.querySelectorAll(".mainDetailButtons .hssDetailTv");
  assert.equal(b.length, 1);
  assert.equal(b[0].previousElementSibling.className.includes("btnReplay"), true, "right after play / replay");
  assert.equal(b[0].getAttribute("data-hss-play"), "jf121");
  assert.equal(b[0].getAttribute("data-hss-play-type"), "Movie");
  assert.ok(b[0].querySelector(".material-icons.tv"));
  assert.equal(b[0].querySelector(".hssDetailTvLabel").textContent, "Play on TV");
  assert.equal(b[0].getAttribute("title"), "Play on TV");
  s.d.querySelector(".mainDetailButtons").appendChild(s.d.createElement("span"));
  s.win.dispatchEvent(new s.win.HashChangeEvent("hashchange"));
  await s.settle();
  assert.equal(s.d.querySelectorAll(".hssDetailTv").length, 1, "no duplicate after more mutations");
  assert.equal(s.calls.filter((c) => c[1] === "Users/u1/Items/jf121").length, 1, "item looked up once");
  s.win.close();
});

test("details page: German label; the button follows the URL to the next title on the same page", async () => {
  const s = setup(DETAIL_PAGE, { lang: "de", hash: "#/details?id=jf121", routes: { "Users/u1/Items/jf121": { Id: "jf121", Type: "Movie" }, "Users/u1/Items/ep9": { Id: "ep9", Type: "Episode" } } });
  await s.settle();
  assert.equal(s.d.querySelector(".hssDetailTvLabel").textContent, "Auf TV abspielen");
  s.win.history.pushState({}, "", "#/details?id=ep9");
  s.d.body.appendChild(s.d.createElement("div"));
  await s.settle();
  const b = s.d.querySelectorAll(".hssDetailTv");
  assert.equal(b.length, 1);
  assert.equal(b[0].getAttribute("data-hss-play"), "ep9");
  assert.equal(b[0].getAttribute("data-hss-play-type"), "Episode");
  s.win.close();
});

test("details page: no button for collections, people, placeholders (also their seasons), hidden pages or the TV layout", async () => {
  const types = [["bs1", { Id: "bs1", Type: "BoxSet" }], ["p1", { Id: "p1", Type: "Person" }], ["ph1", { Id: "ph1", Type: "Movie" }], ["phs", { Id: "phs", Type: "Season", SeriesId: "phSeries" }]];
  for (const [id, item] of types) {
    const s = setup(DETAIL_PAGE, { hash: "#/details?id=" + id, routes: { ["Users/u1/Items/" + id]: item }, hide: { lib: ["libT"], sets: ["set1"], items: ["ph1", "phSeries"] } });
    await s.settle();
    assert.equal(s.d.querySelectorAll(".hssDetailTv").length, 0, id);
    s.win.close();
  }
  const hidden = setup(DETAIL_PAGE.replace("page libraryPage", "page libraryPage hide"), { hash: "#/details?id=jf121", routes: { "Users/u1/Items/jf121": { Id: "jf121", Type: "Movie" } } });
  await hidden.settle();
  assert.equal(hidden.d.querySelectorAll(".hssDetailTv").length, 0, "cached hidden page");
  hidden.win.close();
  const tv = setup(DETAIL_PAGE + desktopCard("m1", "Movie") + touchCard("m2", "Movie") + listItem("e1", "Episode"), { cls: "layout-tv", hash: "#/details?id=jf121", routes: { "Users/u1/Items/jf121": { Id: "jf121", Type: "Movie" } } });
  await tv.settle();
  assert.equal(tv.d.querySelectorAll("[data-hss-play]").length, 0, "TV layout: nothing");
  assert.match(dt.CSS, /\.layout-tv \.hssDetailTv, \.layout-tv \.hssCardTv, \.layout-tv \.hssListTv, \.layout-tv \.hssOsdTv \{ display: none !important; \}/);
  tv.win.close();
});

test("details page: an episode plays itself from its resume point", async () => {
  const s = setup(DETAIL_PAGE, { hash: "#/details?id=ep3", routes: { "Sessions?": TVS, "Users/u1/Items/ep3": { Id: "ep3", Type: "Episode", UserData: { PlaybackPositionTicks: 1234 } }, "Sessions/sAtv/Playing": 204 } });
  await s.settle();
  await pick(s, s.d.querySelector(".hssDetailTv"));
  assert.ok(s.calls.some((c) => c[0] === "POST" && c[1] === "Sessions/sAtv/Playing?playCommand=PlayNow&itemIds=ep3&startPositionTicks=1234"));
  s.win.close();
});

test("season: the episode in progress, else the next unwatched, else the first episode", async () => {
  const base = { "Sessions?": TVS, "Users/u1/Items/se2": { Id: "se2", Type: "Season", SeriesId: "show1" }, "Sessions/sAtv/Playing": 204 };
  const eps = (played) => ({ Items: [{ Id: "e1", UserData: { Played: played[0] } }, { Id: "e2", UserData: { Played: played[1] } }, { Id: "e3", UserData: { Played: played[2] } }] });
  const cases = [
    [{ "Users/u1/Items/Resume": { Items: [{ Id: "e2", UserData: { PlaybackPositionTicks: 77 } }] } }, "itemIds=e2&startPositionTicks=77"],
    [{ "Users/u1/Items/Resume": { Items: [] }, "Shows/show1/Episodes": eps([true, false, false]) }, "itemIds=e2"],
    [{ "Users/u1/Items/Resume": { Items: [] }, "Shows/show1/Episodes": eps([true, true, true]) }, "itemIds=e1"]
  ];
  for (const [extra, want] of cases) {
    const s = setup(DETAIL_PAGE, { hash: "#/details?id=se2", routes: Object.assign({}, base, extra) });
    await s.settle();
    await pick(s, s.d.querySelector(".hssDetailTv"));
    const urls = s.calls.map((c) => c[1]);
    assert.ok(urls.includes("Users/u1/Items/Resume?ParentId=se2&Limit=1&IncludeItemTypes=Episode&Recursive=true"));
    if (extra["Shows/show1/Episodes"]) assert.ok(urls.includes("Shows/show1/Episodes?SeasonId=se2&UserId=u1&IsMissing=false"));
    assert.equal(urls.at(-1), "Sessions/sAtv/Playing?playCommand=PlayNow&" + want);
    s.win.close();
  }
});

test("cards: a TV icon next to Jellyfin's overlay buttons (desktop) or as its own tap target (phone), only for playable items", async () => {
  const html = '<div class="itemsContainer">' + desktopCard("m1", "Movie", "Ikiru") + touchCard("e1", "Episode", true) + touchCard("s1", "Series") +
    desktopCard("f1", "Folder") + desktopCard("pe1", "Person") + desktopCard("g1", "Genre") + desktopCard("st1", "Studio") + desktopCard("bs1", "BoxSet") + desktopCard("cf1", "CollectionFolder") +
    desktopCard("ph1", "Movie") + desktopCard("phEp", "Episode") +
    '<div class="card hssComingCard" data-id="c1" data-type="Movie"><div class="cardBox"><div class="cardScalable"></div></div></div>' +
    '<div class="card"><div class="cardBox"><div class="cardScalable"></div></div></div></div>';
  const s = setup(html, { hide: { lib: [], sets: [], items: ["ph1", "phEp"] } });
  await s.settle();
  const m1 = s.d.querySelector('[data-id="m1"]');
  const br = m1.querySelector(".cardOverlayContainer .cardOverlayButton-br");
  assert.ok(br.firstElementChild.classList.contains("hssCardTv"), "first of the bottom-right overlay buttons");
  assert.ok(br.firstElementChild.querySelector(".material-icons.tv"));
  assert.equal(br.firstElementChild.getAttribute("aria-label"), "Ikiru: Play on TV");
  const e1 = s.d.querySelector('[data-id="e1"] .cardScalable > .hssCardTv.hssCardTvTouch');
  assert.ok(e1, "phone card: own button in the image");
  assert.equal(e1.getAttribute("data-hss-play"), "e1");
  assert.equal(e1.getAttribute("data-hss-play-type"), "Episode");
  assert.ok(s.d.querySelector('[data-id="s1"] .hssCardTvTouch'), "series card without overlay buttons");
  for (const id of ["f1", "pe1", "g1", "st1", "bs1", "cf1", "ph1", "phEp"]) assert.equal(s.d.querySelector(`[data-id="${id}"] .hssCardTv`), null, id);
  assert.equal(s.d.querySelector('[data-id="c1"] [data-hss-play]'), null, "Coming soon card: no TV icon");
  assert.equal(s.d.querySelectorAll(".hssCardTv").length, 3);
  assert.equal(s.d.querySelectorAll(".hssPlayTv").length, 0, "no full-width button added");
  s.win.close();
});

test("cards: our recent cards (overlay with only the play button) get a bottom-right group", async () => {
  const html = '<div class="card" data-id="r1" data-type="Movie"><div class="cardBox"><div class="cardScalable"><div class="cardOverlayContainer itemAction" data-action="link"><button class="cardOverlayButton cardOverlayFab-primary"></button></div></div></div></div>';
  const s = setup(html);
  await s.settle();
  assert.ok(s.d.querySelector(".cardOverlayContainer > .cardOverlayButton-br.flex > .hssCardTv"));
  s.win.close();
});

test("cards: a tap opens the menu without following the card; an episode card plays that episode", async () => {
  const s = setup('<div class="itemsContainer">' + touchCard("e5", "Episode", true) + "</div>", { routes: { "Sessions?": TVS, "Users/u1/Items/e5": { Id: "e5", Type: "Episode", UserData: { PlaybackPositionTicks: 50 } }, "Sessions/sSony/Playing": 204 } });
  await s.settle();
  let below = 0;
  s.d.querySelector(".itemsContainer").addEventListener("click", () => below++);
  await pick(s, s.d.querySelector(".hssCardTv"), 1);
  assert.equal(below, 0, "Jellyfin's card click never fires");
  assert.equal(s.calls.at(-1)[1], "Sessions/sSony/Playing?playCommand=PlayNow&itemIds=e5&startPositionTicks=50");
  assert.equal(s.d.querySelector(".hssPlayMenuStatus").textContent, "Playing on Sony BRAVIA · Moonfin");
  s.win.close();
});

test("episode lists: the TV icon first in the row's buttons; placeholders skipped", async () => {
  const s = setup('<div class="itemsContainer vertical-list">' + listItem("e1", "Episode") + listItem("phEp", "Episode") + listItem("a1", "Audio") + "</div>", { lang: "de", hide: { items: ["phEp"] } });
  await s.settle();
  const b = s.d.querySelector('[data-id="e1"] .listViewUserDataButtons').firstElementChild;
  assert.ok(b.classList.contains("hssListTv"));
  assert.equal(b.getAttribute("aria-label"), "e1 title: Auf TV abspielen");
  assert.equal(s.d.querySelectorAll(".hssListTv").length, 1);
  s.win.close();
});

test("big grids: a mutation storm is one batched pass, every card gets exactly one button", async () => {
  const s = setup('<div class="itemsContainer" id="grid"></div>');
  await s.settle();
  const before = s.win.HssPlayTv.stats.scans;
  const grid = s.d.getElementById("grid");
  for (let i = 0; i < 400; i++) grid.insertAdjacentHTML("beforeend", desktopCard("m" + i, "Movie"));
  await s.settle();
  assert.equal(s.win.HssPlayTv.stats.scans - before, 1);
  assert.equal(s.d.querySelectorAll(".hssCardTv").length, 400);
  grid.insertAdjacentHTML("beforeend", desktopCard("late", "Movie"));
  await s.settle();
  assert.equal(s.d.querySelectorAll(".hssCardTv").length, 401);
  assert.equal(s.d.querySelectorAll('[data-id="m0"] .hssCardTv').length, 1);
  s.win.close();
});

test("several TVs: always the menu, also with one TV (no auto-play); a busy TV says what plays; none = hint", async () => {
  const one = setup(touchCard("m1", "Movie"), { routes: { "Sessions?": TVS.slice(0, 2), "Users/u1/Items/m1": { Id: "m1" }, "Sessions/sAtv/Playing": 204 } });
  await one.settle();
  one.d.querySelector(".hssCardTv").click();
  await one.settle();
  assert.equal(one.d.querySelectorAll(".hssPlayTarget").length, 1);
  assert.equal(one.calls.filter((c) => c[0] === "POST").length, 0, "nothing starts before a TV is picked");
  one.win.close();
  const two = setup(touchCard("m1", "Movie"), { routes: { "Sessions?": TVS } });
  await two.settle();
  two.d.querySelector(".hssCardTv").click();
  await two.settle();
  const names = [...two.d.querySelectorAll(".hssPlayTarget")].map((b) => b.textContent);
  assert.deepEqual(names, ["Apple TV · Moonfin", "Sony BRAVIA · Moonfinnow playing: Ice Age"]);
  two.win.close();
  const none = setup(touchCard("m1", "Movie"), { routes: { "Sessions?": [TVS[0]] } });
  await none.settle();
  none.d.querySelector(".hssCardTv").click();
  await none.settle();
  assert.equal(none.d.querySelector(".hssPlayMenuStatus").textContent, "No TV online. Open Moonfin on the TV first.");
  none.win.close();
});

test("stream offset: only a non-HLS transcode without copyTimestamps starts its clock at StartTimeTicks", () => {
  assert.equal(pt.offset("blob:https://jellyfin.holy-grail.ch/123"), 0);
  assert.equal(pt.offset("https://j/videos/abc/master.m3u8?StartTimeTicks=500"), 0);
  assert.equal(pt.offset("https://j/Videos/abc/stream.mkv?Static=true&mediaSourceId=abc"), 0);
  assert.equal(pt.offset("https://j/Videos/abc/stream.mp4?StartTimeTicks=6000000000&CopyTimestamps=true"), 0);
  assert.equal(pt.offset("https://j/Videos/abc/stream.mp4?VideoCodec=h264&StartTimeTicks=6000000000"), 6000000000);
  assert.equal(pt.offset("https://j/Videos/abc/stream.webm?starttimeticks=42"), 42);
  assert.equal(pt.offset(""), 0);
  assert.equal(pt.offset("https://j/Videos/abc/stream.mp4?StartTimeTicks=x"), 0);
});

const OSD = '<div id="videoOsdPage" data-role="page" class="page libraryPage"><div class="videoOsdBottom"><div class="osdControls"><div class="buttons focuscontainer-x">' +
  '<div dir="ltr"><button class="btnPause autoSize"></button></div><button class="btnSubtitles hide autoSize"></button><button class="btnAudio autoSize"></button>' +
  '<button class="btnVideoOsdSettings autoSize"></button><button class="btnFullscreen autoSize"></button></div></div></div></div>' +
  '<div class="videoPlayerContainer"><video class="htmlvideoplayer"></video></div>';

function osd(opts) {
  return setup(OSD, Object.assign({ hash: "#/video", before: (w) => {
    const v = w.document.querySelector("video");
    Object.defineProperty(v, "currentTime", { value: opts.time, configurable: true });
    Object.defineProperty(v, "currentSrc", { value: opts.src, configurable: true });
  } }, opts));
}

test("player: Continue on TV sits before the settings button, once", async () => {
  const s = osd({ time: 1, src: "", lang: "de" });
  await s.settle();
  const b = s.d.querySelectorAll(".hssOsdTv");
  assert.equal(b.length, 1);
  assert.ok(b[0].nextElementSibling.classList.contains("btnVideoOsdSettings"));
  assert.equal(b[0].getAttribute("title"), "Auf TV weiterschauen");
  assert.equal(b[0].getAttribute("data-hss-play"), "current");
  s.d.querySelector(".buttons").appendChild(s.d.createElement("span"));
  await s.settle();
  assert.equal(s.d.querySelectorAll(".hssOsdTv").length, 1);
  s.win.close();
});

test("player: picking a TV plays the current item at the current position, then stops here and says where it plays", async () => {
  const s = osd({ time: 120.5, src: "https://jellyfin.holy-grail.ch/Videos/ep7/stream.mp4?VideoCodec=h264&StartTimeTicks=6000000000", routes: { "Sessions?": TVS, "Sessions/sAtv/Playing": 204 } });
  await s.settle();
  let back = 0;
  s.d.querySelector(".headerBackButton").addEventListener("click", () => back++);
  await pick(s, s.d.querySelector(".hssOsdTv"));
  const urls = s.calls.map((c) => c[0] + " " + c[1]);
  assert.ok(urls.includes("GET Sessions?DeviceId=dev-browser"), "own session names the item");
  assert.equal(urls.at(-1), "POST Sessions/sAtv/Playing?playCommand=PlayNow&itemIds=ep7&startPositionTicks=7205000000");
  assert.equal(back, 1, "local playback stopped through Jellyfin's back (PlaybackStopped saves the position)");
  assert.equal(s.d.querySelector(".hssToast").textContent, "Playing on Apple TV · Moonfin");
  s.win.close();
});

test("player: direct play position, item from the stream URL when the session has none; a failed start keeps playing here", async () => {
  const s = osd({ time: 10, src: "https://jellyfin.holy-grail.ch/Videos/0123456789abcdef0123456789abcdef/stream.mkv?Static=true", routes: { "Sessions?ControllableByUserId": TVS, "Sessions?DeviceId": [], "Sessions/sAtv/Playing": 500 } });
  await s.settle();
  let back = 0;
  s.d.querySelector(".headerBackButton").addEventListener("click", () => back++);
  await pick(s, s.d.querySelector(".hssOsdTv"));
  assert.equal(s.calls.at(-1)[1], "Sessions/sAtv/Playing?playCommand=PlayNow&itemIds=0123456789abcdef0123456789abcdef&startPositionTicks=100000000");
  assert.equal(back, 0);
  assert.equal(s.d.querySelector(".hssToast"), null);
  assert.equal(s.d.querySelector(".hssPlayMenuStatus").textContent, "Could not start playback on Apple TV · Moonfin");
  s.win.close();
});

test("player: no back button = history back", async () => {
  const s = osd({ time: 2, src: "blob:x", routes: { "Sessions?": TVS, "Sessions/sAtv/Playing": 204 } });
  s.d.querySelector(".headerBackButton").classList.add("hide");
  let backs = 0;
  s.win.history.back = () => backs++;
  await s.settle();
  await pick(s, s.d.querySelector(".hssOsdTv"));
  assert.equal(s.calls.at(-1)[1], "Sessions/sAtv/Playing?playCommand=PlayNow&itemIds=ep7&startPositionTicks=20000000");
  assert.equal(backs, 1);
  s.win.close();
});

test("placeholder pages: the seasons of a Trending show get no icon; a real show's seasons do", async () => {
  const page = (id) => '<div id="itemDetailPage" data-role="page" class="page libraryPage itemDetailPage">' + DETAIL_BUTTONS + '<div id="childrenContent">' + desktopCard("sx-" + id, "Season") + "</div></div>";
  const ph = setup(page("ph"), { hash: "#/details?id=phSeries", routes: { "Users/u1/Items/phSeries": { Id: "phSeries", Type: "Series" } }, hide: { items: ["phSeries"] } });
  const errors = [];
  ph.win.addEventListener("error", (e) => errors.push(e.message));
  await ph.settle();
  await ph.settle();
  assert.equal(ph.d.querySelectorAll("[data-hss-play]").length, 0);
  assert.equal(ph.d.querySelector('[data-id="sx-ph"]').getAttribute("data-hss-tv"), "-sx-ph", "decided once");
  assert.deepEqual(errors, []);
  ph.win.close();
  const real = setup(page("real"), { hash: "#/details?id=show1", routes: { "Users/u1/Items/show1": { Id: "show1", Type: "Series" } }, hide: { items: ["phSeries"] } });
  await real.settle();
  await real.settle();
  assert.equal(real.d.querySelectorAll(".hssCardTv").length, 1);
  assert.equal(real.d.querySelectorAll(".hssDetailTv").length, 1);
  real.win.close();
});

test("text-only changes (the player clock) cause no extra passes", async () => {
  const s = setup(OSD.replace('<div class="buttons', '<div class="osdPositionText"></div><div class="buttons'), { hash: "#/video" });
  await s.settle();
  const before = s.win.HssPlayTv.stats.scans;
  const clock = s.d.querySelector(".osdPositionText");
  for (let i = 0; i < 5; i++) {
    clock.textContent = "0:0" + i;
    await new Promise((r) => setTimeout(r, 150));
  }
  assert.equal(s.win.HssPlayTv.stats.scans, before);
  s.win.close();
});

test("details page: an item that cannot be read gets no button and no retry storm", async () => {
  const s = setup(DETAIL_PAGE + desktopCard("m1", "Movie"), { hash: "#/details?id=gone" });
  await s.settle();
  await s.settle();
  await s.settle();
  assert.equal(s.d.querySelectorAll(".hssDetailTv").length, 0);
  assert.equal(s.calls.filter((c) => c[1] === "Users/u1/Items/gone").length, 1);
  assert.ok(s.win.HssPlayTv.stats.scans <= 4, "scans " + s.win.HssPlayTv.stats.scans);
  s.win.close();
});

test("re-rendered cards (Jellyfin's React grids) get the icon back; a card reused for another item follows it", async () => {
  const s = setup('<div class="itemsContainer" id="grid">' + desktopCard("m1", "Movie") + desktopCard("m2", "Movie") + "</div>");
  await s.settle();
  const m1 = s.d.querySelector('[data-id="m1"]');
  const br = m1.querySelector(".cardOverlayButton-br");
  br.replaceWith(br.cloneNode(false));
  await s.settle();
  assert.equal(m1.querySelectorAll(".hssCardTv").length, 1, "icon back after the overlay was rebuilt");
  const m2 = s.d.querySelector('[data-id="m2"]');
  m2.setAttribute("data-id", "e9");
  m2.setAttribute("data-type", "Episode");
  m2.querySelector(".cardText").appendChild(s.d.createElement("span"));
  await s.settle();
  const b = m2.querySelectorAll(".hssCardTv");
  assert.equal(b.length, 1);
  assert.equal(b[0].getAttribute("data-hss-play"), "e9");
  assert.equal(b[0].getAttribute("data-hss-play-type"), "Episode");
  m2.setAttribute("data-id", "pe1");
  m2.setAttribute("data-type", "Person");
  m2.querySelector(".cardText").appendChild(s.d.createElement("span"));
  await s.settle();
  assert.equal(m2.querySelectorAll(".hssCardTv").length, 0, "reused for a person: icon gone");
  s.win.close();
});

test("collection pages: real members get the icon, Trending placeholders in the same set do not", async () => {
  const page = '<div id="itemDetailPage" data-role="page" class="page libraryPage itemDetailPage">' + DETAIL_BUTTONS + '<div class="collectionItems">' + desktopCard("real1", "Movie") + desktopCard("ph1", "Movie") + "</div></div>";
  const s = setup(page, { hash: "#/details?id=set1", routes: { "Users/u1/Items/set1": { Id: "set1", Type: "BoxSet" } }, hide: { lib: ["libT"], sets: ["set1"], items: ["ph1"] } });
  const errors = [];
  s.win.addEventListener("error", (e) => errors.push(e.message));
  await s.settle();
  await s.settle();
  assert.equal(s.d.querySelectorAll('[data-id="real1"] .hssCardTv').length, 1);
  assert.equal(s.d.querySelectorAll('[data-id="ph1"] .hssCardTv').length, 0);
  assert.equal(s.d.querySelectorAll(".hssDetailTv").length, 0, "no button for the set itself");
  assert.deepEqual(errors, []);
  s.win.close();
});

test("touch screens: a card that still carries a hover overlay gets the tap target in the image", async () => {
  const s = setup(desktopCard("m1", "Movie"), { before: (w) => { w.matchMedia = (q) => ({ matches: !/hover: hover/.test(q), media: q }); } });
  await s.settle();
  assert.equal(s.d.querySelectorAll(".cardOverlayContainer .hssCardTv").length, 0);
  assert.equal(s.d.querySelectorAll(".cardScalable > .hssCardTvTouch").length, 1);
  s.win.close();
});
