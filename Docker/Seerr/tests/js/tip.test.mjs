import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, tick, tipText, SEERR_TIP } from "./fixture.mjs";

const open = [];
afterEach(() => { while (open.length) open.pop().close(); });
const page = (body) => { const w = load("tip.js", body); open.push(w); return w; };
const ev = (w, type, pointerType = "mouse") => { const e = new w.MouseEvent(type, { bubbles: type !== "pointerenter" && type !== "pointerleave", cancelable: true }); Object.defineProperty(e, "pointerType", { value: pointerType }); return e; };
const tap = (w, el) => { el.dispatchEvent(ev(w, "pointerdown", "touch")); const c = ev(w, "click", "touch"); el.dispatchEvent(c); return c; };
const box = (w) => w.document.getElementById("hg-tip");
const shown = (w) => { const b = box(w); return !!b && w.getComputedStyle(b).display !== "none"; };

test("instant on mouse hover, gone on leave; role tooltip and aria-describedby", () => {
  const w = page('<span id="a">Remux</span>');
  const a = w.document.getElementById("a");
  w.hgTip.set(a, "The untouched disc copy");
  a.dispatchEvent(ev(w, "pointerenter"));
  assert.ok(shown(w));
  assert.equal(box(w).getAttribute("role"), "tooltip");
  assert.equal(a.getAttribute("aria-describedby"), "hg-tip");
  assert.equal(tipText(box(w)), "The untouched disc copy");
  a.dispatchEvent(ev(w, "pointerleave"));
  assert.equal(shown(w), false);
  assert.equal(a.hasAttribute("aria-describedby"), false);
});

test("keyboard focus shows it, blur and Escape hide it", () => {
  const w = page('<button id="a">x</button>');
  const a = w.document.getElementById("a");
  w.hgTip.set(a, "Remove");
  a.dispatchEvent(new w.FocusEvent("focus"));
  assert.ok(shown(w));
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));
  assert.equal(shown(w), false);
  a.dispatchEvent(new w.FocusEvent("focus"));
  a.dispatchEvent(new w.FocusEvent("blur"));
  assert.equal(shown(w), false);
});

test("one box at a time: hovering the next element moves the box", () => {
  const w = page('<span id="a">a</span><span id="b">b</span>');
  const [a, b] = ["a", "b"].map((id) => w.document.getElementById(id));
  w.hgTip.set(a, "first");
  w.hgTip.set(b, "second");
  a.dispatchEvent(ev(w, "pointerenter"));
  b.dispatchEvent(ev(w, "pointerenter"));
  assert.equal(w.document.querySelectorAll("#hg-tip").length, 1);
  assert.equal(tipText(box(w)), "second");
  assert.equal(a.hasAttribute("aria-describedby"), false);
});

test("touch: the first tap shows it and stops the click, the second goes through", () => {
  const w = page('<a id="a" href="/movie/1">Processing</a>');
  const a = w.document.getElementById("a");
  let clicks = 0;
  w.document.body.addEventListener("click", (e) => { if (!e.defaultPrevented) clicks++; });
  w.hgTip.set(a, "Downloading now");
  const first = tap(w, a);
  assert.equal(first.defaultPrevented, true);
  assert.ok(shown(w));
  assert.equal(clicks, 0);
  const second = tap(w, a);
  assert.equal(second.defaultPrevented, false);
  assert.equal(clicks, 1);
  assert.equal(shown(w), false);
});

test("touch with tap off: the tap goes straight through, no box", () => {
  const w = page('<a id="a" href="/manual">Manual</a>');
  const a = w.document.getElementById("a");
  w.hgTip.set(a, "Manual interaction (3)", { tap: false });
  assert.equal(tap(w, a).defaultPrevented, false);
  assert.equal(shown(w), false);
});

