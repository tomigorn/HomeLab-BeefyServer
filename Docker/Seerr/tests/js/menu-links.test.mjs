import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { load, SIDEBAR, MOBILE, tick } from "./fixture.mjs";

const links = (w) => [...w.document.querySelectorAll('[data-hg-link="audiobooks"]')];

test("desktop sidebar: Audiobooks right after Series, plain link, same tab", () => {
  const w = load("menu-links.js", SIDEBAR);
  const [a] = links(w);
  assert.ok(a);
  assert.equal(a.previousElementSibling.getAttribute("href"), "/discover/tv");
  assert.equal(a.getAttribute("href"), "https://books.holy-grail.ch/");
  assert.equal(a.getAttribute("target"), null);
  assert.equal(a.textContent.trim(), "Audiobooks");
  assert.equal(a.querySelector("svg").getAttribute("class"), "mr-3 h-6 w-6");
  assert.ok(a.querySelector("path"));
});

test("an entry cloned from an active link is not shown active", () => {
  const w = load("menu-links.js", SIDEBAR.replace('hover:bg-gray-700 focus:bg-gray-700"><svg class="mr-3 h-6 w-6"></svg>Series', 'bg-gradient-to-br from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500"><svg class="mr-3 h-6 w-6"></svg>Series'));
  const [a] = links(w);
  assert.equal(a.classList.contains("bg-gradient-to-br"), false);
  assert.equal(a.classList.contains("hover:bg-gray-700"), true);
});

test("phone, bar with a more button: in the more list (not active), not in the bar", () => {
  const w = load("menu-links.js", MOBILE);
  const all = links(w);
  assert.equal(all.length, 1);
  assert.equal(all[0].classList.contains("text-indigo-500"), false);
  assert.equal(all[0].getAttribute("role"), null);
  assert.equal(all[0].querySelector("span").textContent, "Audiobooks");
  assert.equal(all[0].querySelector("svg").getAttribute("class"), "h-5 w-5");
});

test("phone, bar of 4 without a more button (regular user): 5th item in the bar", () => {
  const bar = MOBILE.replace(/<button[^]*?<\/button>/, "").replace('<a href="/" class="relative flex flex-col', '<a href="/discover/movies" class="relative flex flex-col items-center space-y-1 "><svg class="h-6 w-6"></svg></a><a href="/requests" class="relative flex flex-col items-center space-y-1 "><svg class="h-6 w-6"></svg></a><a href="/" class="relative flex flex-col');
  const w = load("menu-links.js", bar);
  const inBar = links(w).filter((a) => a.classList.contains("flex-col"));
  assert.equal(inBar.length, 1);
  assert.equal(inBar[0].previousElementSibling.getAttribute("href"), "/discover/tv");
  assert.equal(inBar[0].getAttribute("aria-label"), "Audiobooks");
  assert.ok(inBar[0].querySelector("path"));
  assert.equal(inBar[0].parentElement.querySelectorAll("a").length, 6);
});

test("phone, bar already full (5 links, no more button): not added", () => {
  const five = MOBILE.replace(/<button[^]*?<\/button>/, "").replace('<a href="/" class="relative flex flex-col', ['/a', '/b', '/c'].map((h) => `<a href="${h}" class="relative flex flex-col items-center space-y-1 "><svg class="h-6 w-6"></svg></a>`).join("") + '<a href="/" class="relative flex flex-col');
  const w = load("menu-links.js", five);
  assert.equal(links(w).filter((a) => a.classList.contains("flex-col")).length, 0);
});

test("a menu that renders later gets it once, re-renders do not duplicate", async () => {
  const w = load("menu-links.js", "<main></main>");
  w.document.body.insertAdjacentHTML("beforeend", SIDEBAR);
  for (let i = 0; i < 5; i++) await tick();
  w.document.body.insertAdjacentHTML("beforeend", "<p>x</p>");
  for (let i = 0; i < 5; i++) await tick();
  assert.equal(links(w).length, 1);
});

test("badges of the cloned link are dropped", () => {
  const w = load("menu-links.js", SIDEBAR.replace('<svg class="mr-3 h-6 w-6"></svg>Series</a>', '<svg class="mr-3 h-6 w-6"></svg>Series<div class="ml-auto flex"><span>2</span></div></a>'));
  assert.equal(links(w)[0].querySelector("div"), null);
});

test("Seerr without the Series link: nothing is added, nothing breaks", () => {
  const w = load("menu-links.js", SIDEBAR.replaceAll("/discover/tv", "/discover/shows"));
  assert.equal(links(w).length, 0);
});

test("Discover: the Popular Series slider title (also a /discover/tv link, inside main) gets no entry", () => {
  const slider = `<main class="relative top-16 z-0"><div class="slider-header"><a class="slider-title min-w-0 pr-16" href="/discover/tv"><span class="truncate">Popular Series</span><svg></svg></a></div></main>`;
  const w = load("menu-links.js", SIDEBAR + slider + MOBILE);
  assert.equal(w.document.querySelector(".slider-header").children.length, 1);
  assert.equal(links(w).filter((a) => a.closest("main")).length, 0);
  assert.equal(links(w).length, 2);
});

