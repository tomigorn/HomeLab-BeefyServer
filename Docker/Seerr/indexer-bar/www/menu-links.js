// Extra entries in Seerr's sidebar and mobile menu (README "Menu links"), placed after "Series". If Seerr changes its menu, the entries just do not appear.
(function () {
  "use strict";
  var LINKS = [{ id: "audiobooks", label: "Audiobooks", de: "Hörbücher", href: "https://books.holy-grail.ch/" }];
  var ANCHOR = 'a[href="/discover/tv"]';
  var ACTIVE = ["bg-gradient-to-br", "from-indigo-600", "to-purple-600", "hover:from-indigo-500", "hover:to-purple-500", "text-indigo-500"];
  var BOOK = "M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25";

  function icon(doc, cls) {
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
    path.setAttribute("d", BOOK);
    svg.appendChild(path);
    return svg;
  }

  function entry(doc, tv, link) {
    var a = tv.cloneNode(true);
    a.setAttribute("href", link.href);
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
    old.parentNode.replaceChild(icon(doc, cls), old);
    var tv = doc.querySelector('a[href="/discover/tv"]:not([data-hg-link])'), label = tv && /\bSerien\b/.test(tv.textContent) ? link.de : link.label;
    if (text) text.nodeValue = label;
    else a.setAttribute("aria-label", label);
    a.setAttribute("title", label);
    a.querySelectorAll("div").forEach(function (d) { d.remove(); });
    return a;
  }

  // Phone bottom bar (icon above label): Seerr shows up to 5 links there, more go into its "more" list. The entry joins the bar only while it has room and no "more" button; otherwise the "more" list gets it.
  function skip(a) {
    if (!a.classList.contains("flex-col")) return false;
    var bar = a.parentElement;
    if (bar.querySelector("button")) return true;
    var own = 0;
    for (var i = 0; i < bar.children.length; i++) if (bar.children[i].tagName === "A" && !bar.children[i].hasAttribute("data-hg-link")) own++;
    return own + LINKS.length > 5;
  }

  function attach(doc) {
    var tvs = doc.querySelectorAll(ANCHOR);
    for (var i = 0; i < tvs.length; i++) {
      var tv = tvs[i];
      // main holds page content: Discover's "Popular Series" slider title links to /discover/tv as well
      if (!tv.parentElement || tv.closest("main") || skip(tv)) continue;
      var after = tv;
      for (var j = 0; j < LINKS.length; j++) {
        var next = after.nextElementSibling;
        if (next && next.getAttribute("data-hg-link") === LINKS[j].id) { after = next; continue; }
        var a = entry(doc, tv, LINKS[j]);
        if (!a) break;
        tv.parentElement.insertBefore(a, after.nextSibling);
        after = a;
      }
    }
  }

  if (typeof window === "undefined") return;
  window.hgMenuLinks = { attach: attach, links: LINKS };
  try {
    if (!window.MutationObserver || !document.body) return;
    attach(document);
    new MutationObserver(function () {
      try { attach(document); } catch (e) { /* never break Seerr */ }
    }).observe(document.body, { childList: true, subtree: true });
  } catch (e) { /* never break Seerr */ }
})();
