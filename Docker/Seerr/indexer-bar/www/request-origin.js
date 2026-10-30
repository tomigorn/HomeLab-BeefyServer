// Request origin (README "Request origin"): a "via Jellyfin / Moonfin / TV" pill in the Requested line of every Requests row made through Jellyfin's Popular buttons, and a "Requested via" row on the title page; the real person's name where Seerr shows the service user. Rows come from request-sizes.js (hgRequestSizes.rows), data from /_download-now/origins. Missing data = Seerr unchanged.
(function () {
  "use strict";
  var DATA_URL = "/_download-now/origins";
  var REQUEST_PAGE = /\/requests\/?$/;
  var TITLE_PAGE = /^\/(movie|tv)\/(\d+)\/?$/;
  var REQUESTED = /^(Requested|Angefragt)$/;
  var STATUS = /^(Status)$/;
  var TTL = 60 * 1000;
  var TEXT = {
    en: {
      pill: { jellyfin: "via Jellyfin", moonfin: "via Moonfin", tv: "via TV" },
      name: { jellyfin: "Jellyfin", moonfin: "Moonfin", tv: "TV" },
      hint: { jellyfin: "Requested with 🌙/⚡ on Jellyfin's Popular page", moonfin: "Requested in the Moonfin app", tv: "Requested on the TV by playing a Popular placeholder" },
      service: "Seerr lists it under the service user Jellyfin popular; requested by {n}",
      fact: "Requested via",
    },
    de: {
      pill: { jellyfin: "über Jellyfin", moonfin: "über Moonfin", tv: "über TV" },
      name: { jellyfin: "Jellyfin", moonfin: "Moonfin", tv: "TV" },
      hint: { jellyfin: "Mit 🌙/⚡ auf Jellyfins Beliebt-Seite angefragt", moonfin: "In der Moonfin-App angefragt", tv: "Am TV angefragt, durch Abspielen eines Beliebt-Platzhalters" },
      service: "Seerr führt sie unter dem Dienstkonto Jellyfin popular; angefragt von {n}",
      fact: "Angefragt über",
    },
  };
  var state = { data: null, at: 0, loading: null, titles: {}, titleLoading: {} };

  function lang(doc) {
    try { if (window.hgNightLabel && window.hgNightLabel.lang) return window.hgNightLabel.lang() === "de" ? "de" : "en"; } catch (e) { /* own probe */ }
    var tv = doc.querySelector('a[href="/discover/tv"]');
    return tv && /\bSerien\b/.test(tv.textContent) ? "de" : "en";
  }

  function getJSON(url) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      x.__hgSkip = true;
      x.open("GET", url);
      x.withCredentials = true;
      x.onload = function () {
        try { x.status === 200 ? resolve(JSON.parse(x.responseText)) : reject(new Error(String(x.status))); } catch (e) { reject(e); }
      };
      x.onerror = function () { reject(new Error("network")); };
      x.send();
    });
  }

  function load(force) {
    if (state.loading || (!force && state.at && Date.now() - state.at < TTL)) return state.loading;
    state.loading = getJSON(DATA_URL).then(function (d) {
      var r = d && d.requests;
      state.data = r && typeof r === "object" && !Array.isArray(r) ? r : null;
    }, function () { state.data = null; }).then(function () {
      state.at = Date.now();
      state.loading = null;
      run();
    });
    return state.loading;
  }

  function entry(id) {
    var e = state.data && id != null ? state.data[String(id)] : null;
    return e && typeof e === "object" && TEXT.en.pill[e.source] ? e : null;
  }

  function hintOf(e, l) {
    var t = TEXT[l].hint[e.source];
    return e.service && e.name ? t + " · " + TEXT[l].service.replace("{n}", e.name) : t;
  }

  function hint(el, text) {
    if (el.__hgOriginHint === text) return;
    el.__hgOriginHint = text;
    if (window.hgNightLabel && window.hgNightLabel.hint) window.hgNightLabel.hint(el, text);
    else el.setAttribute("title", text);
  }

  function pillText(e, l) {
    return TEXT[l].pill[e.source] + (e.service && e.name ? " · " + e.name : "");
  }

  function requestedField(row) {
    var names = row.querySelectorAll(".card-field > .card-field-name");
    for (var i = 0; i < names.length; i++) if (REQUESTED.test(names[i].textContent.trim())) return names[i].parentElement;
    return null;
  }

  function pill(doc, field, e, l) {
    var p = field.querySelector(":scope > [data-hg-origin]");
    if (!e) { if (p) p.parentNode.removeChild(p); return; }
    if (!p) {
      p = doc.createElement("span");
      p.className = "hg-origin";
      p.setAttribute("data-hg-origin", "");
      p.setAttribute("tabindex", "0");
    }
    var text = pillText(e, l);
    if (p.textContent !== text) p.textContent = text;
    if (p.getAttribute("data-hg-origin") !== e.source) p.setAttribute("data-hg-origin", e.source);
    if (p.parentElement !== field || p.nextElementSibling) field.appendChild(p);
    hint(p, hintOf(e, l));
  }

  function list(doc) {
    var sizes = window.hgRequestSizes;
    if (!sizes || !sizes.rows || !state.data) return;
    var l = lang(doc);
    sizes.rows(doc).forEach(function (r) {
      var field = requestedField(r.row);
      if (field) pill(doc, field, entry(r.id), l);
    });
  }

  function loadTitle(key) {
    if (state.titleLoading[key] || state.titles[key]) return;
    state.titleLoading[key] = getJSON("/api/v1/" + key).then(function (d) {
      var reqs = d && d.mediaInfo && Array.isArray(d.mediaInfo.requests) ? d.mediaInfo.requests : [];
      state.titles[key] = reqs.map(function (r) { return r && r.id; }).filter(function (id) { return id != null; }).sort(function (a, b) { return a - b; });
    }, function () { state.titles[key] = []; }).then(function () {
      state.titleLoading[key] = null;
      run();
    });
  }

  function title(doc) {
    var m = TITLE_PAGE.exec(window.location.pathname), panel = m && doc.querySelector(".media-facts");
    var row = panel ? panel.querySelector(":scope > [data-hg-origin-fact]") : doc.querySelector("[data-hg-origin-fact]");
    if (!panel || !state.data) { if (row) row.parentNode.removeChild(row); return; }
    var key = m[1] + "/" + m[2];
    if (!state.titles[key]) { loadTitle(key); if (row) row.parentNode.removeChild(row); return; }
    var l = lang(doc), seen = {}, parts = [], hints = [];
    state.titles[key].forEach(function (id) {
      var e = entry(id);
      if (!e) return;
      var text = TEXT[l].name[e.source] + (e.name ? " (" + e.name + ")" : "");
      if (seen[text]) return;
      seen[text] = true;
      parts.push(text);
      hints.push(hintOf(e, l));
    });
    var after = panel.querySelector(":scope > [data-hg-size-fact]"), facts = panel.querySelectorAll(":scope > .media-fact:not([data-hg-origin-fact])");
    for (var i = 0; !after && i < facts.length; i++) if (facts[i].firstElementChild && STATUS.test(facts[i].firstElementChild.textContent.trim())) after = facts[i];
    if (!parts.length || !after) { if (row) row.parentNode.removeChild(row); return; }
    if (!row) {
      row = doc.createElement("div");
      row.className = "media-fact";
      row.setAttribute("data-hg-origin-fact", "");
      var name = doc.createElement("span"), value = doc.createElement("span");
      value.className = "media-fact-value";
      row.appendChild(name);
      row.appendChild(value);
    }
    var text = parts.join(", ");
    if (row.firstChild.textContent !== TEXT[l].fact) row.firstChild.textContent = TEXT[l].fact;
    if (row.lastChild.textContent !== text) row.lastChild.textContent = text;
    if (after.nextElementSibling !== row) after.after(row);
    hint(row, hints.join("\n"));
  }

  function style(doc) {
    if (doc.getElementById("hg-origin-style")) return;
    var s = doc.createElement("style");
    s.id = "hg-origin-style";
    s.textContent = ".hg-origin{display:inline-flex;flex-shrink:0;align-items:center;margin-left:.375rem;padding:0 .5rem;border-radius:9999px;font-size:.75rem;line-height:1.25rem;white-space:nowrap;cursor:default;background:rgba(79,70,229,.25);color:#c7d2fe;border:1px solid rgba(129,140,248,.5)}@media (max-width:1279px){.card-field:has(>[data-hg-origin]){flex-wrap:wrap;row-gap:.25rem}.card-field:has(>[data-hg-origin])>span.truncate{flex-shrink:0;max-width:calc(100% - 6rem)}}.hg-origin[data-hg-origin=tv]{background:rgba(13,148,136,.25);color:#99f6e4;border-color:rgba(45,212,191,.5)}.hg-origin[data-hg-origin=moonfin]{background:rgba(147,51,234,.25);color:#e9d5ff;border-color:rgba(192,132,252,.5)}";
    (doc.head || doc.documentElement).appendChild(s);
  }

  function run() {
    if (typeof document === "undefined") return;
    var path = window.location.pathname;
    if (!REQUEST_PAGE.test(path) && !TITLE_PAGE.test(path)) return;
    try {
      load(false);
      style(document);
      list(document);
      title(document);
    } catch (e) { /* never break Seerr */ }
  }

  var api = { run: run, state: state, pillText: pillText, load: load };
  if (typeof window === "undefined") return;
  window.hgRequestOrigin = api;
  try {
    window.addEventListener("hg-request-rows", run);
    if (!window.MutationObserver || !document.body) return;
    run();
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var added = records[i].addedNodes;
        for (var n = 0; n < added.length; n++) {
          var a = added[n];
          if (a.nodeType === 1 && !a.hasAttribute("data-hg-origin") && !a.hasAttribute("data-hg-origin-fact")) { run(); return; }
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  } catch (e) { /* never break Seerr */ }
})();