test("outside click and scroll close it", () => {
  const w = page('<span id="a">a</span><p id="p">elsewhere</p>');
  const a = w.document.getElementById("a");
  w.hgTip.set(a, "hint");
  a.dispatchEvent(ev(w, "pointerenter"));
  w.document.getElementById("p").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert.equal(shown(w), false);
  a.dispatchEvent(ev(w, "pointerenter"));
  w.dispatchEvent(new w.Event("scroll"));
  assert.equal(shown(w), false);
});

test("structured content: bold headline, label rows, monospace line, separators; data stays text", () => {
  const w = page('<span id="a">⚠️</span>');
  const a = w.document.getElementById("a");
  w.hgTip.set(a, [
    { title: "⚠️ Needs a manual import", rows: [["Release:", ""], { mono: "<b>Film.2004.1080p</b>-GRP" }, ["What to do:", "open Manual interaction"]] },
    { title: "🌙 Waiting for the night", rows: ["Downloads at night"] },
  ]);
  a.dispatchEvent(ev(w, "pointerenter"));
  const b = box(w);
  assert.equal(b.querySelector(".hg-tip-title").textContent, "⚠️ Needs a manual import");
  assert.equal(b.querySelector(".hg-tip-label").textContent, "Release:");
  assert.equal(b.querySelector(".hg-tip-mono").textContent, "<b>Film.2004.1080p</b>-GRP");
  assert.equal(b.querySelector("b"), null);
  assert.equal(b.querySelectorAll(".hg-tip-sep").length, 1);
  assert.equal(tipText(b), "⚠️ Needs a manual import\nRelease:\n<b>Film.2004.1080p</b>-GRP\nWhat to do: open Manual interaction\n🌙 Waiting for the night\nDownloads at night");
  assert.equal(w.hgTip.text(w.hgTip.get(a)), tipText(b));
  assert.match(w.document.getElementById("hg-tip-style").textContent, /#hg-tip\{[^}]*max-width:min\(22rem/);
});

test("plain strings: one line per newline; headline() splits at the dash", () => {
  const w = page('<span id="a">x</span>');
  const a = w.document.getElementById("a");
  w.hgTip.set(a, "Season 1: 10 episodes\nSeason 2: 12 episodes");
  a.dispatchEvent(ev(w, "pointerenter"));
  assert.equal(box(w).querySelectorAll("[data-hg-tip-line]").length, 2);
  assert.equal(JSON.stringify(w.hgTip.headline("Requested — approved, waiting for the search")), JSON.stringify({ title: "Requested", rows: ["Approved, waiting for the search"] }));
  assert.equal(w.hgTip.headline("Importing…"), "Importing…");
});

test("an element without text gets the hint as aria-label; empty content removes the hint", () => {
  const w = page('<span id="a"></span><span id="b">text</span>');
  const [a, b] = ["a", "b"].map((id) => w.document.getElementById(id));
  w.hgTip.set(a, { title: "🌙 Waiting", rows: ["at night"] });
  w.hgTip.set(b, "hint");
  assert.equal(a.getAttribute("aria-label"), "🌙 Waiting\nat night");
  assert.equal(b.hasAttribute("aria-label"), false);
  w.hgTip.set(a, "");
  a.dispatchEvent(ev(w, "pointerenter"));
  assert.equal(shown(w), false);
  assert.equal(a.hasAttribute("aria-label"), false);
});

test("content as a function: asked at show time, empty = no box", () => {
  const w = page('<span id="a">4K · cut off</span>');
  const a = w.document.getElementById("a");
  let cut = false;
  w.hgTip.set(a, () => (cut ? a.textContent : ""));
  a.dispatchEvent(ev(w, "pointerenter"));
  assert.equal(shown(w), false);
  cut = true;
  a.dispatchEvent(ev(w, "pointerenter"));
  assert.equal(tipText(box(w)), "4K · cut off");
});

test("Seerr's own tooltip opens for the element: our text goes into it as the first block, never two boxes", async () => {
  const w = page('<a id="a" href="/movie/1?manage=1">Processing</a>');
  const a = w.document.getElementById("a");
  w.hgTip.set(a, { title: "⚠️ Needs a manual import", rows: ["What to do: open Manual interaction"] });
  a.dispatchEvent(ev(w, "pointerenter"));
  w.document.body.insertAdjacentHTML("beforeend", SEERR_TIP('<div class="p-4"><div>Film.2004.1080p-GRP</div></div>'));
  await tick();
  const seerr = w.document.body.lastElementChild;
  assert.equal(shown(w), false, "our own box is closed");
  const first = seerr.firstElementChild;
  assert.ok(first.hasAttribute("data-hg-tip-merged"));
  assert.equal(tipText(first), "⚠️ Needs a manual import\nWhat to do: open Manual interaction");
  assert.match(seerr.textContent, /Film\.2004\.1080p-GRP/);
  // React re-renders its text: our block comes back first, once
  seerr.textContent = "Manage Movie";
  await tick();
  assert.equal(seerr.querySelectorAll("[data-hg-tip-merged]").length, 1);
  assert.equal(seerr.firstElementChild.getAttribute("data-hg-tip-merged"), "");
  // Seerr's box goes while the pointer is still on the element: ours shows again
  seerr.remove();
  await tick();
  assert.ok(shown(w));
});

test("Seerr's box already open (touch: it opens before the click): the tap puts our text into it", async () => {
  const w = page('<a id="a" href="/movie/1">Requested</a>' + SEERR_TIP("Manage Movie"));
  const a = w.document.getElementById("a");
  w.hgTip.set(a, "Requested — approved");
  tap(w, a);
  const seerr = w.document.querySelector("body > .z-50");
  assert.equal(shown(w), false);
  assert.equal(tipText(seerr.firstElementChild), "Requested — approved");
});

test("Seerr's box hidden (phones hide the download popover): ours shows", async () => {
  const w = page('<a id="a" href="/movie/1">Processing</a>');
  const a = w.document.getElementById("a");
  w.hgTip.set(a, "Downloading now");
  a.dispatchEvent(ev(w, "pointerenter"));
  w.document.body.insertAdjacentHTML("beforeend", SEERR_TIP("bars").replace('class="', 'style="display:none" class="'));
  await tick();
  assert.ok(shown(w));
  assert.equal(w.document.querySelector("[data-hg-tip-merged]"), null);
});

test("kept inside the viewport: flips above near the bottom, never past the right edge", () => {
  const w = page('<span id="a">x</span>');
  const a = w.document.getElementById("a");
  w.hgTip.set(a, "hint");
  Object.defineProperty(w, "innerWidth", { value: 390 });
  Object.defineProperty(w, "innerHeight", { value: 800 });
  a.getBoundingClientRect = () => ({ left: 380, right: 388, top: 780, bottom: 796, width: 8, height: 16 });
  Object.defineProperty(w.HTMLElement.prototype, "offsetWidth", { configurable: true, get() { return this.id === "hg-tip" ? 200 : 0; } });
  Object.defineProperty(w.HTMLElement.prototype, "offsetHeight", { configurable: true, get() { return this.id === "hg-tip" ? 40 : 0; } });
  a.dispatchEvent(ev(w, "pointerenter"));
  assert.equal(box(w).getAttribute("data-side"), "above");
  assert.equal(box(w).style.top, "734px");
  assert.equal(box(w).style.left, "182px");
});

// Seerr's box (popper: followCursor, auto placement) with our block: pinned to the element instead, not moved by popper's inline transform
const pinPage = (rect, size = { w: 300, h: 80 }, view = { w: 1280, h: 800 }) => {
  const w = page('<a id="a" href="/tv/1?manage=1">Available</a>');
  const a = w.document.getElementById("a");
  Object.defineProperty(w, "innerWidth", { value: view.w });
  Object.defineProperty(w, "innerHeight", { value: view.h });
  a.getBoundingClientRect = () => ({ ...rect, right: rect.left + 80, bottom: rect.top + 20, width: 80, height: 20 });
  Object.defineProperty(w.HTMLElement.prototype, "offsetWidth", { configurable: true, get() { return /\bz-50\b/.test(this.className) || this.id === "hg-tip" ? size.w : 0; } });
  Object.defineProperty(w.HTMLElement.prototype, "offsetHeight", { configurable: true, get() { return /\bz-50\b/.test(this.className) || this.id === "hg-tip" ? size.h : 0; } });
  w.hgTip.set(a, { title: "✅ Everything released so far is in Jellyfin", rows: ["Next episode expected 18 Nov"] });
  return { w, a };
};
const pos = (box) => [box.style.getPropertyValue("--hg-tip-x"), box.style.getPropertyValue("--hg-tip-y")];
const popper = (w, box, x, y) => { box.style.transform = `translate(${x}px, ${y}px)`; box.style.inset = "0px auto auto 0px"; w.document.dispatchEvent(new w.MouseEvent("mousemove", { clientX: x, clientY: y })); };

test("Seerr's box with our block is pinned below the element, left edges aligned, the same place across mouse moves", async () => {
  const { w, a } = pinPage({ left: 400, top: 300 });
  a.dispatchEvent(ev(w, "pointerenter"));
  w.document.body.insertAdjacentHTML("beforeend", SEERR_TIP("Play on Jellyfin").replace('class="', 'style="position: absolute; inset: 0px auto auto 0px; transform: translate(520px, 330px);" class="'));
  await tick();
  const seerr = w.document.body.lastElementChild;
  assert.ok(seerr.hasAttribute("data-hg-tip-pin"));
  assert.equal(seerr.getAttribute("data-side"), "below");
  assert.deepEqual(pos(seerr), ["400px", "326px"]);
  popper(w, seerr, 610, 280);
  await tick();
  assert.deepEqual(pos(seerr), ["400px", "326px"]);
  popper(w, seerr, 120, 340);
  await tick();
  assert.deepEqual(pos(seerr), ["400px", "326px"]);
  const css = w.document.getElementById("hg-tip-style").textContent;
  const rule = /\[data-hg-tip-pin\]\{([^}]*)\}/.exec(css)[1];
  for (const d of ["position:fixed !important", "left:var(--hg-tip-x) !important", "top:var(--hg-tip-y) !important", "right:auto !important", "bottom:auto !important", "transform:none !important", "margin:0 !important"]) assert.ok(rule.includes(d), d);
});

test("pinned box: flips above when there is no room below, kept inside the viewport, follows scroll", async () => {
  const { w, a } = pinPage({ left: 1200, top: 760 });
  a.dispatchEvent(ev(w, "pointerenter"));
  w.document.body.insertAdjacentHTML("beforeend", SEERR_TIP("Play on Jellyfin"));
  await tick();
  const seerr = w.document.body.lastElementChild;
  assert.equal(seerr.getAttribute("data-side"), "above");
  assert.deepEqual(pos(seerr), ["972px", "674px"]);
  a.getBoundingClientRect = () => ({ left: 1200, top: 200, right: 1280, bottom: 220, width: 80, height: 20 });
  w.dispatchEvent(new w.Event("scroll"));
  assert.equal(seerr.getAttribute("data-side"), "below");
  assert.deepEqual(pos(seerr), ["972px", "226px"]);
});

test("Seerr's box without our block is left to Seerr (not pinned)", async () => {
  const w = page('<a id="a" href="/tv/1">Available</a>');
  w.document.body.insertAdjacentHTML("beforeend", SEERR_TIP("Play on Jellyfin"));
  await tick();
  assert.equal(w.document.querySelector("[data-hg-tip-pin]"), null);
});