test("German UI: the entry reads Hörbücher", () => {
  const w = load("menu-links.js", SIDEBAR.replace(">Series</a>", ">Serien</a>"));
  const [a] = links(w);
  assert.equal(a.textContent.trim(), "Hörbücher");
  assert.equal(w.hgTip.get(a), "Hörbücher");
});

const manual = (w) => [...w.document.querySelectorAll('[data-hg-link="manual"]')];
const counting = (status, body, calls = []) => (w) => {
  w.fetch = (url, opts) => { calls.push({ url, opts }); return Promise.resolve({ ok: status === 200, status, json: () => Promise.resolve(body) }); };
};
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };

test("Manual interaction: after Audiobooks, opens the page", () => {
  const w = load("menu-links.js", SIDEBAR);
  const [m] = manual(w);
  assert.ok(m);
  assert.equal(m.previousElementSibling.getAttribute("data-hg-link"), "audiobooks");
  assert.equal(m.getAttribute("href"), "/manual");
  assert.equal(m.textContent.trim(), "Manual interaction");
  assert.ok(m.querySelector("path").getAttribute("d").length > 20);
  assert.notEqual(m.querySelector("path").getAttribute("d"), links(w)[0].querySelector("path").getAttribute("d"));
});

test("German UI: Manuell beheben", () => {
  const w = load("menu-links.js", SIDEBAR.replace(">Series</a>", ">Serien</a>"));
  const [m] = manual(w);
  assert.equal(m.textContent.trim(), "Manuell beheben");
  assert.equal(m.getAttribute("href"), "/manual");
});

test("open problems: count badge on every Manual entry, asked once", async () => {
  const calls = [];
  const w = load("menu-links.js", SIDEBAR + MOBILE, counting(200, { open: 3 }, calls));
  await settle();
  const badges = manual(w).map((m) => m.querySelector("[data-hg-count]"));
  assert.ok(badges.length >= 2);
  badges.forEach((b) => assert.equal(b.textContent, "3"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/_download-now/manual/count");
  assert.equal(manual(w)[0].getAttribute("aria-label"), null);
  assert.equal(w.hgTip.get(manual(w)[0]), "Manual interaction (3)");
  const tap = new w.MouseEvent("click", { bubbles: true, cancelable: true });
  const down = new w.MouseEvent("pointerdown", { bubbles: true });
  Object.defineProperty(down, "pointerType", { value: "touch" });
  manual(w)[0].dispatchEvent(down);
  manual(w)[0].dispatchEvent(tap);
  assert.equal(w.document.getElementById("hg-tip"), null, "a tap on a menu link navigates, no hint first");
});

test("no open problems or no session: no badge", async () => {
  for (const [status, body] of [[200, { open: 0 }], [401, {}], [200, { open: "x" }]]) {
    const w = load("menu-links.js", SIDEBAR, counting(status, body));
    await settle();
    assert.equal(w.document.querySelector("[data-hg-count]"), null, JSON.stringify(body));
  }
});

test("a menu that renders later gets the badge too", async () => {
  const w = load("menu-links.js", "<main></main>", counting(200, { open: 2 }));
  await settle();
  w.document.body.insertAdjacentHTML("beforeend", SIDEBAR);
  await settle();
  assert.equal(manual(w)[0].querySelector("[data-hg-count]").textContent, "2");
});

test("phone, bar of 4 without a more button: Audiobooks 5th, Manual 6th; a full bar of 5 still gets Manual", () => {
  const bar4 = MOBILE.replace(/<button[^]*?<\/button>/, "").replace('<a href="/" class="relative flex flex-col', '<a href="/discover/movies" class="relative flex flex-col items-center space-y-1 "><svg class="h-6 w-6"></svg></a><a href="/requests" class="relative flex flex-col items-center space-y-1 "><svg class="h-6 w-6"></svg></a><a href="/" class="relative flex flex-col');
  let w = load("menu-links.js", bar4);
  const bar = w.document.querySelector(".flex.h-full");
  assert.deepEqual([...bar.querySelectorAll("a")].map((a) => a.getAttribute("data-hg-link")).slice(-2), ["audiobooks", "manual"]);
  const five = MOBILE.replace(/<button[^]*?<\/button>/, "").replace('<a href="/" class="relative flex flex-col', ['/a', '/b', '/c'].map((h) => `<a href="${h}" class="relative flex flex-col items-center space-y-1 "><svg class="h-6 w-6"></svg></a>`).join("") + '<a href="/" class="relative flex flex-col');
  w = load("menu-links.js", five);
  assert.deepEqual(manual(w).filter((a) => a.classList.contains("flex-col")).length, 1);
  assert.equal(links(w).filter((a) => a.classList.contains("flex-col")).length, 0);
});

test("on /manual the Manual entry looks active like Seerr's own, elsewhere not", async () => {
  let w = load("menu-links.js", SIDEBAR + MOBILE, null, "https://requests.example.com/manual");
  const [side, drawer] = manual(w);
  assert.equal(side.classList.contains("bg-gradient-to-br"), true);
  assert.equal(side.classList.contains("hover:bg-gray-700"), false);
  assert.equal(drawer.classList.contains("text-indigo-500"), true);
  w.history.pushState({}, "", "/discover/movies");
  w.document.body.insertAdjacentHTML("beforeend", "<p>x</p>");
  await settle();
  assert.equal(manual(w)[0].classList.contains("bg-gradient-to-br"), false);
  w = load("menu-links.js", SIDEBAR, null, "https://requests.example.com/discover/movies");
  assert.equal(manual(w)[0].classList.contains("bg-gradient-to-br"), false);
});

test("a click on the Manual entry navigates inside Seerr when its router is there", () => {
  const pushed = [];
  const w = load("menu-links.js", SIDEBAR, (win) => { win.next = { router: { push: (u, as) => { pushed.push([u, as]); return Promise.resolve(true); } } }; });
  const ev = new w.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
  manual(w)[0].dispatchEvent(ev);
  // Seerr has no /manual page: Next would reload the whole page (menu flashes), so its 404 page is shown under /manual, as on a direct load
  assert.deepEqual(pushed, [["/404", "/manual"]]);
  assert.equal(ev.defaultPrevented, true);
  const plain = new w.MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ctrlKey: true });
  manual(w)[0].dispatchEvent(plain);
  assert.equal(pushed.length, 1);
});

