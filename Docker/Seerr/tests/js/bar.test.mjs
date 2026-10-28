import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, SIDEBAR, tick } from "./fixture.mjs";

const TB = 1024 ** 4, GB = 1024 ** 3;

function usage(storage) {
  return { ok: true, generated: new Date().toISOString(), indexers: [], subscriptions: [], storage };
}
// Texts as scripts/indexer-usage.py writes them.
const SSD = { name: "SSD", total: 7.3 * TB, used: 2.7 * TB, free: 4.6 * TB, pct: 37.6, level: "ok", yellow: 70, red: 80, partsLabel: "SSDs",
  sub: { en: "fast tier, new downloads", de: "schnell, neue Downloads" },
  hint: { en: "Above 70 % the night mover moves titles nobody played for 30 days (oldest first) to the HDD, down to 60 %; from 80 % new downloads go straight to the HDD.",
    de: "Ab 70 % verschiebt der Nacht-Mover Titel, die 30 Tage niemand geschaut hat (älteste zuerst), auf die HDD, bis 60 %; ab 80 % landen neue Downloads direkt auf der HDD." } };
const HDD = { name: "HDD", total: 28 * TB, used: 535 * GB, free: 27.5 * TB, pct: 1.9, level: "ok", yellow: 80, red: 90,
  sub: { en: "archive", de: "Archiv" },
  hint: { en: "Archive for older titles. From 90 % new downloads are no longer imported.", de: "Archiv für ältere Titel. Ab 90 % werden neue Downloads nicht mehr importiert." } };

const open = [];
afterEach(() => { while (open.length) open.pop().close(); });

async function bar(data, body = "") {
  const w = load("bar.js", body, (win) => {
    win.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
  });
  open.push(w);
  await tick(); await tick(); await tick();
  return w;
}
const chip = (w, name) => [...w.document.querySelectorAll("#indexer-bar .ib-chips [data-ib-disk]")].find((c) => c.dataset.ibDisk === name);

test("SSD and HDD chips: used/total, percent, green meter", async () => {
  const w = await bar(usage([SSD, HDD]));
  const s = chip(w, "SSD"), h = chip(w, "HDD");
  assert.match(s.textContent, /^SSD\s*2\.7\/7\.3 TB\s*38 %$/);
  assert.match(h.textContent, /^HDD\s*535 GB\/28 TB\s*2 %$/);
  assert.ok(s.querySelector(".ib-meter.ib-ok"));
  assert.equal(s.querySelector(".ib-fill").style.width, "37.6%");
});

test("colour follows the level from the collector", async () => {
  const w = await bar(usage([{ ...SSD, pct: 75, level: "yellow" }, { ...HDD, pct: 93, level: "full" }]));
  assert.ok(chip(w, "SSD").querySelector(".ib-meter.ib-yellow"));
  assert.ok(chip(w, "SSD").querySelector(".ib-yellow-text"));
  assert.ok(chip(w, "HDD").querySelector(".ib-meter.ib-full"));
  assert.ok(w.document.querySelector("#indexer-bar .ib-title .ib-dot.ib-full"));
});

test("hint: free space and the rule (English)", async () => {
  const w = await bar(usage([SSD, HDD]));
  assert.equal(chip(w, "SSD").title, "SSD: 4.6 TB free. Above 70 % the night mover moves titles nobody played for 30 days (oldest first) to the HDD, down to 60 %; from 80 % new downloads go straight to the HDD.");
  assert.equal(chip(w, "HDD").title, "HDD: 27.5 TB free. Archive for older titles. From 90 % new downloads are no longer imported.");
});

test("two SSDs: the hint names each one", async () => {
  const w = await bar(usage([{ ...SSD, parts: [75, 0.4] }, HDD]));
  assert.match(chip(w, "SSD").title, /^SSD: 4\.6 TB free \(2 SSDs: 75 % · 0 %\)\. Above 70 %/);
});

test("German UI: German hint and decimal comma", async () => {
  const w = await bar(usage([SSD, HDD]), SIDEBAR.replace(">Series<", ">Serien<"));
  assert.match(chip(w, "SSD").textContent, /2,7\/7,3 TB/);
  assert.equal(chip(w, "SSD").title, "SSD: 4,6 TB frei. Ab 70 % verschiebt der Nacht-Mover Titel, die 30 Tage niemand geschaut hat (älteste zuerst), auf die HDD, bis 60 %; ab 80 % landen neue Downloads direkt auf der HDD.");
  assert.equal(chip(w, "HDD").title, "HDD: 27,5 TB frei. Archiv für ältere Titel. Ab 90 % werden neue Downloads nicht mehr importiert.");
});

