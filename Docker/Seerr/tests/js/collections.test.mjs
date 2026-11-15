import { test } from "node:test";
import assert from "node:assert/strict";
import { load, tick } from "./fixture.mjs";

// Seerr's layout around its 404 page (an unknown route like /collections), reduced to what the script looks at
const PAGE = `<div class="flex h-full min-w-0 min-h-full bg-gray-900"><div class="sidebar"><a href="/discover/tv" class="group flex">Series</a></div>
<div class="relative mb-16 flex w-0 min-w-0 flex-1 flex-col lg:ml-64"><main class="relative top-16 z-0 focus:outline-none" tabindex="0"><div class="mb-6"><div class="error-message" data-seerr-404><p>404 - Page Not Found</p></div></div></main></div></div>`;
const API = "/_download-now/collections/";

const item = (id, o, over) => Object.assign({ k: "movie", id, t: "Film " + id, y: 2000 + id, p: "/p" + id + ".jpg", o }, over);
const list = (over) => Object.assign({ id: "user-pat-produced", name: "Pat produced", ownerName: "jane", mine: true, canEdit: true, query: "producer:Pat Example", sort: "release", max: 50,
  count: 2, owned: 1, items: [item(1, true), item(2, false)], updated: "2026-11-15T18:00:00Z" }, over);
const LISTS = { status: 200, body: { me: { id: 7, admin: false, create: true }, limits: { total: 20, perUser: 5, mine: 1, all: 2 },
  lists: [list({ id: "user-joe", name: "Joe list", ownerName: "joe", mine: false, canEdit: false, query: "studio:Fake Studio" }), list()] } };
const PICK = { status: 200, body: { status: "pick", picks: [{ term: 0, field: "producer", value: "Pat Example", options: [
  { id: 501, name: "Pat Example", photo: "/pat.jpg", known: ["Fake Film A", "Fake Show"] }, { id: 502, name: "Pat Example", photo: null, known: ["Another Thing"] }] }] } };
const OK = { status: 200, body: { status: "ok", query: "producer:Pat Example", items: [item(3, true), item(4, false, { k: "tv", t: "Show 4" })], count: 2, total: 2, owned: 1 } };

function world(routes) {
  const calls = [];
  const before = (w) => {
    w.fetch = (url, opts) => {
      calls.push({ url, opts: opts || {} });
      const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => url.startsWith(k));
      const r = key ? routes[key] : { status: 404, body: {} };
      const res = typeof r === "function" ? r(url, opts) : r;
      return Promise.resolve({ ok: res.status === 200, status: res.status, json: () => Promise.resolve(res.body) });
    };
  };
  return { calls, before };
}

const settle = async () => { for (let i = 0; i < 12; i++) await tick(); };
const ME = { status: 200, body: { id: 7, settings: { locale: "" } } };

async function page(routes, url = "https://requests.example.com/collections", body = PAGE) {
  const s = world(Object.assign({ "/api/v1/auth/me": ME, [API + "lists"]: LISTS }, routes));
  const w = load("collections.js", body, s.before, url);
  await settle();
  return { w, calls: s.calls, $: (q) => w.document.querySelector(q), $$: (q) => [...w.document.querySelectorAll(q)] };
}

const posts = (calls) => calls.filter((c) => c.opts.method === "POST").map((c) => ({ url: c.url, body: JSON.parse(c.opts.body), type: c.opts.headers["Content-Type"] }));
const type = (w, el, value) => { el.value = value; el.dispatchEvent(new w.Event("input", { bubbles: true })); };

test("the page replaces Seerr's 404 on /collections, with the title and the lists of everyone", async () => {
  const { w, $, $$ } = await page({});
  assert.ok($("main > #hg-collections"));
  assert.equal($("main").hasAttribute("data-hg-collections"), true);
  assert.equal(w.document.title, "Collections - Seerr");
  assert.equal($("#hg-collections h1").textContent, "Collections");
  assert.deepEqual($$("[data-list]").map((c) => c.getAttribute("data-list")), ["user-joe", "user-pat-produced"]);
  assert.match($('[data-list="user-joe"] [data-owner]').textContent, /by joe/);
  assert.match($('[data-list="user-pat-produced"] [data-query]').textContent, /producer:Pat Example/);
  assert.match($('[data-list="user-pat-produced"] [data-counts]').textContent, /2 titles · 1 on the server/);
});

test("only own lists (or an admin's) get Edit and Delete; others read only with the creator's name", async () => {
  const { $ } = await page({});
  assert.ok($('[data-list="user-pat-produced"] [data-act="edit"]'));
  assert.ok($('[data-list="user-pat-produced"] [data-act="delete"]'));
  assert.equal($('[data-list="user-joe"] [data-act="edit"]'), null);
  assert.equal($('[data-list="user-joe"] [data-act="delete"]'), null);
  assert.match($('[data-list="user-joe"] [data-readonly]').textContent, /Read only — created by joe/);
});

