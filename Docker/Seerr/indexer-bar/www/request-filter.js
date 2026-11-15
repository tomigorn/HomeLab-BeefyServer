// Request filter (README "Request filter"): "✅ Finished" and "⏳ Not finished yet" right after "All" in the Requests filter select, as extra options carrying Seerr's own filter values (available, processing), so Seerr's state, URL, pagination and our size sort keep working.
(function () {
  "use strict";
  var ENTRIES = [["available", "finished"], ["processing", "open"]];
  var TEXT = {
    en: { finished: "✅ Finished", open: "⏳ Not finished yet" },
    de: { finished: "✅ Fertig", open: "⏳ Noch nicht fertig" },
  };

  function lang(doc) {
    try { if (window.hgNightLabel && window.hgNightLabel.lang) return window.hgNightLabel.lang() === "de" ? "de" : "en"; } catch (e) { /* own probe */ }
    var tv = doc.querySelector('a[href="/discover/tv"]');
    return tv && /\bSerien\b/.test(tv.textContent) ? "de" : "en";
  }

  function seerrHas(sel, value) {
    for (var i = 0; i < sel.options.length; i++) if (sel.options[i].value === value && !sel.options[i].hasAttribute("data-hg-filter")) return true;
    return false;
  }

  function apply(doc) {
    var sel = doc.querySelector("select#filter");
    if (!sel || !seerrHas(sel, "processing") || !seerrHas(sel, "available")) return;
    var t = TEXT[lang(doc)], all = sel.querySelector('option[value="all"]'), after = all;
    ENTRIES.forEach(function (e) {
      var o = sel.querySelector('option[data-hg-filter="' + e[1] + '"]');
      if (!o) {
        o = doc.createElement("option");
        o.value = e[0];
        o.setAttribute("data-hg-filter", e[1]);
      }
      if (o.textContent !== t[e[1]]) o.textContent = t[e[1]];
      var want = after ? after.nextSibling : sel.firstChild;
      if (o !== want) sel.insertBefore(o, want);
      after = o;
    });
    // the select shows the first option with its value: ours, also when Seerr's own entry was picked
    var cur = sel.options[sel.selectedIndex];
    if (cur && !cur.hasAttribute("data-hg-filter")) {
      var mine = sel.querySelector('option[data-hg-filter][value="' + cur.value + '"]');
      if (mine) mine.selected = true;
    }
  }

  if (typeof window === "undefined" || !document.body) return;
  apply(document);
  var queued = false;
  new MutationObserver(function () {
    if (queued) return;
    queued = true;
    Promise.resolve().then(function () { queued = false; apply(document); });
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
})();