test("panel lists storage with free space and the rule", async () => {
  const w = await bar(usage([SSD, HDD]));
  w.document.querySelector("#indexer-bar .ib-more").click();
  const p = w.document.querySelector("#indexer-bar .ib-panel");
  const h4 = [...p.querySelectorAll("h4")].map((h) => h.textContent);
  assert.ok(h4.includes("Storage"));
  assert.match(p.textContent, /4\.6 TB free/);
  assert.match(p.textContent, /night mover/);
});

test("no storage data: no disk chips, indexers unchanged", async () => {
  const w = await bar(usage(undefined));
  assert.equal(chip(w, "SSD"), undefined);
  assert.ok(w.document.querySelector("#indexer-bar .ib-title"));
});

function gated(status, path) {
  const calls = { n: 0, status };
  const w = load("bar.js", "", (win) => {
    win.fetch = () => { calls.n++; return Promise.resolve({ ok: calls.status === 200, status: calls.status, json: () => Promise.resolve(usage([SSD, HDD])) }); };
  }, "https://requests.example.com" + path);
  open.push(w);
  return { w, calls };
}

test("401 from the data: no bar, no ib-on", async () => {
  const { w } = gated(401, "/");
  await tick(); await tick(); await tick();
  assert.equal(w.document.getElementById("indexer-bar"), null);
  assert.equal(w.document.documentElement.classList.contains("ib-on"), false);
});

test("403 from the data: no bar", async () => {
  const { w } = gated(403, "/");
  await tick(); await tick(); await tick();
  assert.equal(w.document.getElementById("indexer-bar"), null);
});

test("served data on /login and /setup: still no bar", async () => {
  for (const path of ["/login", "/setup", "/login/x"]) {
    const { w } = gated(200, path);
    await tick(); await tick(); await tick();
    assert.equal(w.document.getElementById("indexer-bar"), null, path);
  }
});

test("login by client-side navigation: the bar appears without a reload", async () => {
  const { w, calls } = gated(401, "/login");
  await tick(); await tick(); await tick();
  assert.equal(w.document.getElementById("indexer-bar"), null);
  calls.status = 200;
  w.history.pushState({}, "", "/");
  await new Promise((r) => setTimeout(r, 20));
  await tick(); await tick(); await tick();
  assert.ok(w.document.getElementById("indexer-bar"));
  assert.ok(w.document.documentElement.classList.contains("ib-on"));
  w.history.pushState({}, "", "/login");
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(w.document.getElementById("indexer-bar"), null);
  assert.equal(w.document.documentElement.classList.contains("ib-on"), false);
});

test("without its config tag the bar does nothing", async () => {
  let n = 0;
  const w = load("bar.js", "", (win) => { win.fetch = () => { n++; return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(usage([SSD])) }); }; }, undefined, "");
  open.push(w);
  await tick(); await tick(); await tick();
  assert.equal(n, 0);
  assert.equal(w.document.getElementById("indexer-bar"), null);
});

test("Seerr: unlimited indexers only under more, no quota chip", async () => {
  const d = usage([SSD]);
  d.indexers = [{ name: "NZBgeek", limited: false, window: "rolling", level: "ok", searches: { used: 3, limit: null, level: "ok" }, downloads: { used: 1, limit: null, level: "ok" } }];
  const w = await bar(d);
  assert.equal(w.document.querySelector("#indexer-bar [data-ib-indexer]"), null);
  assert.equal(w.document.querySelector("#indexer-bar [data-ib-quota]"), null);
  assert.doesNotMatch(w.document.querySelector("#indexer-bar .ib-chips").textContent, /NZBgeek/);
});

test("panel note comes from the data", async () => {
  const d = usage([SSD]);
  d.note = "When a limited indexer is used up, requests may wait until it resets.";
  const w = await bar(d);
  w.document.querySelector("#indexer-bar .ib-more").click();
  assert.match(w.document.querySelector("#indexer-bar .ib-panel").textContent, /requests may wait until it resets/);
});