test("other pages: nothing is added", async () => {
  const { $, calls } = await page({}, "https://requests.example.com/discover/movies");
  assert.equal($("#hg-collections"), null);
  assert.equal(calls.filter((c) => c.url.startsWith(API)).length, 0);
});

test("a name that fits several people: pick list with photo and known-for, a pick previews again with it", async () => {
  const seen = [];
  const { w, $, $$ } = await page({ [API + "preview"]: (url) => { seen.push(url); return url.includes("pick=") ? OK : PICK; } });
  type(w, $("[data-field=name]"), "Pat produced");
  type(w, $("[data-field=query]"), "producer:Pat Example");
  $('[data-act="preview"]').click();
  await settle();
  assert.match(seen[0], /preview\?q=producer%3APat%20Example&sort=release&max=50$/);
  const opts = $$("[data-pick]");
  assert.equal(opts.length, 2);
  assert.match($("[data-picks]").textContent, /Which “Pat Example” \(producer\)\?/);
  assert.match(opts[0].textContent, /Fake Film A, Fake Show/);
  assert.equal(opts[0].querySelector("img").getAttribute("src"), "https://image.tmdb.org/t/p/w185/pat.jpg");
  assert.equal(opts[1].querySelector("img"), null);
  opts[0].click();
  await settle();
  assert.match(seen[1], /&pick=0%3A501$/);
  assert.equal($$("[data-picks] [data-pick]").length, 0);
  assert.match($("[data-preview] [data-counts]").textContent, /2 titles · 1 on the server/);
  const cards = $$("[data-preview] [data-title]");
  assert.deepEqual(cards.map((c) => c.getAttribute("data-title")), ["movie/3", "tv/4"]);
  assert.match(cards[0].textContent, /✓ On the server/);
  assert.match(cards[1].textContent, /⬇ Not downloaded yet/);
  assert.equal(cards[1].querySelector("a").getAttribute("href"), "/tv/4");
});

test("save sends name, query, sort, max and the picks as JSON; the lists reload with a note", async () => {
  let saved = false;
  const { w, $, calls } = await page({ [API + "preview"]: (url) => (url.includes("pick=") ? OK : PICK), [API + "save"]: () => { saved = true; return { status: 200, body: list({ id: "user-new" }) }; } });
  type(w, $("[data-field=name]"), "New list");
  type(w, $("[data-field=query]"), "producer:Pat Example");
  w.document.querySelector("[data-field=sort]").value = "popularity";
  type(w, $("[data-field=max]"), "20");
  $('[data-act="preview"]').click();
  await settle();
  $$sel(w, "[data-pick]")[0].click();
  await settle();
  $('[data-act="save"]').click();
  await settle();
  assert.ok(saved);
  assert.deepEqual(posts(calls), [{ url: API + "save", type: "application/json", body: { name: "New list", query: "producer:Pat Example", sort: "popularity", max: 20, picks: { 0: 501 } } }]);
  assert.match($("[data-flash]").textContent, /Saved\. Jellyfin shows it under Collections & Trending in a few minutes\./);
  assert.equal(calls.filter((c) => c.url === API + "lists").length, 2);
  assert.equal($("[data-field=name]").value, "");
});

function $$sel(w, q) { return [...w.document.querySelectorAll(q)]; }

test("Save waits for a preview of what is in the form", async () => {
  const { w, $ } = await page({ [API + "preview"]: OK });
  assert.equal($('[data-act="save"]').disabled, true);
  type(w, $("[data-field=name]"), "Mine");
  type(w, $("[data-field=query]"), "producer:Pat Example");
  $('[data-act="preview"]').click();
  await settle();
  assert.equal($('[data-act="save"]').disabled, false);
  type(w, $("[data-field=query]"), "producer:Pat Example year:2001");
  assert.equal($('[data-act="save"]').disabled, true);
});

test("Edit fills the form with the list and saves under its id", async () => {
  const { w, $, calls } = await page({ [API + "preview"]: OK, [API + "save"]: { status: 200, body: list() } });
  $('[data-list="user-pat-produced"] [data-act="edit"]').click();
  await settle();
  assert.equal($("[data-field=name]").value, "Pat produced");
  assert.equal($("[data-field=query]").value, "producer:Pat Example");
  assert.match($("[data-form-title]").textContent, /Edit “Pat produced”/);
  $('[data-act="preview"]').click();
  await settle();
  $('[data-act="save"]').click();
  await settle();
  assert.equal(posts(calls)[0].body.id, "user-pat-produced");
});

