// Hint boxes for every script of ours (README "Hint boxes"): Seerr's tooltip look, instant on hover and keyboard focus, first tap on touch, one box at a time. When Seerr opens its own tooltip for the same element, our text goes into that one as its first block instead. Every Seerr tooltip is pinned next to its trigger instead of following the mouse. Content: a string, { title, rows, sections } or an array of those; text only, never markup.
(function () {
  "use strict";
  var ID = "hg-tip";
  var GAP = 6;
  var EDGE = 8;
  var CSS = "#hg-tip{position:fixed;z-index:10000;box-sizing:border-box;max-width:min(22rem,calc(100vw - 16px));padding:.25rem .5rem;border-radius:.25rem;background:#1f2937;color:#f3f4f6;border:1px solid #4b5563;box-shadow:0 1px 3px rgba(0,0,0,.3),0 1px 2px rgba(0,0,0,.2);font-size:.875rem;line-height:1.25rem;font-weight:400;text-align:left;white-space:normal;overflow-wrap:anywhere;pointer-events:none}" +
    "#hg-tip .hg-tip-title,[data-hg-tip-merged] .hg-tip-title{font-weight:600;color:#fff}" +
    "#hg-tip .hg-tip-label,[data-hg-tip-merged] .hg-tip-label{color:#9ca3af}" +
    "#hg-tip .hg-tip-mono,[data-hg-tip-merged] .hg-tip-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.75rem;line-height:1rem;color:#d1d5db;overflow-wrap:anywhere;margin:.1rem 0}" +
    "#hg-tip .hg-tip-sep,[data-hg-tip-merged] .hg-tip-sep{border-top:1px solid #374151;margin:.3rem 0}" +
    "[data-hg-tip-merged]{max-width:22rem;padding:0 0 .25rem;margin-bottom:.25rem;border-bottom:1px solid #374151;font-size:.875rem;line-height:1.25rem;white-space:normal;overflow-wrap:anywhere;text-align:left}" +
    "[data-hg-tip-merged].hg-tip-roomy{max-width:none;padding:.75rem 1rem;margin:0}" +
    "[data-hg-tip-pin]{position:fixed !important;left:var(--hg-tip-x) !important;top:var(--hg-tip-y) !important;right:auto !important;bottom:auto !important;transform:none !important;margin:0 !important}";
  var cur = null, host = null, pointer = null, last = null, mouse = null;

  function sections(c) {
    if (typeof c === "function") { try { c = c(); } catch (e) { c = null; } }
    if (c === null || c === undefined || c === "") return [];
    if (typeof c === "string") return [{ rows: c.split("\n").filter(Boolean) }];
    if (Array.isArray(c)) return c.reduce(function (all, x) { return all.concat(sections(x)); }, []);
    if (typeof c !== "object") return [{ rows: [String(c)] }];
    var own = c.title || (c.rows && c.rows.length) ? [{ title: c.title ? String(c.title) : "", rows: c.rows || [] }] : [];
    return own.concat(c.sections ? sections(c.sections) : []);
  }

  // one line: "text", [label, value], { label, value } or { mono: "release.name" }
  function line(doc, row) {
    var e = doc.createElement("div");
    e.setAttribute("data-hg-tip-line", "");
    if (row && typeof row === "object" && !Array.isArray(row) && row.mono !== undefined) {
      e.className = "hg-tip-mono";
      e.textContent = String(row.mono);
      return e;
    }
    var pair = Array.isArray(row) ? row : row && typeof row === "object" ? [row.label, row.value] : null;
    if (!pair) { e.className = "hg-tip-row"; e.textContent = String(row); return e; }
    e.className = "hg-tip-row";
    var label = doc.createElement("span");
    label.className = "hg-tip-label";
    label.textContent = String(pair[0] == null ? "" : pair[0]);
    e.appendChild(label);
    if (pair[1] !== undefined && pair[1] !== null && pair[1] !== "") e.appendChild(doc.createTextNode(" " + String(pair[1])));
    return e;
  }

  function render(doc, into, content) {
    into.textContent = "";
    sections(content).forEach(function (s, i) {
      if (i) { var sep = doc.createElement("div"); sep.className = "hg-tip-sep"; into.appendChild(sep); }
      if (s.title) { var t = line(doc, s.title); t.className = "hg-tip-title"; into.appendChild(t); }
      (s.rows || []).forEach(function (r) { into.appendChild(line(doc, r)); });
    });
    return into;
  }

  function rowText(row) {
    if (row && typeof row === "object" && !Array.isArray(row) && row.mono !== undefined) return String(row.mono);
    var pair = Array.isArray(row) ? row : row && typeof row === "object" ? [row.label, row.value] : null;
    if (!pair) return String(row);
    return String(pair[0] == null ? "" : pair[0]) + (pair[1] !== undefined && pair[1] !== null && pair[1] !== "" ? " " + pair[1] : "");
  }

  function text(content) {
    var out = [];
    sections(content).forEach(function (s) {
      if (s.title) out.push(s.title);
      (s.rows || []).forEach(function (r) { out.push(rowText(r)); });
    });
    return out.join("\n");
  }

  // "Requested — approved, waiting" → bold "Requested" + "Approved, waiting"; no dash = the string as it is
  function headline(s) {
    var i = String(s).indexOf(" — ");
    if (i < 0) return String(s);
    var rest = String(s).slice(i + 3);
    return { title: String(s).slice(0, i), rows: [rest.charAt(0).toUpperCase() + rest.slice(1)] };
  }

  function style(doc) {
    if (doc.getElementById("hg-tip-style")) return;
    var s = doc.createElement("style");
    s.id = "hg-tip-style";
    s.textContent = CSS;
    (doc.head || doc.documentElement).appendChild(s);
  }

  function box(doc, make) {
    var b = doc.getElementById(ID);
    if (b || !make) return b;
    style(doc);
    b = doc.createElement("div");
    b.id = ID;
    b.setAttribute("role", "tooltip");
    b.style.display = "none";
    doc.body.appendChild(b);
    return b;
  }

  // below el, left edges aligned; above when it does not fit below; inside the viewport
  function spot(doc, b, el) {
    var win = doc.defaultView || window, vw = win.innerWidth || 360, vh = win.innerHeight || 640, r = el.getBoundingClientRect();
    var w = b.offsetWidth, h = b.offsetHeight;
    var below = r.bottom + GAP, side = below + h > vh - EDGE && r.top - h - GAP >= EDGE ? "above" : "below";
    b.setAttribute("data-side", side);
    return { left: Math.max(EDGE, Math.min(Math.round(r.left), vw - w - EDGE)) + "px", top: Math.round(side === "above" ? r.top - h - GAP : below) + "px" };
  }

  function place(doc, b, el) {
    b.style.left = "0px";
    b.style.top = "0px";
    b.style.display = "block";
    var p = spot(doc, b, el);
    b.style.left = p.left;
    b.style.top = p.top;
  }

  // Seerr's box stays put next to its trigger; popper's cursor-following inline transform loses to the !important rule
  function pin(doc, h) {
    if (!h || !h.isConnected || !h.__hgTipFor || !h.__hgTipFor.isConnected) return;
    h.setAttribute("data-hg-tip-pin", "");
    var p = spot(doc, h, h.__hgTipFor);
    h.style.setProperty("--hg-tip-x", p.left);
    h.style.setProperty("--hg-tip-y", p.top);
  }

  // Seerr's Tooltip portal: a direct child of body with its classes, visible (phones hide the download popover)
  function isSeerr(doc, n) {
    if (!n || n.nodeType !== 1 || n.id === ID || n.parentNode !== doc.body) return false;
    var c = typeof n.className === "string" ? n.className : "";
    if (!/\bz-50\b/.test(c) || !/\babsolute\b/.test(c) || !/\bbg-gray-800\b/.test(c)) return false;
    var view = doc.defaultView;
    return !view || view.getComputedStyle(n).display !== "none";
  }

  function openSeerr(doc) {
    var kids = doc.body ? doc.body.children : [];
    for (var i = kids.length - 1; i >= 0; i--) if (isSeerr(doc, kids[i])) return kids[i];
    return null;
  }

  // Seerr's Tooltip portal has no link to its trigger: the badge, button or link around what the pointer or focus last reached
  function trigger(doc, t) {
    if (!t || t.nodeType !== 1 || !t.isConnected) return null;
    for (var n = t; n && n !== doc.body; n = n.parentElement) if (isSeerr(doc, n) || n.id === ID) return null;
    return t.closest('a[href],button,[role="button"],input,select,textarea,label,[tabindex]') || t.closest(".rounded-full,.inline-flex") || t;
  }

  function watch(doc, h) {
    // React re-renders Seerr's box (text content, new rows): our block goes back on top, once; the new size is placed again
    if (h.__hgTipWatch || !doc.defaultView || !doc.defaultView.MutationObserver) return;
    h.__hgTipWatch = new doc.defaultView.MutationObserver(function () {
      if (h.__hgTipBlock && h.firstChild !== h.__hgTipBlock) h.insertBefore(h.__hgTipBlock, h.firstChild);
      pin(doc, h);
    });
    h.__hgTipWatch.observe(h, { childList: true, subtree: true, characterData: true });
  }

  function adopt(doc, h, el) {
    style(doc);
    host = h;
    h.__hgTipFor = el;
    watch(doc, h);
    pin(doc, h);
  }

  function merge(doc, h, el) {
    var block = h.__hgTipBlock;
    if (!block) {
      block = doc.createElement("div");
      block.setAttribute("data-hg-tip-merged", "");
      h.__hgTipBlock = block;
    }
    style(doc);
    render(doc, block, el.__hgTip);
    block.className = h.querySelector(".p-4") ? "hg-tip-roomy" : "";
    if (h.firstChild !== block) h.insertBefore(block, h.firstChild);
    adopt(doc, h, el);
    var b = box(doc);
    if (b) b.style.display = "none";
    if (el.getAttribute("aria-describedby") === ID) el.removeAttribute("aria-describedby");
  }

  function hide(doc) {
    doc = doc || document;
    var b = box(doc);
    if (b) b.style.display = "none";
    if (cur && cur.getAttribute("aria-describedby") === ID) cur.removeAttribute("aria-describedby");
    cur = null;
  }

  function show(doc, el) {
    if (!el.__hgTip || !text(el.__hgTip)) { if (cur === el) hide(doc); return false; }
    var b = box(doc, true);
    if (cur && cur !== el && cur.getAttribute("aria-describedby") === ID) cur.removeAttribute("aria-describedby");
    cur = el;
    var h = openSeerr(doc);
    if (h) { merge(doc, h, el); return true; }
    host = null;
    render(doc, b, el.__hgTip);
    if (!el.hasAttribute("aria-describedby")) el.setAttribute("aria-describedby", ID);
    place(doc, b, el);
    return true;
  }

  function shownFor(el) {
    if (cur !== el) return false;
    if (host && host.isConnected) return true;
    var b = box(el.ownerDocument);
    return !!b && b.style.display !== "none";
  }

  function global(doc) {
    if (doc.__hgTipGlobal) return;
    doc.__hgTipGlobal = true;
    var win = doc.defaultView || window;
    doc.addEventListener("click", function (e) { var b = box(doc); if (!b || !b.contains(e.target)) hide(doc); });
    doc.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(doc); });
    win.addEventListener("scroll", function () { pin(doc, host); hide(doc); }, true);
    win.addEventListener("resize", function () { pin(doc, host); hide(doc); });
    doc.addEventListener("pointerover", function (e) { var t = trigger(doc, e.target); if (t) last = t; }, true);
    doc.addEventListener("focusin", function (e) { var t = trigger(doc, e.target); if (t) last = t; }, true);
    if (!win.MutationObserver || !doc.body) return;
    // runs before the next paint: Seerr's box that opens for the hovered element takes our text, no second box flashes
    new win.MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var r = records[i], n;
        for (n = 0; n < r.addedNodes.length; n++) {
          var add = r.addedNodes[n];
          if (!isSeerr(doc, add)) continue;
          if (cur) merge(doc, add, cur);
          else if (last && last.isConnected) adopt(doc, add, last);
        }
        for (n = 0; n < r.removedNodes.length; n++) {
          var gone = r.removedNodes[n];
          if (gone.__hgTipWatch) { gone.__hgTipWatch.disconnect(); gone.__hgTipWatch = null; }
          if (gone === host) { host = null; if (cur && cur.isConnected) show(doc, cur); }
        }
      }
    }).observe(doc.body, { childList: true });
  }

  function wire(doc, el) {
    if (el.__hgTipWired) return;
    el.__hgTipWired = true;
    el.addEventListener("pointerdown", function (ev) { pointer = ev.pointerType; el.__hgTipPt = ev.pointerType; });
    // only a moving mouse opens a hint: an element sliding under a resting cursor (page load, re-render) waits for the next move
    el.addEventListener("pointerenter", function (ev) {
      if (ev.pointerType !== "mouse") return;
      if (mouse && (mouse.x !== ev.clientX || mouse.y !== ev.clientY)) { el.__hgTipWait = null; show(doc, el); }
      else el.__hgTipWait = { x: ev.clientX, y: ev.clientY };
    });
    el.addEventListener("pointermove", function (ev) {
      var at = el.__hgTipWait;
      if (ev.pointerType !== "mouse" || !at || (at.x === ev.clientX && at.y === ev.clientY)) return;
      el.__hgTipWait = null;
      show(doc, el);
    });
    el.addEventListener("pointerleave", function (ev) { if (ev.pointerType !== "mouse") return; el.__hgTipWait = null; if (cur === el) hide(doc); });
    el.addEventListener("focus", function () { if (el.__hgTipPt !== "touch") show(doc, el); });
    el.addEventListener("blur", function () { if (cur === el && !host) hide(doc); });
    el.addEventListener("click", function (ev) {
      var touch = el.__hgTipPt === "touch";
      el.__hgTipPt = null;
      if (!touch || !el.__hgTipTap || !el.__hgTip) return;
      if (shownFor(el)) { hide(doc); return; }
      if (!show(doc, el)) return;
      ev.preventDefault();
      ev.stopPropagation();
    });
  }

  // content: see the top; opts.tap false = touch taps go straight through (links whose hint only names them)
  function set(el, content, opts) {
    if (!el || typeof document === "undefined") return;
    var doc = el.ownerDocument || document;
    el.__hgTip = content || null;
    el.__hgTipTap = !(opts && opts.tap === false);
    var flat = typeof content === "function" || !content ? "" : text(content);
    if (el.__hgTipAria && (!flat || el.getAttribute("aria-label") !== flat)) { el.removeAttribute("aria-label"); el.__hgTipAria = false; }
    if (flat && !el.hasAttribute("aria-label") && !el.textContent.trim()) { el.setAttribute("aria-label", flat); el.__hgTipAria = true; }
    if (!content) { if (cur === el) hide(doc); return; }
    global(doc);
    wire(doc, el);
    if (cur === el) show(doc, el);
  }

  var api = { set: set, get: function (el) { return el ? el.__hgTip || null : null; }, text: text, headline: headline, show: function (el) { return show(el.ownerDocument, el); }, hide: function () { hide(document); }, render: render };
  if (typeof window === "undefined") return;
  window.hgTip = api;
  // where the mouse last really moved (capture: runs before the element listeners of the same event)
  document.addEventListener("pointermove", function (ev) { if (ev.pointerType === "mouse") mouse = { x: ev.clientX, y: ev.clientY }; }, true);
  if (document.body) global(document);
  else document.addEventListener("DOMContentLoaded", function () { global(document); });
})();
