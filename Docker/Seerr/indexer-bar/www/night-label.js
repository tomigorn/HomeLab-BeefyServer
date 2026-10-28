// "Waiting for the night" label (README "Night label"): a download paused by the "Can wait" night queue shows the night label instead of Seerr's "Processing" at 0 %; a "Can wait" request not downloading gets a 🌙, a title not released yet a 📅. Both lists come from night-downloads.py; missing, stale or unexpected = Seerr unchanged.
(function () {
  "use strict";
  var WAITING_URL = "/_indexer-bar/night-waiting.json";
  var REQUESTS_URL = "/_indexer-bar/request-state.json";
  var MAX_AGE = 10 * 60 * 1000;
  var TTL = 60 * 1000;
  var TEXT = {
    en: { label: "Waiting for the night 🌙", tip: "Starts automatically at 22:05 or on Sunday; untick 'Can wait' next time for an immediate download" },
    de: { label: "Wartet auf die Nacht 🌙", tip: "Startet automatisch um 22:05 oder am Sonntag; nächstes Mal „Can wait“ abwählen für einen sofortigen Download" },
  };
  var FLAGS = {
    en: { moon: "Waiting for the night — downloads at night (cheaper power)", compass: "No release found yet — searched again in tonight's scheduled search (04:17)", cal: "Not released yet", expected: "expected", moonShort: "Waiting for the night", compassNight: ", downloads at night when found", restNight: "the rest downloads at night", compassShort: "No release found yet — searched again tonight (04:17)", nextEp: "next episode", locale: "en-GB" },
    de: { moon: "Wartet auf die Nacht — lädt nachts herunter (günstigerer Strom)", compass: "Noch kein Release gefunden — die geplante Suche heute Nacht (04:17) sucht weiter", cal: "Noch nicht erschienen", expected: "erwartet", moonShort: "Wartet auf die Nacht", compassNight: ", lädt nachts, sobald gefunden", restNight: "der Rest lädt nachts herunter", compassShort: "Noch kein Release gefunden — die Suche heute Nacht (04:17) sucht weiter", nextEp: "nächste Folge", locale: "de-CH" },
  };
  var STATUS = {
    en: { spin: "Downloading now", avail: "Available in Jellyfin", part: "Partly available" },
    de: { spin: "Lädt gerade herunter", avail: "In Jellyfin verfügbar", part: "Teilweise verfügbar" },
  };
  var SPIN = "◌";
  var HALF = "◐";
  var PENDING = /^(4K )?(Pending|Ausstehend)$/;
  var MEDIA_LINK = /^\/(movie|tv)\/(\d+)$/;
  var EPISODE = /^(.*): (?:Season|Staffel) (\d+) (?:Episode|Folge) (\d+)$/;
  var GERMAN = /^(Verarbeiten|Verfügbar|Teilweise verfügbar|Angefragt|Gelöscht)$/;

  var RELEASED_FOR = 10 * 60 * 1000;
  var state = { waiting: null, requests: null, waitingAt: 0, waitingLoading: null, media: {}, posters: {}, loading: {}, released: {} };

  function waitingSet(data, now) {
    if (!data || !Array.isArray(data.waiting)) return null;
    var t = Date.parse(data.updated);
    if (!(now - t <= MAX_AGE)) return null;
    var s = {};
    for (var i = 0; i < data.waiting.length; i++) {
      var id = String(data.waiting[i]).toLowerCase();
      if (!(state.released[id] > now)) s[id] = true;
    }
    return s;
  }

  function requestSets(data, now) {
    if (!data || !Array.isArray(data.canWait) || !data.unreleased || typeof data.unreleased !== "object") return null;
    if (!(now - Date.parse(data.updated) <= MAX_AGE)) return null;
    var can = {};
    for (var i = 0; i < data.canWait.length; i++) can[String(data.canWait[i])] = true;
    return { canWait: can, unreleased: data.unreleased, noRelease: data.noRelease && typeof data.noRelease === "object" ? data.noRelease : {} };
  }

  function busy(m) {
    if (!m) return false;
    if (m.status === 5) return true;
    for (var i = 0; i < m.items.length; i++) if (String(m.items[i].status).toLowerCase() !== "paused") return true;
    return false;
  }

  // which icons a title gets: 🌙 Can wait (not downloading, not available), 📅 not released yet
  function flags(key, rs, m) {
    if (!rs || !key) return { moon: false, compass: false, cal: false, date: null };
    var has = Object.prototype.hasOwnProperty, done = !!(m && m.status === 5);
    var moon = rs.canWait[key] === true && !busy(m) && !done;
    var compass = has.call(rs.noRelease, key) && !busy(m) && !done;
    var cal = has.call(rs.unreleased, key) && !done;
    return { moon: moon, compass: compass, cal: cal, date: cal ? rs.unreleased[key] : null };
  }

  function dateText(iso, l, short) {
    var d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    if (!d) return "";
    try { return new Date(Date.UTC(+d[1], +d[2] - 1, +d[3])).toLocaleDateString(FLAGS[l].locale, short ? { day: "numeric", month: "short", timeZone: "UTC" } : { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }); } catch (e) { return iso; }
  }

  // [icon, hint] in display order 🌙🧭📅
  function flagList(f, l, part) {
    var out = [];
    if (f.moon) out.push(["🌙", FLAGS[l].moon]);
    if (f.compass) out.push(["🧭", FLAGS[l].compass]);
    if (f.cal) out.push(["📅", FLAGS[l].cal + (f.date ? " — " + (part ? FLAGS[l].nextEp + " " + dateText(f.date, l, true) : FLAGS[l].expected + " " + dateText(f.date, l)) : "")]);
    return out;
  }

  // one hint for a badge's icons together: 🌙 shortened when combined, 🧭 with the night when 🌙 is there
  function merged(list, l) {
    if (list.length === 1) return list[0][1];
    if (list[0][0] === HALF) {
      var rest = list.slice(1), only = rest.length === 1 && rest[0][0] === "🌙";
      return [list[0][1]].concat(rest.map(function (x) { return x[0] === "🌙" ? FLAGS[l].moonShort + (only ? " — " + FLAGS[l].restNight : "") : x[0] === "🧭" ? FLAGS[l].compassShort : x[1]; })).join(" · ");
    }
    var moon = list.some(function (x) { return x[0] === "🌙"; });
    return list.map(function (x) { return x[0] === "🌙" ? FLAGS[l].moonShort : x[0] === "🧭" && moon ? x[1] + FLAGS[l].compassNight : x[1]; }).join(" · ");
  }

  // touch has no hover: the first tap on a badge with icons shows its hint, a second tap goes through (link, card)
  function tapHint(doc, el, text) {
    el.__hgHint = text;
    if (el.__hgTap) return;
    el.__hgTap = true;
    el.addEventListener("click", function (ev) {
      if (!el.__hgHint) return;
      var tip = doc.getElementById("hg-flag-tip");
      if (tip && tip.__for === el && tip.style.display !== "none") { tip.style.display = "none"; return; }
      ev.preventDefault();
      ev.stopPropagation();
      if (!tip) {
        tip = doc.createElement("div");
        tip.id = "hg-flag-tip";
        tip.setAttribute("role", "tooltip");
        doc.body.appendChild(tip);
        doc.addEventListener("click", function (e) { if (e.target !== tip) tip.style.display = "none"; });
        (doc.defaultView || window).addEventListener("scroll", function () { tip.style.display = "none"; }, true);
      }
      tip.textContent = el.__hgHint;
      tip.__for = el;
      var r = el.getBoundingClientRect(), vw = (doc.defaultView && doc.defaultView.innerWidth) || 360;
      tip.style.display = "block";
      tip.style.top = Math.round(r.bottom + 6) + "px";
      tip.style.left = Math.max(8, Math.min(Math.round(r.left), vw - 264)) + "px";
    });
  }

  // Seerr's own mini badge: spinner = downloading, green check = available, green minus = partly available
  function nativeKind(holder) {
    var svgs = holder.querySelectorAll("svg"), svg = null;
    for (var i = 0; i < svgs.length; i++) if (!svgs[i].closest("[data-hg-night-own]")) { svg = svgs[i]; break; }
    if (!svg) return null;
    if (svg.querySelector("animateTransform")) return "spin";
    if (!/\bbg-green-/.test(holder.className)) return null;
    var path = svg.querySelector("path");
    return path && /^M5\.25 12a\.75/.test(path.getAttribute("d") || "") ? "part" : "avail";
  }

  function progress(m) {
    var size = 0, left = 0, all = m ? m.items.concat(m.items4k) : [];
    for (var i = 0; i < all.length; i++) { size += +all[i].size || 0; left += +all[i].sizeLeft || 0; }
    return size > 0 ? Math.max(0, Math.min(100, Math.round(100 * (1 - left / size)))) : null;
  }

  function statusEntry(kind, m, l) {
    var T = STATUS[l];
    if (kind === "spin") { var pct = progress(m); return [SPIN, T.spin + (pct === null ? "" : " — " + pct + " %")]; }
    return kind === "avail" ? ["✅", T.avail] : [HALF, T.part];
  }

  function half(doc) {
    var span = doc.createElement("span");
    span.setAttribute("data-hg-half", "");
    return span;
  }

  function spinner(doc) {
    var ns = "http://www.w3.org/2000/svg", span = doc.createElement("span"), svg = doc.createElementNS(ns, "svg");
    span.setAttribute("data-hg-spin", "");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 8; i++) {
      var ln = doc.createElementNS(ns, "line");
      ln.setAttribute("x1", "12"); ln.setAttribute("y1", "3"); ln.setAttribute("x2", "12"); ln.setAttribute("y2", "7");
      ln.setAttribute("stroke", "currentColor"); ln.setAttribute("stroke-width", "3.2"); ln.setAttribute("stroke-linecap", "round");
      ln.setAttribute("opacity", String(0.3 + i * 0.1)); ln.setAttribute("transform", "rotate(" + i * 45 + " 12 12)");
      svg.appendChild(ln);
    }
    span.appendChild(svg);
    return span;
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
      if (info && info.tmdbId && (info.mediaType === "movie" || info.mediaType === "tv") && (info !== o || Array.isArray(info.downloadStatus))) {
        var key = info.mediaType + "/" + info.tmdbId, prev = state.media[key] || {};
        var names = info === o ? prev.names || [] : [o.title, o.name, o.originalTitle, o.originalName].filter(Boolean);
        state.media[key] = { items: Array.isArray(info.downloadStatus) ? info.downloadStatus : [], items4k: Array.isArray(info.downloadStatus4k) ? info.downloadStatus4k : [], status: info.status, names: names.length ? names : prev.names || [], at: now };
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
    var requests = getJSON(REQUESTS_URL).then(function (d) { state.requests = d; }, function () { state.requests = null; });
    state.waitingLoading = Promise.all([requests, getJSON(WAITING_URL).then(function (d) { state.waiting = d; }, function () { state.waiting = null; })]).then(function () {
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
    if (root.getAttribute("data-hg-night") === "title") { root.removeAttribute("title"); root.removeAttribute("aria-label"); }
    root.removeAttribute("data-hg-night-moon");
    root.removeAttribute("data-hg-night-pill");
    root.removeAttribute("data-hg-night-icons");
    root.removeAttribute("data-hg-night-solid");
    if (root.getAttribute("role") === "img") root.removeAttribute("role");
    root.__hgHint = null;
    root.removeAttribute("data-hg-night");
  }

  function setFlags(doc, b, place, f, l) {
    var list = flagList(f, l), want = list.map(function (x) { return x[0]; }).join("");
    if ((b.getAttribute("data-hg-flags") || "") === want) return;
    var old = b.querySelectorAll("[data-hg-flag-own]");
    for (var i = 0; i < old.length; i++) old[i].parentNode.removeChild(old[i]);
    if (b.hasAttribute("data-hg-flags-title")) {
      var prev = b.getAttribute("data-hg-flags-title");
      if (prev) b.setAttribute("title", prev); else b.removeAttribute("title");
      b.removeAttribute("data-hg-flags-title");
    }
    b.__hgHint = null;
    if (!want) { b.removeAttribute("data-hg-flags"); return; }
    for (var j = 0; j < list.length; j++) {
      var e = doc.createElement("span");
      e.setAttribute("data-hg-flag-own", "");
      e.setAttribute("role", "img");
      e.setAttribute("aria-label", list[j][1]);
      e.style.cssText = "margin-left:.25rem";
      e.textContent = list[j][0];
      place.appendChild(e);
    }
    var hint = merged(list, l), before = b.getAttribute("title") || "";
    b.setAttribute("data-hg-flags-title", before);
    b.setAttribute("title", before ? before + " · " + hint : hint);
    b.style.cursor = "help";
    tapHint(doc, b, b.getAttribute("title"));
    b.setAttribute("data-hg-flags", want);
  }

  // StatusBadge without a download in progress ("Requested", "Pending"): 🌙/📅 after the text
  function flagBadges(doc, rs, loc) {
    var rows = doc.querySelectorAll("div.relative.z-20"), seen = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i], b = row.parentElement, span = row.querySelector(":scope > span");
      if (!b || !span || !/\binline-flex\b/.test(b.className) || !/\brounded-full\b/.test(b.className)) continue;
      seen.push([b, row, span.textContent.trim()]);
    }
    var pend = doc.querySelectorAll("a.inline-flex.rounded-full, span.inline-flex.rounded-full");
    for (var p = 0; p < pend.length; p++) {
      var pb = pend[p], txt = "";
      for (var c = 0; c < pb.childNodes.length; c++) if (pb.childNodes[c].nodeType === 3) txt += pb.childNodes[c].textContent;
      if (/bg-yellow-500\/80/.test(pb.className) && PENDING.test(txt.trim())) seen.push([pb, pb, txt.trim()]);
    }
    for (var k = 0; k < seen.length; k++) {
      var badge = seen[k][0], text = seen[k][2], key = badgeKey(badge, loc), m = key && state.media[key];
      if (key && rs && (rs.canWait[key] || Object.prototype.hasOwnProperty.call(rs.unreleased, key) || Object.prototype.hasOwnProperty.call(rs.noRelease, key))) loadMedia(key);
      var f = flags(key, rs, m);
      if (badge.getAttribute("data-hg-night") !== null) f.moon = false;
      if (/^(4K\s)?(Available|Verfügbar)$/.test(text)) f = { moon: false, compass: false, cal: false };
      setFlags(doc, badge, seen[k][1], f, lang(doc, text.replace(/^4K\s/, "")));
    }
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

  // TitleCard (Discover, lists): StatusBadgeMini chip; the title comes from the poster Seerr's list response named. Night-paused or Can wait 🌙, not released 📅, both side by side in a pill
  function titleCards(doc, set, rs) {
    var badges = doc.querySelectorAll('[role="link"] .pointer-events-none.z-40.flex');
    for (var i = 0; i < badges.length; i++) {
      var card = badges[i].closest('[role="link"]'), holder = badges[i].firstElementChild && badges[i].firstElementChild.firstElementChild;
      var img = card && card.querySelector("img");
      if (!img || !holder) continue;
      var src = "";
      try { src = decodeURIComponent((img.getAttribute("src") || "") + " " + (img.getAttribute("srcset") || "")); } catch (e) { src = img.getAttribute("src") || ""; }
      var key = null;
      for (var p in state.posters) if (src.indexOf(p) >= 0) { key = state.posters[p]; break; }
      var m = key && state.media[key], f = flags(key, rs, m), l = lang(doc);
      var kind = nativeKind(holder), list = flagList(f, l, kind === "part");
      if (m && allWaiting(m.items, set) && !f.moon) list.unshift(["🌙", TEXT[l].label.replace(/\s*🌙$/, "") + " — " + TEXT[l].tip]);
      var status = kind && (kind !== "spin" || !(m && allWaiting(m.items, set)));
      if (kind && status) list.unshift(statusEntry(kind, m, l));
      if (kind === "part" && m && busy(m) && !allWaiting(m.items, set)) list.splice(1, 0, statusEntry("spin", m, l));
      var want = list.map(function (x) { return x[0]; }).join("");
      if (!want) { restore(holder); continue; }
      if (holder.getAttribute("data-hg-night-icons") === want) continue;
      restore(holder);
      for (var c = 0; c < holder.children.length; c++) hide(holder.children[c]);
      var tips = merged(list, l);
      var icon = own(doc, "span", want, "");
      if (want.indexOf(SPIN) >= 0 || want.indexOf(HALF) >= 0) {
        icon.textContent = "";
        list.forEach(function (x) {
          if (x[0] === SPIN) icon.appendChild(spinner(doc));
          else if (x[0] === HALF) icon.appendChild(half(doc));
          else { var t = doc.createElement("span"); t.textContent = x[0]; icon.appendChild(t); }
        });
      }
      holder.appendChild(icon);
      if (kind && status) holder.setAttribute("data-hg-night-solid", "");
      holder.setAttribute(list.length > 1 ? "data-hg-night-pill" : "data-hg-night-moon", "");
      holder.setAttribute("title", tips);
      holder.setAttribute("aria-label", tips);
      holder.setAttribute("role", "img");
      tapHint(doc, holder, tips);
      holder.setAttribute("data-hg-night-icons", want);
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
    s.textContent = "[data-hg-night-hide]{display:none !important}[data-hg-night-grey]{width:100% !important;background:#4b5563 !important}[data-hg-night-moon]{display:flex !important;align-items:center;justify-content:center;box-sizing:border-box;width:1.625rem !important;height:1.625rem !important;padding:0 !important}[data-hg-night-moon] > [data-hg-night-own]{display:block;font-size:.95rem;line-height:1;text-align:center}[data-hg-night-pill]{display:flex !important;align-items:center;justify-content:center;box-sizing:border-box;width:auto !important;height:1.625rem !important;padding:0 .3rem !important}[data-hg-night-pill] > [data-hg-night-own]{display:block;font-size:.95rem;line-height:1;letter-spacing:.1rem;white-space:nowrap}[data-hg-night-solid]{background-color:rgba(99,102,241,.8) !important;border-color:#818cf8 !important;--tw-ring-color:#818cf8 !important;color:#eef2ff !important}[data-hg-half]{display:inline-block;width:14px;height:14px;box-sizing:border-box;border-radius:50%;border:2px solid #fff;background:linear-gradient(90deg,#fff 50%,transparent 50%);vertical-align:middle}[data-hg-night-own] > [data-hg-half] + *{margin-left:.2rem}[data-hg-spin]{display:inline-block;width:1rem;height:1rem;vertical-align:middle;line-height:0}[data-hg-spin] svg{display:block;width:100%;height:100%;animation:hg-spin .8s steps(8) infinite}@keyframes hg-spin{to{transform:rotate(360deg)}}@media (prefers-reduced-motion:reduce){[data-hg-spin] svg{animation:none}}[data-hg-night][data-hg-night-icons]{pointer-events:auto;cursor:help}#hg-flag-tip{position:fixed;z-index:10000;max-width:16rem;padding:.4rem .6rem;border-radius:.5rem;background:#111827;color:#f3f4f6;border:1px solid #4b5563;font-size:.75rem;line-height:1.1rem;white-space:pre-line;box-shadow:0 4px 12px rgba(0,0,0,.4)}";
    (doc.head || doc.documentElement).appendChild(s);
  }

  function apply(doc, loc) {
    var set = waitingSet(state.waiting, Date.now());
    if (set && !Object.keys(set).length) set = null;
    var rs = requestSets(state.requests, Date.now());
    style(doc);
    statusBadges(doc, set, loc);
    flagBadges(doc, rs, loc);
    titleCards(doc, set, rs);
    downloadBlocks(doc, set, loc);
  }

  // Download now (download-now.js) started these: no label even while night-waiting.json still lists them (rewritten every 2 min)
  function release(ids) {
    var until = Date.now() + RELEASED_FOR;
    for (var i = 0; i < (ids || []).length; i++) state.released[String(ids[i]).toLowerCase()] = until;
    schedule();
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

  // for other scripts (my-request.js): a title's icons with their hints, and the tap hint
  function icons(key) {
    if (typeof document === "undefined") return [];
    return flagList(flags(key, requestSets(state.requests, Date.now()), state.media[key]), lang(document));
  }

  var api = { icons: icons, hintText: function (list) { return merged(list, lang(document)); }, hint: function (el, text) { tapHint(document, el, text); }, requestSets: requestSets, flags: flags, waitingSet: waitingSet, isWaiting: isWaiting, allWaiting: allWaiting, remember: remember, badgeKey: badgeKey, blockItems: blockItems, apply: apply, refresh: refresh, release: release, state: state };
  if (typeof window === "undefined") return;
  window.hgNightLabel = api;
  try {
    hookXHR(window.XMLHttpRequest);
    if (!window.MutationObserver || !document.body) return;
    schedule();
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var t = records[i].target;
        if (!(t.closest && t.closest("[data-hg-night],[data-hg-flags]"))) { schedule(); return; }
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
    setInterval(refresh, TTL);
  } catch (e) { /* never break Seerr */ }
})();
