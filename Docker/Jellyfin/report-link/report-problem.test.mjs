import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { reportUrl, label } = createRequire(import.meta.url)("./report-problem.js");
const BASE = "https://report.holy-grail.ch/";
const show = { Type: "Series", Name: "Company Retreat", ProviderIds: { Tmdb: "4040", Tvdb: "9090" } };

test("movie by TMDB id", () => {
  assert.equal(reportUrl({ Type: "Movie", ProviderIds: { Tmdb: "4242" } }, null), `${BASE}?type=movie&tmdb=4242`);
});

test("episode pre-selects series, season and episode", () => {
  assert.equal(reportUrl({ Type: "Episode", SeriesId: "s1", ParentIndexNumber: 1, IndexNumber: 8 }, show), `${BASE}?type=tv&tvdb=9090&tmdb=4040&season=1&episode=8`);
});

test("series and season pages open the series", () => {
  assert.equal(reportUrl(show, null), `${BASE}?type=tv&tvdb=9090&tmdb=4040`);
  assert.equal(reportUrl({ Type: "Season", SeriesId: "s1", IndexNumber: 2 }, show), `${BASE}?type=tv&tvdb=9090&tmdb=4040`);
});

test("no ids or other types: no link", () => {
  assert.equal(reportUrl({ Type: "Movie", ProviderIds: {} }, null), null);
  assert.equal(reportUrl({ Type: "Movie", ProviderIds: { Tmdb: "1;drop" } }, null), null);
  assert.equal(reportUrl({ Type: "Episode", SeriesId: "s1" }, null), null);
  assert.equal(reportUrl({ Type: "Audio" }, null), null);
  assert.equal(reportUrl(null, null), null);
});

test("episode without numbers opens the series only", () => {
  assert.equal(reportUrl({ Type: "Episode", SeriesId: "s1" }, show), `${BASE}?type=tv&tvdb=9090&tmdb=4040`);
});

test("label in German or English", () => {
  assert.equal(label("de-CH"), "⚠ Problem melden");
  assert.equal(label("en-US"), "⚠ Report a problem");
  assert.equal(label(""), "⚠ Report a problem");
});
