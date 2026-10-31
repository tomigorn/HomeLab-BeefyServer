import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, tick } from "./fixture.mjs";

const RESET = new Date(Date.now() + 3600e3).toISOString();
const SUBDL = { name: "SubDL", id: "subdl", window: "utc-day", status: "ok", level: "ok",
  searches: { used: 300, limit: 2000, level: "ok", resetsAt: RESET, approx: true }, downloads: { used: 12, limit: 50, level: "ok", resetsAt: RESET } };
const OPENSUBS = { name: "OpenSubtitles", id: "opensubtitlescom", window: "utc-day", status: "ok", level: "ok",
  searches: { used: 0, limit: null, level: "ok", resetsAt: null }, downloads: { used: 20, limit: null, level: "ok", resetsAt: null } };

function usage(subtitles, extra = {}) {
  return { ok: true, generated: new Date().toISOString(), indexers: [], subscriptions: [], storage: [], subtitles, subtitlesOk: true, ...extra };
}

const open = [];
afterEach(() => { while (open.length) open.pop().close(); });

async function bar(data) {
  const w = load("bar.js", "", (win) => { win.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(data) }); });
  open.push(w);
  await tick(); await tick(); await tick();
  return w;
}
const chip = (w, id) => w.document.querySelector(`#indexer-bar .ib-chips [data-ib-subtitle="${id}"]`);
// Text with a space at every element boundary, as the bar reads on screen.
function text(n) {
  const parts = [];
  (function walk(x) { for (const c of x.childNodes) { if (c.nodeType === 3) parts.push(c.textContent); else { parts.push(" "); walk(c); parts.push(" "); } } })(n);
  return parts.join("").replace(/\s+/g, " ").trim();
}

test("SubDL chip: ≈ searches and exact downloads with meters", async () => {
  const w = await bar(usage([SUBDL]));
  const c = chip(w, "subdl");
  assert.equal(text(c), "SubDL ≈300/2000 searches 12/50 downloads");
  assert.equal(c.querySelectorAll(".ib-meter.ib-ok").length, 2);
  assert.match(c.title, /≈ = estimated from Bazarr's searches/);
  assert.match(c.title, /00:00 UTC/);
});

test("downloads used up: red meter and the reset time", async () => {
  const w = await bar(usage([{ ...SUBDL, level: "full", downloads: { used: 50, limit: 50, level: "full", resetsAt: RESET } }]));
  const c = chip(w, "subdl");
  assert.ok(c.querySelector(".ib-meter.ib-full"));
  assert.match(text(c), /used up, resets /);
  assert.ok(w.document.querySelector("#indexer-bar .ib-title .ib-dot.ib-full"));
});

test("provider without a limit: plain count; paused: reason and retry in red", async () => {
  const w = await bar(usage([{ ...OPENSUBS, status: "paused", level: "full", statusText: "download limit reached, retry in an hour" }]));
  const c = chip(w, "opensubtitlescom");
  assert.equal(text(c), "OpenSubtitles 20 downloads download limit reached, retry in an hour");
  assert.equal(c.querySelector(".ib-full-text").textContent, "download limit reached, retry in an hour");
  assert.equal(c.querySelector(".ib-meter"), null);
  assert.ok(w.document.querySelector("#indexer-bar .ib-title .ib-dot.ib-full"));
});

test("Prowlarr down: subtitle chips still shown", async () => {
  const w = await bar(usage([SUBDL], { ok: false }));
  assert.match(text(w.document.querySelector("#indexer-bar .ib-chips")), /Indexer usage unavailable/);
  assert.ok(chip(w, "subdl"));
});

test("Bazarr down: one unavailable chip", async () => {
  const w = await bar(usage([], { subtitlesOk: false }));
  assert.match(text(w.document.querySelector("#indexer-bar .ib-chips")), /Subtitle usage unavailable/);
});

test("no subtitles in the data (AudioBookRequest): nothing about subtitles", async () => {
  const d = usage([]);
  delete d.subtitles; delete d.subtitlesOk;
  const w = await bar(d);
  assert.doesNotMatch(text(w.document.getElementById("indexer-bar")), /Subtitle/);
  w.document.querySelector("#indexer-bar .ib-more").click();
  assert.doesNotMatch(text(w.document.querySelector("#indexer-bar .ib-panel")), /Subtitle/);
});

test("panel: Subtitles section with the UTC day and the ≈ note", async () => {
  const w = await bar(usage([SUBDL, OPENSUBS]));
  w.document.querySelector("#indexer-bar .ib-more").click();
  const p = text(w.document.querySelector("#indexer-bar .ib-panel"));
  assert.match(p, /Subtitles/);
  assert.match(p, /SubDL per day \(00:00 UTC\) ≈300\/2000 searches 12\/50 downloads/);
  assert.match(p, /OpenSubtitles per day \(00:00 UTC\) 20 downloads/);
  assert.match(p, /≈ = estimated from Bazarr's searches/);
});
