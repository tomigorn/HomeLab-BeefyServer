// "Waiting for the night" label (README "Night label"): a download paused by the "Can wait" night queue shows the night label instead of Seerr's "Processing" at 0 %. The night list comes from night-downloads.py; missing, stale or unexpected = Seerr unchanged.
(function () {
  "use strict";
  var WAITING_URL = "/_indexer-bar/night-waiting.json";
  var MAX_AGE = 10 * 60 * 1000;
  var TTL = 60 * 1000;
  var TEXT = {
    en: { label: "Waiting for the night 🌙", tip: "Starts automatically at 22:05 or on Sunday; untick 'Can wait' next time for an immediate download" },
    de: { label: "Wartet auf die Nacht 🌙", tip: "Startet automatisch um 22:05 oder am Sonntag; nächstes Mal „Can wait“ abwählen für einen sofortigen Download" },
  };
  var MEDIA_LINK = /^\/(movie|tv)\/(\d+)$/;
  var EPISODE = /^(.*): (?:Season|Staffel) (\d+) (?:Episode|Folge) (\d+)$/;
  var GERMAN = /^(Verarbeiten|Verfügbar|Teilweise verfügbar|Angefragt|Gelöscht)$/;

  var state = { waiting: null, waitingAt: 0, waitingLoading: null, media: {}, posters: {}, loading: {} };

  function waitingSet(data, now) {
    if (!data || !Array.isArray(data.waiting)) return null;
    var t = Date.parse(data.updated);
    if (!(now - t <= MAX_AGE)) return null;
    var s = {};
    for (var i = 0; i < data.waiting.length; i++) s[String(data.waiting[i]).toLowerCase()] = true;
    return s;
  }

  function isWaiting(it, set) {
    return !!it && String(it.status).toLowerCase() === "paused" && !!it.downloadId && set[String(it.downloadId).toLowerCase()] === true;
  }

  function allWaiting(items, set) {
    if (!set || !items || !items.length) return false;
    for (var i = 0; i < items.length; i++) if (!isWaiting(items[i], set)) return false;
    return true;
  }

  // Collects what Seerr's API returned: download lists per title, poster -> title, title names.
  function remember(json) {
    var now = Date.now();
    (function walk(o, depth) {
      if (!o || typeof o !== "object" || depth > 6) return;
      if (Array.isArray(o)) { for (var i = 0; i < o.length && i < 500; i++) walk(o[i], depth + 1); return; }
      var info = o.mediaInfo && typeof o.mediaInfo === "object" ? o.mediaInfo : (Array.isArray(o.downloadStatus) ? o : null);
      if (info && info.tmdbId && (info.mediaType === "movie" || info.mediaType === "tv") && Array.isArray(info.downloadStatus)) {
        var key = info.mediaType + "/" + info.tmdbId, prev = state.media[key] || {};
        var names = info === o ? prev.names || [] : [o.title, o.name, o.originalTitle, o.originalName].filter(Boolean);
        state.media[key] = { items: info.downloadStatus, items4k: Array.isArray(info.downloadStatus4k) ? info.downloadStatus4k : [], names: names.length ? names : prev.names || [], at: now };
        if (info !== o && o.posterPath) state.posters[o.posterPath] = key;
      }
      for (var k in o) if (k !== "downloadStatus" && k !== "downloadStatus4k" && Object.prototype.hasOwnProperty.call(o, k)) walk(o[k], depth + 1);
    })(json, 0);
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

  function loadWaiting(force) {
    if (!force && state.waitingAt && Date.now() - state.waitingAt < TTL) return Promise.resolve();
    if (state.waitingLoading) return state.waitingLoading;
    state.waitingLoading = getJSON(WAITING_URL).then(function (d) { state.waiting = d; }, function () { state.waiting = null; }).then(function () {
      state.waitingAt = Date.now();
      state.waitingLoading = null;
    });
    return state.waitingLoading;
  }

  function loadMedia(key) {
    var m = state.media[key];
    if ((m && m.names.length && Date.now() - m.at < TTL) || state.loading[key]) return;
    state.loading[key] = getJSON("/api/v1/" + key).then(remember, function () { /* Seerr unchanged */ }).then(function () {
      delete state.loading[key];
      schedule();
    });
  }

  function pathKey(loc) {
    var m = /^\/(movie|tv)\/(\d+)\/?$/.exec(loc.pathname);
    return m ? m[1] + "/" + m[2] : null;
  }

  function linkKey(a) {
    var m = MEDIA_LINK.exec(a.getAttribute("href") || "");
    return m ? m[1] + "/" + m[2] : null;
  }

  // the title a status badge belongs to: the title page header, else the one title its card links to
  function badgeKey(el, loc) {
    if (el.closest(".media-status")) return pathKey(loc);
    for (var a = el.parentElement, n = 0; a && n < 10; a = a.parentElement, n++) {
      var links = a.querySelectorAll("a[href]"), keys = {}, count = 0, last = null;
      for (var i = 0; i < links.length; i++) {
        var k = linkKey(links[i]);
        if (k && !keys[k]) { keys[k] = true; count++; last = k; }
      }
      if (count === 1) return last;
      if (count > 1 || a.getAttribute("data-testid") === "request-card") return null;
    }
    return null;
  }

  function lang(doc, hint) {
    if (hint) return GERMAN.test(hint) ? "de" : "en";
    var tv = doc.querySelector('a[href="/discover/tv"]');
    if (tv && /\bSerien\b/.test(tv.textContent)) return "de";
    if (tv && /\bSeries\b/.test(tv.textContent)) return "en";
    return /^de\b/i.test((doc.defaultView && doc.defaultView.navigator.language) || "") ? "de" : "en";
  }

  function hide(el) { if (el) el.setAttribute("data-hg-night-hide", ""); }

  function own(doc, tag, text, css) {
    var e = doc.createElement(tag);
    e.setAttribute("data-hg-night-own", "");
    e.textContent = text;
    if (css) e.style.cssText = css;
    return e;
  }

  function restore(root) {
    if (!root.hasAttribute("data-hg-night")) return;
    var owned = root.querySelectorAll("[data-hg-night-own]");
    for (var i = 0; i < owned.length; i++) owned[i].parentNode.removeChild(owned[i]);
    var hidden = root.querySelectorAll("[data-hg-night-hide]");
    for (var j = 0; j < hidden.length; j++) hidden[j].removeAttribute("data-hg-night-hide");
    var grey = root.querySelectorAll("[data-hg-night-grey]");
    for (var g = 0; g < grey.length; g++) grey[g].removeAttribute("data-hg-night-grey");
    if (root.getAttribute("data-hg-night") === "title") root.removeAttribute("title");
    root.removeAttribute("data-hg-night");
  }

  // StatusBadge while in progress: progress overlay + text row with "Processing" (4K prefixed)
  function statusBadges(doc, set, loc) {
    var rows = doc.querySelectorAll("div.relative.z-20");
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i], b = row.parentElement, span = row.querySelector("span");
      var overlay = b && b.querySelector(":scope > div.absolute.left-0.top-0.z-10");
      if (!overlay || !span) continue;
      var text = span.textContent.trim(), is4k = /^4K\s/.test(text);
      var key = badgeKey(b, loc), m = key && state.media[key];
      if (key && set) loadMedia(key);
      var on = !!m && allWaiting(is4k ? m.items4k : m.items, set);
      if (!on) { restore(b); continue; }
      if (b.hasAttribute("data-hg-night")) continue;
      var t = TEXT[lang(doc, text.replace(/^4K\s/, ""))];
      hide(overlay);
      for (var c = 0; c < row.children.length; c++) hide(row.children[c]);
      row.appendChild(own(doc, "span", (is4k ? "4K " : "") + t.label));
      if (!b.hasAttribute("title")) { b.setAttribute("title", t.tip); b.setAttribute("data-hg-night", "title"); } else b.setAttribute("data-hg-night", "");
    }
  }

  // TitleCard (Discover, lists): StatusBadgeMini spinner; the title comes from the poster Seerr's list response named
  function titleCards(doc, set) {
    var spinners = doc.querySelectorAll(".pointer-events-none.z-40 svg");
    for (var i = 0; i < spinners.length; i++) {
      var svg = spinners[i];
      if (!svg.querySelector("animateTransform")) continue;
      var card = svg.closest('[role="link"]'), holder = svg.parentElement;
      var img = card && card.querySelector("img");
      if (!img || !holder) continue;
      var src = "";
      try { src = decodeURIComponent((img.getAttribute("src") || "") + " " + (img.getAttribute("srcset") || "")); } catch (e) { src = img.getAttribute("src") || ""; }
      var key = null;
      for (var p in state.posters) if (src.indexOf(p) >= 0) { key = state.posters[p]; break; }
      var m = key && state.media[key];
      if (!(m && allWaiting(m.items, set))) { restore(holder); continue; }
      if (holder.hasAttribute("data-hg-night")) continue;
      hide(svg);
      holder.appendChild(own(doc, "span", "🌙", "display:flex;align-items:center;justify-content:center;font-size:.7rem;line-height:1;height:100%;"));
      holder.setAttribute("title", TEXT[lang(doc)].tip);
      holder.setAttribute("data-hg-night", "title");
    }
  }

  // the downloads a DownloadBlock shows: release name (admins), "Title: Season n Episode m" or the title
  function blockItems(name) {
    var keys = Object.keys(state.media), out = [], k, m, i, j, all;
    for (k = 0; k < keys.length; k++) {
      m = state.media[keys[k]];
      all = m.items.concat(m.items4k);
      for (i = 0; i < all.length; i++) if (all[i].title === name) out.push(all[i]);
    }
    if (out.length) return out;
    var ep = EPISODE.exec(name);
    for (k = 0; k < keys.length; k++) {
      m = state.media[keys[k]];
      if (ep && m.names.indexOf(ep[1]) >= 0) {
        all = m.items.concat(m.items4k);
        for (j = 0; j < all.length; j++) if (all[j].episode && all[j].episode.seasonNumber === +ep[2] && all[j].episode.episodeNumber === +ep[3]) out.push(all[j]);
      } else if (!ep && m.names.indexOf(name) >= 0) out = out.concat(m.items);
    }
    return out;
  }

  function downloadBlocks(doc, set, loc) {
    var bars = doc.querySelectorAll("div.relative.h-6.overflow-hidden.rounded-full.bg-gray-700");
    var hint = pathKey(loc);
    if (bars.length && hint && set) loadMedia(hint);
    for (var i = 0; i < bars.length; i++) {
      var bar = bars[i], block = bar.parentElement, nameEl = block && block.firstElementChild;
      var status = block && block.querySelector(".capitalize"), fill = bar.firstElementChild, pct = bar.querySelector("span");
      if (!nameEl || nameEl === bar || !status || !fill) continue;
      if (!allWaiting(blockItems(nameEl.textContent.trim()), set)) { restore(block); continue; }
      if (block.hasAttribute("data-hg-night")) continue;
      var t = TEXT[lang(doc)];
      hide(status);
      hide(pct);
      fill.setAttribute("data-hg-night-grey", "");
      var badge = own(doc, "span", t.label);
      badge.className = status.className.replace(/\bcapitalize\b/, "").replace(/bg-indigo-500\/80|border-indigo-500|!text-indigo-100/g, "") + " bg-gray-600 border border-gray-500 text-gray-100";
      badge.title = t.tip;
      status.parentNode.insertBefore(badge, status.nextSibling);
      block.setAttribute("data-hg-night", "");
    }
  }

  function style(doc) {
    if (doc.getElementById("hg-night-style")) return;
    var s = doc.createElement("style");
    s.id = "hg-night-style";
    s.textContent = "[data-hg-night-hide]{display:none !important}[data-hg-night-grey]{width:100% !important;background:#4b5563 !important}";
    (doc.head || doc.documentElement).appendChild(s);
  }

  function apply(doc, loc) {
    var set = waitingSet(state.waiting, Date.now());
    if (set && !Object.keys(set).length) set = null;
    style(doc);
    statusBadges(doc, set, loc);
    titleCards(doc, set);
    downloadBlocks(doc, set, loc);
  }

  var timer = null;
  function schedule() {
    if (timer || typeof window === "undefined") return;
    timer = setTimeout(function () {
      timer = null;
      loadWaiting(false).then(function () {
        try { apply(document, window.location); } catch (e) { /* never break Seerr */ }
      });
    }, 150);
  }

  function refresh() {
    loadWaiting(true).then(schedule);
  }

  function hookXHR(XHR) {
    if (!XHR || !XHR.prototype || XHR.prototype.__hgNight) return;
    var open = XHR.prototype.open, send = XHR.prototype.send;
    XHR.prototype.__hgNight = true;
    XHR.prototype.open = function (method, url) {
      this.__hgNightUrl = String(method).toUpperCase() === "GET" ? String(url) : null;
      return open.apply(this, arguments);
    };
    XHR.prototype.send = function () {
      var self = this;
      if (!this.__hgSkip && this.__hgNightUrl && /^(https?:\/\/[^/]+)?\/api\/v1\//.test(this.__hgNightUrl) && this.addEventListener) {
        this.addEventListener("load", function () {
          try {
            if (self.status !== 200) return;
            remember(self.responseType === "json" ? self.response : JSON.parse(self.responseText));
            schedule();
          } catch (e) { /* not JSON */ }
        });
      }
      return send.apply(this, arguments);
    };
  }

  var api = { waitingSet: waitingSet, isWaiting: isWaiting, allWaiting: allWaiting, remember: remember, badgeKey: badgeKey, blockItems: blockItems, apply: apply, refresh: refresh, state: state };
  if (typeof window === "undefined") return;
  window.hgNightLabel = api;
  try {
    hookXHR(window.XMLHttpRequest);
    if (!window.MutationObserver || !document.body) return;
    schedule();
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var t = records[i].target;
        if (!(t.closest && t.closest("[data-hg-night]"))) { schedule(); return; }
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
    setInterval(refresh, TTL);
  } catch (e) { /* never break Seerr */ }
})();
