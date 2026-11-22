import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const rr = createRequire(import.meta.url)("./region-rows.js");
const hl = createRequire(import.meta.url)("./home-layout.js");
const es = createRequire(import.meta.url)("./empty-sections.js");

const PLUGIN = ["ContinueWatching", "NextUp", "MyList", "RecentlyAddedMovies", "RecentlyAddedShows", "HiddenGems", "FinishTheseCollections", "CollectionsSection"];
const ROWS = [
  { id: "HssRegionNordic", name: { en: "Nordic Films & Series", de: "Nordische Filme & Serien" }, items: ["a", "b", "c"] },
  { id: "HssRegionAnime", name: { en: "Anime", de: "Anime" }, items: ["d"] }
];

function fakeApi(answer, seen) {
  return {
    getCurrentUserId: () => "u1",
    getUrl: (path, q) => ({ path, q }),
    getJSON: (url) => { seen.push(url); return Promise.resolve(answer(url)); }
  };
}

test("layout: region rows right after Hidden Gems, before the movie genres, whatever order the scripts run in", () => {
  const ids = ROWS.map((r) => r.id);
  const base = es.withDiscover(es.withRecent(PLUGIN));
  const want = ["HssComingSoon", "MyList"];
  const a = hl.layout(rr.layout(base, ids));
  const b = rr.layout(hl.layout(base), ids);
  assert.deepEqual(a, b);
  const at = a.indexOf("HiddenGems");
  assert.deepEqual(a.slice(at, at + 4), ["HiddenGems", "HssRegionNordic", "HssRegionAnime", "DiscoverMovieGenres"]);
  assert.ok(want.every((id) => a.includes(id)));
  assert.deepEqual(rr.layout(a, ids), a);
});

test("layout: without Hidden Gems before the movie genres, without both last; no rows changes nothing", () => {
  assert.deepEqual(rr.layout(["X", "DiscoverMovieGenres", "Y"], ["HssRegionNordic"]), ["X", "HssRegionNordic", "DiscoverMovieGenres", "Y"]);
  assert.deepEqual(rr.layout(["X", "Y"], ["HssRegionNordic"]), ["X", "Y", "HssRegionNordic"]);
  assert.deepEqual(rr.layout(["X", "Y"], []), ["X", "Y"]);
});

test("data: only well-formed rows count", () => {
  const rows = rr.rowsOf({ HssRegionRows: [ROWS[0], { id: "bad id", name: { en: "x" }, items: [] }, { id: "HssRegionX", items: "a" }, null] });
  assert.deepEqual(rows.map((r) => r.id), ["HssRegionNordic"]);
  assert.deepEqual(rr.rowsOf({}), []);
});

test("row: the user's own items in the list's order, titles the user may not see left out", async () => {
  const seen = [];
  const api = fakeApi(() => ({ Items: [{ Id: "c" }, { Id: "a" }] }), seen);
  assert.deepEqual((await rr.loadRow(api, "u1", ROWS[0])).map((i) => i.Id), ["a", "c"]);
  assert.equal(seen[0].path, "Users/u1/Items");
  assert.equal(seen[0].q.Ids, "a,b,c");
});

test("extension: names per language, loading until the rows are there, then drawn as Jellyfin cards", async () => {
  const win = { HssRegionRows: ROWS };
  const ext = rr.create(win);
  assert.deepEqual(ext.ids, ["HssRegionNordic", "HssRegionAnime"]);
  assert.equal(ext.name("HssRegionNordic", "de-CH"), "Nordische Filme & Serien");
  assert.equal(ext.name("HssRegionNordic", "en-US"), "Nordic Films & Series");
  assert.equal(ext.loading("HssRegionNordic"), true);
  const drawn = [];
  let repaints = 0;
  const tools = { syncRecent: (c, id, items, name) => drawn.push([id, items.map((i) => i.Id), name]), repaint: () => { repaints++; } };
  const api = fakeApi((url) => ({ Items: url.q.Ids.split(",").map((Id) => ({ Id })) }), []);
  ext.sync({}, api, "en-US", tools);
  assert.deepEqual(drawn, []);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(repaints, 1);
  assert.equal(ext.loading("HssRegionNordic"), false);
  ext.sync({}, api, "en-US", tools);
  assert.deepEqual(drawn, [["HssRegionNordic", ["a", "b", "c"], "Nordic Films & Series"], ["HssRegionAnime", ["d"], "Anime"]]);
});

test("extension: a failed load ends loading with empty rows (hint instead of a skeleton)", async () => {
  const ext = rr.create({ HssRegionRows: ROWS });
  const api = { getCurrentUserId: () => "u1", getUrl: () => "", getJSON: () => Promise.reject(new Error("down")) };
  ext.sync({}, api, "en", { syncRecent: () => {}, repaint: () => {} });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(ext.loading("HssRegionAnime"), false);
  assert.equal(ext.hint("HssRegionAnime", "de"), "Noch nichts hier.");
});
