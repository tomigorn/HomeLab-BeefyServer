import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const conf = readFileSync(new URL("../../indexer-bar/nginx.conf", import.meta.url), "utf8");
const injected = conf.match(/sub_filter "<\/head>" '(.*)';/)[1];
const scripts = [...injected.matchAll(/<script ([^>]*)><\/script>/g)].map((m) => m[1]);
const name = (s) => s.match(/\/_indexer-bar\/([a-z-]+\.js)/)[1];

test("bar-early.js comes first, render-blocking (no defer), versioned, with the bar's no-bar pattern", () => {
  assert.ok(injected.startsWith('<script src="/_indexer-bar/bar-early.js?v=$hg_v"'), injected.slice(0, 80));
  assert.doesNotMatch(scripts[0], /\b(defer|async)\b/);
  const noBar = (s) => s.match(/data-ib-no-bar="([^"]*)"/)[1];
  assert.equal(noBar(scripts[0]), noBar(scripts.find((s) => name(s) === "bar.js")));
});

test("deferred scripts: tip.js first, then menu-links.js (the hidden menu waits for it), then the rest", () => {
  const deferred = scripts.filter((s) => /\bdefer\b/.test(s)).map(name);
  assert.deepEqual(deferred.slice(0, 3), ["tip.js", "menu-links.js", "bar.js"]);
  assert.equal(deferred.length, 13);
  assert.equal(deferred[11], "collections.js");
  assert.equal(deferred[12], "retention.js");
});
