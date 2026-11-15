import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { load, tick, BAR_TAG } from "./fixture.mjs";

const WWW = new URL("../../indexer-bar/www/", import.meta.url);
const EARLY_TAG = `<script data-ib-no-bar="^/(login|setup|resetpassword)\\b"></script>`;
const open = [];
afterEach(() => { while (open.length) open.pop().close(); });

function early(url = "https://requests.example.com/discover/tv", before) {
  const w = load("bar-early.js", "", before, url, EARLY_TAG + BAR_TAG, { tip: false });
  open.push(w);
  return w;
}
const on = (w, c = "ib-on") => w.document.documentElement.classList.contains(c);

test("reserves the bar before bar.js runs: html gets ib-on at once", () => {
  const w = early();
  assert.equal(on(w), true);
  assert.equal(on(w, "ib-mini"), false);
  assert.equal(w.__indexerBar, undefined);
});

test("a collapsed bar (remembered per browser) is reserved collapsed", () => {
  const w = early(undefined, (win) => win.localStorage.setItem("indexer-bar-mini", "1"));
  assert.equal(on(w), true);
  assert.equal(on(w, "ib-mini"), true);
});

test("no-bar pages (login, setup, resetpassword) stay untouched", () => {
  for (const path of ["/login", "/setup", "/resetpassword/abc"]) assert.equal(on(early("https://requests.example.com" + path)), false, path);
});

test("storage blocked: still reserved, not collapsed", () => {
  const w = early(undefined, (win) => Object.defineProperty(win, "localStorage", { get() { throw new Error("blocked"); } }));
  assert.equal(on(w), true);
  assert.equal(on(w, "ib-mini"), false);
});

test("no session: bar.js takes the reserved strip away again (401)", async () => {
  const w = early(undefined, (win) => { win.fetch = () => Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve(null) }); });
  assert.equal(on(w), true);
  w.eval(readFileSync(new URL("bar.js", WWW), "utf8"));
  await tick(); await tick(); await tick();
  assert.equal(on(w), false);
  assert.equal(w.document.getElementById("indexer-bar"), null);
});

test("bar.css draws an empty dark strip of the bar's height while ib-on waits for the data", () => {
  const css = readFileSync(new URL("bar.css", WWW), "utf8");
  const rule = css.match(/html\.ib-on body::before\s*\{([^}]*)\}/);
  assert.ok(rule, "html.ib-on body::before rule");
  assert.match(rule[1], /position:\s*fixed/);
  assert.match(rule[1], /top:\s*0/);
  assert.match(rule[1], /height:\s*var\(--ib-h\)/);
  assert.match(rule[1], /background:\s*#111827/);
});

test("Next's replaceState while the first answer is pending keeps the reserved strip", async () => {
  let release;
  const body = new Promise((r) => { release = r; });
  const w = early(undefined, (win) => { win.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => body }); });
  w.eval(readFileSync(new URL("bar.js", WWW), "utf8"));
  await tick();
  w.history.replaceState({}, "", "/discover/tv");
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(on(w), true);
  release({ ok: true, generated: new Date().toISOString(), indexers: [], subscriptions: [] });
  await tick(); await tick(); await tick();
  assert.equal(on(w), true);
  assert.ok(w.document.getElementById("indexer-bar"));
});

test("first answer fails (network): the reserved strip goes away", async () => {
  const w = early(undefined, (win) => { win.fetch = () => Promise.reject(new Error("offline")); });
  w.eval(readFileSync(new URL("bar.js", WWW), "utf8"));
  await tick(); await tick(); await tick();
  assert.equal(on(w), false);
});

test("a fresh load of /manual is marked so Seerr's 404 stays hidden until our page is in; other pages are not", () => {
  assert.equal(early("https://requests.example.com/manual").document.documentElement.getAttribute("data-hg-early"), "manual");
  assert.equal(early("https://requests.example.com/manual/").document.documentElement.getAttribute("data-hg-early"), "manual");
  assert.equal(early("https://requests.example.com/requests").document.documentElement.hasAttribute("data-hg-early"), false);
});

test("a fresh load of /collections is marked the same way", () => {
  assert.equal(early("https://requests.example.com/collections").document.documentElement.getAttribute("data-hg-early"), "collections");
  assert.equal(early("https://requests.example.com/collections/").document.documentElement.getAttribute("data-hg-early"), "collections");
});