test("menu shown as soon as our entries are in: html gets data-hg-menu on the next frame, not after images load", async () => {
  const w = load("menu-links.js", SIDEBAR);
  assert.equal(w.document.documentElement.hasAttribute("data-hg-menu"), false);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(w.document.documentElement.hasAttribute("data-hg-menu"), true);
});

test("menu-links.css hides Seerr's menu until ready, outside page content, and shows it anyway after a timeout", () => {
  const css = readFileSync(new URL("../../indexer-bar/www/menu-links.css", import.meta.url), "utf8");
  assert.match(css, /html:not\(\[data-hg-menu\]\)/);
  assert.match(css, /:has\(> ?a\[href="\/discover\/tv"\]\)/);
  assert.match(css, /:not\(main \*\)/);
  assert.match(css, /visibility: ?hidden/);
  assert.match(css, /animation:[^;]*\b2\.5s/);
  assert.match(css, /@keyframes[^{]*\{ ?to ?\{ ?visibility: ?visible/);
});

const frames = (n = 4) => new Promise((r) => setTimeout(r, 20 * n));
const hydrate = (w) => { w.document.getElementById("__next").firstElementChild["__reactFiber$test"] = {}; };

test("Next page: nothing is added before React hydrated Seerr's markup (no hydration error #418, no redraw), then the menu is completed and shown", async () => {
  const w = load("menu-links.js", `<div id="__next"><div class="flex h-full">${SIDEBAR}</div></div>`);
  await frames();
  assert.equal(links(w).length, 0);
  assert.equal(w.document.documentElement.hasAttribute("data-hg-menu"), false);
  hydrate(w);
  await frames();
  assert.equal(links(w).length, 1);
  assert.equal(manual(w).length, 1);
  assert.equal(w.document.documentElement.hasAttribute("data-hg-menu"), true);
});

test("Next page hydrated before the script runs: entries at once", () => {
  const w = load("menu-links.js", `<div id="__next"><div class="flex h-full">${SIDEBAR}</div></div>`, (win) => { win.document.getElementById("__next").firstElementChild["__reactFiber$x"] = {}; });
  assert.equal(links(w).length, 1);
});

test("a Next page that renders its menu after hydration still gets the entries", async () => {
  const w = load("menu-links.js", `<div id="__next"><div class="flex h-full"></div></div>`);
  hydrate(w);
  await frames();
  w.document.querySelector("#__next > div").insertAdjacentHTML("beforeend", SIDEBAR);
  await frames();
  assert.equal(links(w).length, 1);
});

test("Manual count in the sidebar: label stays on one line, the badge sits on the icon's corner, the entry's height never changes", async () => {
  const w = load("menu-links.js", SIDEBAR + MOBILE, counting(200, { open: 3 }));
  await settle();
  const [side, drawer] = manual(w);
  assert.equal(side.style.whiteSpace, "nowrap");
  assert.equal(side.style.position, "relative");
  const b = side.querySelector("[data-hg-count]");
  assert.equal(b.style.position, "absolute");
  assert.equal(b.style.marginLeft, "");
  assert.equal(drawer.querySelector("[data-hg-count]").style.marginLeft, "auto", "phone drawer (full width): badge at the end as before");
});
