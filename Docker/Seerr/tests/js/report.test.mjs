import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { pickLang, statusInfo, linkQuery, itemLabel, browseQuery, rowLabel, TEXT, REASONS } = createRequire(import.meta.url)("../../download-now/report-www/report.js");

test("language from the browser, German or English", () => {
  assert.equal(pickLang(["de-CH", "en"]), "de");
  assert.equal(pickLang(["fr-CH", "en-GB"]), "en");
  assert.equal(pickLang(["fr"]), "en");
  assert.equal(pickLang([]), "en");
});

test("every reason and status has both languages", () => {
  for (const l of ["en", "de"]) {
    for (const r of REASONS) assert.ok(TEXT[l].reasons[r], `${l} ${r}`);
    assert.deepEqual(Object.keys(TEXT[l].status).sort(), Object.keys(TEXT.en.status).sort());
  }
});

test("status text, colour and progress", () => {
  assert.deepEqual(statusInfo(TEXT.en, { status: "downloading", progress: 42 }), { text: "Downloading 42 %", cls: "warn", done: false });
  assert.equal(statusInfo(TEXT.de, { status: "imported" }).cls, "ok");
  assert.equal(statusInfo(TEXT.en, { status: "failed" }).cls, "bad");
  assert.equal(statusInfo(TEXT.en, { status: "review" }).done, true);
});

test("Jellyfin link query: only known numeric fields", () => {
  assert.equal(linkQuery("?type=movie&tmdb=4242"), "type=movie&tmdb=4242");
  assert.equal(linkQuery("?type=tv&tvdb=9090&tmdb=4040&season=1&episode=8"), "type=tv&tmdb=4040&tvdb=9090&season=1&episode=8");
  assert.equal(linkQuery("?type=tv&tmdb=1;drop&tvdb=2"), "type=tv&tvdb=2");
  assert.equal(linkQuery("?type=book&tmdb=1"), null);
  assert.equal(linkQuery("?type=movie"), null);
  assert.equal(linkQuery(""), null);
});

test("item label with episode", () => {
  assert.equal(itemLabel(TEXT.en, { title: "Company Retreat", year: 2026, season: 1, episode: 8, episodeTitle: "Eight" }), "Company Retreat (2026) · S01E08 Eight");
  assert.equal(itemLabel(TEXT.en, { title: "Some Film", year: 2004 }), "Some Film (2004)");
});

test("browse list: query, row text, every sort and label in both languages", () => {
  assert.equal(browseQuery("movie", "az", 1), "kind=movie&sort=az&page=1");
  assert.equal(browseQuery("tv", "watched", 3), "kind=tv&sort=watched&page=3");
  assert.equal(rowLabel({ title: "Some Film", year: 2004 }), "Some Film (2004)");
  assert.equal(rowLabel({ title: "Old" }), "Old");
  for (const l of ["en", "de"]) {
    for (const k of ["browse", "films", "series", "more", "noPoster"]) assert.ok(TEXT[l][k], `${l} ${k}`);
    for (const k of ["az", "year", "added", "watched"]) assert.ok(TEXT[l].sorts[k], `${l} sort ${k}`);
  }
  assert.deepEqual(Object.keys(TEXT.de).sort(), Object.keys(TEXT.en).sort());
});
