// Extra entries in Seerr's sidebar and mobile menu (README "Menu links"), placed after "Series"; Manual interaction carries the count of open problems. If Seerr changes its menu, the entries just do not appear.
(function () {
  "use strict";
  var BOOK = "M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25";
  var WRENCH = "M11.42 15.17 17.25 21A2.652 2.652 0 0 0 21 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 1 1-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 0 0 4.486-6.336l-3.276 3.277a3.004 3.004 0 0 1-2.25-2.25l3.276-3.276a4.5 4.5 0 0 0-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437 1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008Z";
  // bar: the phone bottom bar takes the entry while it has at most this many links with it
  var LINKS = [
    { id: "audiobooks", label: "Audiobooks", de: "Hörbücher", href: "https://books.holy-grail.ch/", icon: BOOK, bar: 5 },
    { id: "manual", label: "Manual interaction", de: "Manuell beheben", href: "/manual", route: true, icon: WRENCH, bar: 6, count: true }
  ];
  var COUNT_URL = "/_download-now/manual/count";
  var count = null, asked = 0;
  var ANCHOR = 'a[href="/discover/tv"]';
  var ACTIVE = ["bg-gradient-to-br", "from-indigo-600", "to-purple-600", "hover:from-indigo-500", "hover:to-purple-500", "text-indigo-500"];

  function icon(doc, cls, d) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = doc.createElementNS(ns, "svg");
    svg.setAttribute("xmlns", ns);
    svg.setAttribute("fill", "none");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("stroke-width", "1.5");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("aria-hidden", "true");
    if (cls) svg.setAttribute("class", cls);
    var path = doc.createElementNS(ns, "path");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("d", d);
    svg.appendChild(path);
    return svg;
  }

  function entry(doc, tv, link) {
    var a = tv.cloneNode(true);
    a.setAttribute("data-hg-link", link.id);
    a.removeAttribute("data-testid");
    a.removeAttribute("role");
    var wasActive = a.classList.contains("bg-gradient-to-br");
    ACTIVE.forEach(function (c) { a.classList.remove(c); });
    if (wasActive) a.classList.add("hover:bg-gray-700", "focus:bg-gray-700");
    var old = a.querySelector("svg");
    var cls = old ? old.getAttribute("class") : "mr-3 h-6 w-6";
    var text = null;
    var walker = doc.createTreeWalker(a, 4);
    while (walker.nextNode()) if (walker.currentNode.nodeValue.trim()) { text = walker.currentNode; break; }
    if (!old) return null;
    old.parentNode.replaceChild(icon(doc, cls, link.icon), old);
    var any = doc.querySelector('a[href="/discover/tv"]:not([data-hg-link])'), de = !!any && /\bSerien\b/.test(any.textContent), label = de ? link.de : link.label;
    a.setAttribute("href", link.href);
    if (link.route) a.addEventListener("click", function (ev) { inApp(ev, link.href); });
    a.setAttribute("data-hg-label", label);
    if (text) text.nodeValue = label;
    else a.setAttribute("aria-label", label);
    a.setAttribute("title", label);
    a.querySelectorAll("div").forEach(function (d) { d.remove(); });
    return a;
  }

  // Phone bottom bar (icon above label): Seerr shows up to 5 links there, more go into its "more" list. With a "more" button the entries go into that list; without one each joins the bar while the bar stays within the entry's `bar` size.
  function attach(doc) {
    var tvs = doc.querySelectorAll(ANCHOR);
    for (var i = 0; i < tvs.length; i++) {
      var tv = tvs[i], bar = tv.parentElement;
      // main holds page content: Discover's "Popular Series" slider title links to /discover/tv as well
      if (!bar || tv.closest("main")) continue;
      var inBar = tv.classList.contains("flex-col");
      if (inBar && bar.querySelector("button")) continue;
      var own = 0, placed = 0, after = tv;
      for (var k = 0; k < bar.children.length; k++) if (bar.children[k].tagName === "A" && !bar.children[k].hasAttribute("data-hg-link")) own++;
      for (var j = 0; j < LINKS.length; j++) {
        var next = after.nextElementSibling;
        if (next && next.getAttribute("data-hg-link") === LINKS[j].id) { after = next; placed++; continue; }
        if (inBar && own + placed + 1 > LINKS[j].bar) continue;
        var a = entry(doc, tv, LINKS[j]);
        if (!a) break;
        bar.insertBefore(a, after.nextSibling);
        after = a;
        placed++;
      }
    }
    paint(doc);
    mark(doc);
  }

  // a page of ours inside Seerr (/manual): Seerr's router shows its 404 page under our path (as a direct load does), so the layout stays; Next reloads the whole page for a path it has no page for
  function inApp(ev, href) {
    var w = typeof window !== "undefined" ? window : null, router = w && w.next && w.next.router;
    if (!router || typeof router.push !== "function" || ev.defaultPrevented || ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    try { router.push("/404", href); } catch (e) { w.location.href = href; }
  }

  // our route entries look active on their page, like Seerr's own entries
  function mark(doc) {
    var path = (doc.defaultView && doc.defaultView.location.pathname || "").replace(/\/+$/, "");
    for (var j = 0; j < LINKS.length; j++) {
      if (!LINKS[j].route) continue;
      var on = path === LINKS[j].href, all = doc.querySelectorAll('[data-hg-link="' + LINKS[j].id + '"]');
      for (var i = 0; i < all.length; i++) {
        var a = all[i], side = a.classList.contains("group");
        if (side) {
          if (on && !a.classList.contains("bg-gradient-to-br")) { a.classList.remove("hover:bg-gray-700", "focus:bg-gray-700"); a.classList.add("bg-gradient-to-br", "from-indigo-600", "to-purple-600", "hover:from-indigo-500", "hover:to-purple-500"); }
          if (!on && a.classList.contains("bg-gradient-to-br")) { ACTIVE.forEach(function (c) { a.classList.remove(c); }); a.classList.add("hover:bg-gray-700", "focus:bg-gray-700"); }
        } else if (on !== a.classList.contains("text-indigo-500")) {
          a.classList.toggle("text-indigo-500", on);
        }
      }
    }
  }

  // open problems on the Manual entry: a small amber count, nothing at 0 or without an answer
  function paint(doc) {
    var all = doc.querySelectorAll('[data-hg-link="manual"]');
    for (var i = 0; i < all.length; i++) {
      var a = all[i], b = a.querySelector("[data-hg-count]"), label = a.getAttribute("data-hg-label") || "";
      var want = count > 0 ? String(count) : null, title = want ? label + " (" + want + ")" : label;
      if (a.getAttribute("title") !== title) a.setAttribute("title", title);
      if (a.hasAttribute("aria-label") && a.getAttribute("aria-label") !== title) a.setAttribute("aria-label", title);
      if (!want) { if (b) b.remove(); continue; }
      if (b) { if (b.textContent !== want) b.textContent = want; continue; }
      b = doc.createElement("span");
      b.setAttribute("data-hg-count", "");
      b.setAttribute("aria-hidden", "true");
      b.textContent = want;
      var st = b.style;
      st.background = "#d97706"; st.color = "#fff"; st.borderRadius = "9999px"; st.fontSize = "0.75rem"; st.fontWeight = "600";
      st.lineHeight = "1.25rem"; st.minWidth = "1.25rem"; st.padding = "0 0.375rem"; st.textAlign = "center";
      if (a.classList.contains("flex-col")) { st.position = "absolute"; st.top = "-0.25rem"; st.right = "-0.5rem"; }
      else st.marginLeft = "auto";
      a.appendChild(b);
    }
  }

  function ask(win) {
    try {
      win.fetch(COUNT_URL, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (r) {
        return r.ok ? r.json() : null;
      }).then(function (j) {
        count = j && typeof j.open === "number" ? j.open : null;
        paint(win.document);
      }, function () { /* no badge */ });
    } catch (e) { /* no badge */ }
  }

  if (typeof window === "undefined") return;
  window.hgMenuLinks = { attach: attach, links: LINKS };
  try {
    if (!window.MutationObserver || !document.body) return;
    attach(document);
    var fresh = function () {
      if (!window.fetch || Date.now() - asked < 300000) return;
      asked = Date.now();
      ask(window);
    };
    fresh();
    new MutationObserver(function () {
      try { attach(document); fresh(); } catch (e) { /* never break Seerr */ }
    }).observe(document.body, { childList: true, subtree: true });
  } catch (e) { /* never break Seerr */ }
})();