test("Delete needs a second tap and says the films stay", async () => {
  const { $, calls } = await page({ [API + "delete"]: { status: 200, body: { deleted: "user-pat-produced" } } });
  const del = $('[data-list="user-pat-produced"] [data-act="delete"]');
  del.click();
  await settle();
  assert.equal(posts(calls).length, 0);
  assert.equal(del.textContent, "Tap again to delete");
  assert.match($('[data-list="user-pat-produced"] [data-warning]').textContent, /films and series on the server stay/);
  del.click();
  await settle();
  assert.deepEqual(posts(calls), [{ url: API + "delete", type: "application/json", body: { id: "user-pat-produced", confirm: "delete" } }]);
});

test("refusals in plain words: limit, name taken, not found", async () => {
  const { w, $ } = await page({ [API + "preview"]: { status: 404, body: { error: "not-found", term: 0 } } });
  type(w, $("[data-field=query]"), "producer:Nobody");
  $('[data-act="preview"]').click();
  await settle();
  assert.match($("[data-msg]").textContent, /Nothing found for “producer:Nobody”/);
  const { w: w2, $: $2 } = await page({ [API + "preview"]: OK, [API + "save"]: { status: 409, body: { error: "limit-user" } } });
  type(w2, $2("[data-field=name]"), "Sixth");
  type(w2, $2("[data-field=query]"), "producer:Pat Example");
  $2('[data-act="preview"]').click();
  await settle();
  $2('[data-act="save"]').click();
  await settle();
  assert.match($2("[data-msg]").textContent, /You already have 5 lists/);
});

test("a user who may not request sees the lists but no form", async () => {
  const body = JSON.parse(JSON.stringify(LISTS.body));
  body.me.create = false;
  const { $ } = await page({ [API + "lists"]: { status: 200, body } });
  assert.equal($("[data-form]"), null);
  assert.match($("[data-no-create]").textContent, /Only users who may request titles can create lists/);
  assert.equal($$count($, "[data-list]"), 2);
});

function $$count($, q) { return $(q) ? [...$(q).ownerDocument.querySelectorAll(q)].length : 0; }

test("names, titles and queries are text, never markup; odd image paths load nothing", async () => {
  const evil = list({ id: "user-x", name: '<img src=x onerror="alert(1)">', query: "<b>q</b>", items: [item(9, false, { t: "<script>x</script>", p: "javascript:alert(1)" })] });
  const { $, $$ } = await page({ [API + "lists"]: { status: 200, body: Object.assign({}, LISTS.body, { lists: [evil] }) } });
  assert.match($('[data-list="user-x"] h2').textContent, /<img src=x/);
  assert.equal($$('[data-list="user-x"] img').length, 0);
  assert.equal($$("#hg-collections script, #hg-collections b").length, 0);
});

test("German UI: Sammlungen, German buttons and counts", async () => {
  const { w, $ } = await page({}, "https://requests.example.com/collections", PAGE.replace(">Series<", ">Serien<"));
  assert.equal($("#hg-collections h1").textContent, "Sammlungen");
  assert.equal(w.document.title, "Sammlungen - Seerr");
  assert.match($('[data-list="user-pat-produced"] [data-counts]').textContent, /2 Titel · 1 auf dem Server/);
  assert.equal($('[data-act="preview"]').textContent, "Vorschau");
});

test("the search help is a hint box listing every term, not a browser tooltip", async () => {
  const { w, $ } = await page({});
  const help = $("[data-help]");
  assert.ok(w.hgTip.get(help));
  for (const t of ["actor:", "director:", "producer:", "writer:", "studio:", "franchise:", "genre:", "year:", "type:", "keyword:"]) assert.match(w.hgTip.get(help), new RegExp(t));
});

test("a fresh load waits for React's hydration before putting the page in, and clears the early mark", async () => {
  const s = world({ "/api/v1/auth/me": ME, [API + "lists"]: LISTS });
  const w = load("collections.js", `<div id="__next"><div></div></div>${PAGE}`, (win) => { s.before(win); win.document.documentElement.setAttribute("data-hg-early", "collections"); }, "https://requests.example.com/collections");
  await settle();
  assert.equal(w.document.getElementById("hg-collections"), null);
  assert.equal(w.document.documentElement.hasAttribute("data-hg-collections-wait"), true);
  w.document.querySelector("#__next > div")["__reactFiber$x"] = {};
  await new Promise((r) => setTimeout(r, 60));
  await settle();
  assert.ok(w.document.querySelector("main > #hg-collections"));
  assert.equal(w.document.documentElement.hasAttribute("data-hg-early"), false);
  assert.equal(w.document.documentElement.hasAttribute("data-hg-collections-wait"), false);
});
