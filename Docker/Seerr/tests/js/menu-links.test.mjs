import { test } from "node:test";
import assert from "node:assert/strict";
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
  assert.equal(inBar[0].parentElement.querySelectorAll("a").length, 5);
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
  assert.equal(a.getAttribute("title"), "Hörbücher");
});
