import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, tick, SIDEBAR, hintOf } from "./fixture.mjs";

const RESET = new Date(Date.now() + 3600e3).toISOString();
const LIFETIME = { lifetime: true, expires: null, expired: false };
const UNTIL = { lifetime: false, expires: "2026-11-18", expired: false };
const ENDED = { lifetime: false, expires: "2026-03-03", expired: true };
const PLAIN = { lifetime: false, expires: null, expired: false };
const m = (used, limit) => ({ used, limit, level: "ok", resetsAt: limit == null ? null : RESET });

function usage() {
  return {
    ok: true, generated: new Date().toISOString(), storage: [], subtitlesOk: true,
    indexers: [
      { name: "Treasure Maps", limited: true, window: "rolling", level: "ok", searches: m(10, null), downloads: m(5, 400), premium: UNTIL },
      { name: "1337x", limited: false, window: "rolling", level: "ok", searches: m(3, null), downloads: m(0, null) },
      { name: "NZBgeek", limited: false, window: "rolling", level: "ok", searches: m(3, null), downloads: m(1, null), premium: LIFETIME },
    ],
    subtitles: [
      { name: "SubDL", id: "subdl", window: "utc-day", status: "ok", level: "ok", searches: m(989, 2000), downloads: m(14, 50) },
      { name: "Paid Subs", id: "paid", window: "utc-day", status: "ok", level: "ok", searches: m(0, null), downloads: m(2, null), premium: PLAIN },
    ],
    subscriptions: [
      { name: "Treasure Maps", kind: "indexer", lifetime: false, expires: "2026-11-18", daysLeft: 3, expired: false, level: "orange", premium: UNTIL },
      { name: "Eweka", kind: "Usenet provider", lifetime: false, expires: "2026-03-03", daysLeft: -257, expired: true, level: "full", premium: ENDED },
      { name: "NZBgeek", kind: "indexer", lifetime: true, expires: null, daysLeft: null, expired: false, level: "ok", premium: LIFETIME },
    ],
  };
}

const open = [];
afterEach(() => { while (open.length) open.pop().close(); });

async function bar(data, body = "") {
  const w = load("bar.js", body, (win) => { win.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(data) }); });
  open.push(w);
  await tick(); await tick(); await tick();
  return w;
}
function stars(root) {
  const w = root.ownerDocument.defaultView;
  return [...root.querySelectorAll(".ib-star")].map((s) => [s.closest(".ib-name").firstChild.textContent, hintOf(w, s).text]);
}

test("chips: star with its text on paid indexers, subtitles and due subscriptions, none on free ones", async () => {
  const w = await bar(usage());
  const got = stars(w.document.querySelector("#indexer-bar .ib-chips"));
  assert.deepEqual(got, [
    ["Treasure Maps", "Paid subscription until 18 Nov 2026"],
    ["Paid Subs", "Paid account"],
    ["Treasure Maps", "Paid subscription until 18 Nov 2026"],
    ["Eweka", "Paid subscription, ended 3 Mar 2026"],
  ]);
});

test("star: ★ with an accessible label equal to its hint, focusable for touch", async () => {
  const w = await bar(usage());
  const s = w.document.querySelector("#indexer-bar .ib-chips .ib-star");
  assert.equal(s.textContent, "★");
  assert.equal(s.getAttribute("aria-label"), hintOf(w, s).text);
  assert.equal(s.getAttribute("role"), "img");
  assert.equal(s.tabIndex, 0);
});

test("panel: stars on limited, unlimited, subtitle and subscription rows", async () => {
  const w = await bar(usage());
  w.document.querySelector("#indexer-bar .ib-more").click();
  const got = stars(w.document.querySelector("#indexer-bar .ib-panel"));
  assert.deepEqual(got, [
    ["Treasure Maps", "Paid subscription until 18 Nov 2026"],
    ["NZBgeek", "Paid account (lifetime)"],
    ["Paid Subs", "Paid account"],
    ["Eweka", "Paid subscription, ended 3 Mar 2026"],
    ["Treasure Maps", "Paid subscription until 18 Nov 2026"],
    ["NZBgeek", "Paid account (lifetime)"],
  ]);
});

test("German UI: German star texts", async () => {
  const w = await bar(usage(), SIDEBAR.replace(">Series<", ">Serien<"));
  w.document.querySelector("#indexer-bar .ib-more").click();
  const got = Object.fromEntries(stars(w.document.querySelector("#indexer-bar .ib-panel")));
  assert.equal(got["Treasure Maps"], "Bezahltes Abo bis 18. Nov. 2026");
  assert.equal(got["Eweka"], "Bezahltes Abo, beendet am 3. März 2026");
  assert.equal(got["NZBgeek"], "Bezahltes Konto (lebenslang)");
  assert.equal(got["Paid Subs"], "Bezahltes Konto");
});

test("without tip.js: the star keeps its accessible label, no title", async () => {
  const w = load("bar.js", "", (win) => { win.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(usage()) }); }, undefined, undefined, { tip: false });
  open.push(w);
  await tick(); await tick(); await tick();
  const s = w.document.querySelector("#indexer-bar .ib-chips .ib-star");
  assert.equal(s.getAttribute("aria-label"), "Paid subscription until 18 Nov 2026");
  assert.equal(s.hasAttribute("title"), false);
});
