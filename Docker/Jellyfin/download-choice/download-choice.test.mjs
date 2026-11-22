import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { itemOf, swapItem, formatSize, choices, plan, texts, authHeader } = createRequire(import.meta.url)("./download-choice.js");
const ORIGIN = "https://jellyfin.holy-grail.ch";
const ID = "6368815bff4f9df65ec668a9c397cc6e";
const PHONE = "cac6604c30b35d9bba1ba76cb5a60f2b";
const URL1 = `${ORIGIN}/Items/${ID}/Download?api_key=tok`;

test("only same-origin item downloads are caught", () => {
  assert.equal(itemOf(URL1, ORIGIN), ID);
  assert.equal(itemOf(`${ORIGIN}/emby/items/${ID.toUpperCase()}/download?api_key=tok`, ORIGIN), ID);
  assert.equal(itemOf(`https://evil.example.com/Items/${ID}/Download`, ORIGIN), null);
  assert.equal(itemOf(`${ORIGIN}/Items/${ID}/Images/Primary`, ORIGIN), null);
  assert.equal(itemOf(`${ORIGIN}/Videos/${ID}/stream?static=true`, ORIGIN), null);
  assert.equal(itemOf(`${ORIGIN}/Items/xyz/Download`, ORIGIN), null);
  assert.equal(itemOf("not a url", ORIGIN), null);
  assert.equal(itemOf(undefined, ORIGIN), null);
});

test("the phone version is the same download URL with the version's id", () => {
  assert.equal(swapItem(URL1, ID, PHONE), `${ORIGIN}/Items/${PHONE}/Download?api_key=tok`);
  assert.equal(swapItem(`${ORIGIN}/emby/items/${ID}/download?api_key=tok`, ID, PHONE), `${ORIGIN}/emby/Items/${PHONE}/Download?api_key=tok`);
  assert.equal(swapItem(URL1, ID, ID), URL1);
});

test("sizes in GB with one decimal, MB below 1 GB, German comma", () => {
  assert.equal(formatSize(29363263741, "en"), "29.4 GB");
  assert.equal(formatSize(4384001056, "de"), "4,4 GB");
  assert.equal(formatSize(734000000, "en"), "734 MB");
  assert.equal(formatSize(null, "en"), "");
});

const ready = { name: "Godzilla Minus One", full: { size: 29363263741, label: "4K HDR" }, phone: { state: "ready", size: 4384001056, label: "1080p HDR" } };

test("one title: full file and its ready phone version", () => {
  const c = choices({ [ID]: ready }, "en");
  assert.equal(c.title, "Godzilla Minus One");
  assert.deepEqual(c.options.map((o) => [o.value, o.label, o.detail]), [["full", "Full file", "4K HDR · 29.4 GB"], ["phone", "Phone version", "1080p HDR · 4.4 GB"]]);
});

test("one title without a phone version yet: made on request", () => {
  const c = choices({ [ID]: { ...ready, phone: { state: "order" } } }, "en");
  assert.equal(c.options[1].detail, "smaller, made on request (usually 5–30 min)");
  const d = choices({ [ID]: { ...ready, phone: { state: "preparing", what: "42 % downloaded" } } }, "de");
  assert.deepEqual([d.options[0].label, d.options[1].label, d.options[1].detail], ["Ganze Datei", "Handy-Version", "wird vorbereitet: 42 % downloaded"]);
});

test("several titles: counts and the sum of what downloads now", () => {
  const items = {
    a: ready,
    b: { name: "Nosferatu", full: { size: 2100000000, label: "1080p" }, phone: null },
    c: { name: "X", full: { size: 30000000000, label: "4K" }, phone: { state: "order" } },
  };
  const c = choices(items, "en");
  assert.equal(c.title, "3 titles");
  assert.equal(c.options[0].detail, "61.5 GB");
  assert.equal(c.options[1].detail, "6.5 GB now · 1 made on request");
  assert.equal(choices(items, "de").options[1].detail, "6,5 GB jetzt · 1 auf Anfrage");
});

test("after picking the phone version: what to download and what to say", () => {
  const urls = { [ID]: URL1, b: `${ORIGIN}/Items/b/Download?api_key=tok` };
  const p = plan({ downloads: [{ item: ID, file: PHONE }], preparing: [], ordered: [] }, urls, "en");
  assert.deepEqual(p, { downloads: [{ item: ID, url: `${ORIGIN}/Items/${PHONE}/Download?api_key=tok` }], message: null });
  const q = plan({ downloads: [], preparing: [], ordered: [ID] }, urls, "en");
  assert.deepEqual(q.downloads, []);
  assert.equal(q.message, texts("en").ordered);
  const r = plan({ downloads: [], preparing: [{ item: ID, what: "37 % converted" }], ordered: [] }, urls, "en");
  assert.equal(r.message, texts("en").preparing.replace("{what}", "37 % converted"));
  assert.deepEqual(plan({ downloads: [{ item: "unknown", file: PHONE }] }, urls, "en").downloads, []);
});

test("German or English by the page language", () => {
  assert.equal(texts("de-CH").cancel, "Abbrechen");
  assert.equal(texts("en-US").cancel, "Cancel");
  assert.equal(texts("").cancel, "Cancel");
  for (const lang of ["en", "de"]) assert.ok(texts(lang).ordered.includes("✅"), lang);
});

test("the request names the web client like Jellyfin's own API client does (names as it stores them, already URL-encoded)", () => {
  const api = { appName: () => "Jellyfin%20Web", deviceName: () => "Edge%20Chromium", deviceId: () => "dev1", appVersion: () => "12.4.0", accessToken: () => "tok" };
  assert.equal(authHeader(api), 'MediaBrowser Client="Jellyfin%20Web", Device="Edge%20Chromium", DeviceId="dev1", Version="12.4.0", Token="tok"');
});
