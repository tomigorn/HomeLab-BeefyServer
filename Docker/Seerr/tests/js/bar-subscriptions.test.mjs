import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, tick, SIDEBAR } from "./fixture.mjs";

const sub = (name, kind, daysLeft, level = "ok") => daysLeft == null
  ? { name, kind, lifetime: true, expires: null, daysLeft: null, expired: false, level: "ok" }
  : { name, kind, lifetime: false, expires: "2027-01-01", daysLeft, expired: daysLeft < 0, level };

// The real usage.json shape (2026-11-16)
const REAL = [
  sub("Treasure Maps", "indexer", 1827), sub("Eweka", "Usenet provider", 460), sub("ProtonVPN Plus", "VPN", 211),
  sub("NZBgeek", "indexer", null), sub("NzbPlanet", "indexer", null),
];

const open = [];
afterEach(() => { while (open.length) open.pop().close(); });

async function panel(subscriptions, body = "") {
  const data = { ok: true, generated: new Date().toISOString(), indexers: [], storage: [], subscriptions };
  const w = load("bar.js", body, (win) => { win.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(data) }); });
  open.push(w);
  await tick(); await tick(); await tick();
  w.document.querySelector("#indexer-bar .ib-more").click();
  return w.document.querySelector("#indexer-bar .ib-panel");
}
// [heading, [names…]] per group, as the popup shows them under "Subscriptions"
function groups(p) {
  return [...p.querySelectorAll(".ib-subs-group")].map((g) => [g.querySelector(".ib-subs-kind").textContent,
    [...g.querySelectorAll("tr .ib-name")].map((n) => n.firstChild.textContent)]);
}

test("subscriptions: one group per kind, soonest end first, lifetime-only groups last", async () => {
  const p = await panel(REAL);
  assert.deepEqual(groups(p), [
    ["VPN", ["ProtonVPN Plus"]],
    ["Usenet", ["Eweka"]],
    ["Indexers", ["Treasure Maps", "NZBgeek", "NzbPlanet"]],
  ]);
});

test("subscriptions: the kind is the heading, not repeated under every name", async () => {
  const p = await panel(REAL);
  const rows = [...p.querySelectorAll(".ib-subs-group tr")];
  assert.equal(rows.length, 5);
  rows.forEach((r) => assert.equal(r.cells[0].textContent.replace("★", ""), r.cells[0].firstChild.textContent));
});

test("subscriptions: inside a group by days left, expired first, lifetime last alphabetically", async () => {
  const p = await panel([
    sub("Zeta", "indexer", null), sub("Late", "indexer", 400), sub("Alpha", "indexer", null),
    sub("Gone", "indexer", -2, "full"), sub("Soon", "indexer", 5, "orange"),
  ]);
  assert.deepEqual(groups(p), [["Indexers", ["Gone", "Soon", "Late", "Alpha", "Zeta"]]]);
});

test("subscriptions: groups ordered by their soonest-ending entry, lifetime-only groups after", async () => {
  const p = await panel([
    sub("Geek", "indexer", null), sub("Tracker", "indexer", 300), sub("Proton", "VPN", 20, "yellow"),
    sub("Eweka", "Usenet provider", null), sub("Box", "Seedbox", 90),
  ]);
  assert.deepEqual(groups(p).map((g) => g[0]), ["VPN", "Seedbox", "Indexers", "Usenet"]);
});

test("subscriptions: unknown kind gets its own group headed by the kind, none = Other", async () => {
  const p = await panel([sub("Box", "Seedbox", 90), sub("Thing", null, 10), sub("Geek", "indexer", 50)]);
  assert.deepEqual(groups(p), [["Other", ["Thing"]], ["Indexers", ["Geek"]], ["Seedbox", ["Box"]]]);
});

test("subscriptions: German headings", async () => {
  const p = await panel([...REAL, sub("Box", "Seedbox", 90), sub("Thing", "", 10)], SIDEBAR.replace(">Series<", ">Serien<"));
  assert.deepEqual(groups(p).map((g) => g[0]), ["Weitere", "Seedbox", "VPN", "Usenet", "Indexer"]);
});

test("subscriptions: dots, colours and texts kept per row", async () => {
  const p = await panel([sub("Soon", "indexer", 5, "orange"), sub("Geek", "indexer", null)]);
  const [soon, geek] = p.querySelectorAll(".ib-subs-group tr");
  assert.ok(soon.querySelector(".ib-dot.ib-orange"));
  assert.match(soon.querySelector(".ib-orange-text").textContent, /^5 days left · /);
  assert.equal(geek.querySelector(".ib-dot"), null);
  assert.equal(geek.cells[1].querySelector(".ib-muted").textContent, "lifetime");
});
