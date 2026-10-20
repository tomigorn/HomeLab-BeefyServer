import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { trackerUrl } = createRequire(import.meta.url)("./rate-in-tracker.js");
const BASE = "https://tracker.holy-grail.ch/go/tmdb";
const dark = { Type: "Series", Name: "Dark", ProviderIds: { Tmdb: "70523" } };

test("movie", () => {
  assert.equal(trackerUrl({ Type: "Movie", Name: "The Matrix", ProviderIds: { Tmdb: "603" } }, null), `${BASE}/movie/603?title=The%20Matrix`);
});

test("episode uses series", () => {
  assert.equal(trackerUrl({ Type: "Episode", SeriesId: "s1", ProviderIds: { Tvdb: "1" } }, dark), `${BASE}/show/70523?title=Dark`);
});

test("series page", () => {
  assert.equal(trackerUrl(dark, null), `${BASE}/show/70523?title=Dark`);
});

test("season uses series", () => {
  assert.equal(trackerUrl({ Type: "Season", SeriesId: "s1", ProviderIds: {} }, dark), `${BASE}/show/70523?title=Dark`);
});

test("no tmdb no link", () => {
  assert.equal(trackerUrl({ Type: "Movie", Name: "Home video", ProviderIds: {} }, null), null);
  assert.equal(trackerUrl({ Type: "Movie", Name: "Home video" }, null), null);
  assert.equal(trackerUrl({ Type: "Episode", SeriesId: "s1", ProviderIds: { Tmdb: "9" } }, { Type: "Series", Name: "X", ProviderIds: {} }), null);
  assert.equal(trackerUrl({ Type: "Episode", SeriesId: "s1", ProviderIds: { Tmdb: "9" } }, null), null);
  assert.equal(trackerUrl({ Type: "Audio" }, null), null);
  assert.equal(trackerUrl(null, null), null);
});

test("non-numeric tmdb no link", () => {
  assert.equal(trackerUrl({ Type: "Movie", Name: "X", ProviderIds: { Tmdb: "1;drop" } }, null), null);
});

test("title encoded", () => {
  assert.equal(trackerUrl({ Type: "Movie", Name: "Tom & Jerry #1", ProviderIds: { Tmdb: "1" } }, null), `${BASE}/movie/1?title=Tom%20%26%20Jerry%20%231`);
});
