// "Waiting for the night" label (README "Night label"): a download paused by the "Can wait" night queue shows the night label instead of Seerr's "Processing" at 0 %; a "Can wait" request not downloading gets a 🌙, a title not released yet a 📅, a running search a pulsing 🔍, a request without a search yet 🔎, waiting for approval ⏳, a download or import problem ⚠️. All lists come from night-downloads.py; missing, stale or unexpected = Seerr unchanged.
(function () {
  "use strict";
  var WAITING_URL = "/_indexer-bar/night-waiting.json";
  var REQUESTS_URL = "/_indexer-bar/request-state.json";
  var MAX_AGE = 10 * 60 * 1000;
  var TTL = 60 * 1000;
  var TEXT = {
    en: { label: "Waiting for the night 🌙", short: "🌙 Tonight", tip: "Starts automatically at 22:05 or on Sunday; untick 'Can wait' next time for an immediate download" },
    de: { label: "Wartet auf die Nacht 🌙", short: "🌙 Heute Nacht", tip: "Startet automatisch um 22:05 oder am Sonntag; nächstes Mal „Kann warten“ abwählen für einen sofortigen Download" },
  };
  var FLAGS = {
    en: { moon: "Waiting for the night — downloads at night (cheaper power)", compass: "No release found yet — searched again in tonight's scheduled search (04:17)", cal: "Not released yet", expected: "expected", moonShort: "Waiting for the night", compassNight: ", downloads at night when found", restNight: "the rest downloads at night", compassShort: "No release found yet — searched again tonight (04:17)", nextEp: "next episode", soFar: "Everything released so far is in Jellyfin", nextExp: "Next episode expected", nightShort: "downloads at night", soFarLabel: "Available so far", locale: "en-GB" },
    de: { moon: "Wartet auf die Nacht — lädt nachts herunter (günstigerer Strom)", compass: "Noch kein Release gefunden — die geplante Suche heute Nacht (04:17) sucht weiter", cal: "Noch nicht erschienen", expected: "erwartet", moonShort: "Wartet auf die Nacht", compassNight: ", lädt nachts, sobald gefunden", restNight: "der Rest lädt nachts herunter", compassShort: "Noch kein Release gefunden — die Suche heute Nacht (04:17) sucht weiter", nextEp: "nächste Folge", soFar: "Alles bisher Erschienene ist in Jellyfin", nextExp: "Nächste Folge erwartet", nightShort: "lädt nachts", soFarLabel: "Bisher verfügbar", locale: "de-CH" },
  };
  var STATUS = {
    en: { spin: "Downloading now", avail: "Available in Jellyfin", part: "Partly available" },
    de: { spin: "Lädt gerade herunter", avail: "In Jellyfin verfügbar", part: "Teilweise verfügbar" },
  };
  var LIVE = {
    en: { started: "Searching indexers now (started {t}) — usually a few minutes", queued: "Waiting for other searches ({n} ahead)", fresh: "Requested — search starts shortly", unsearched: "Requested — not searched yet; tonight's scheduled search (04:17) tries", pending: "Waiting for approval", problem: "Problem — see Requests/Activity", seerr: "Problem — Seerr could not hand the request to Radarr/Sonarr", importing: "Importing…", imported: "Downloaded — in Jellyfin shortly", locale: "en-GB" },
    de: { started: "Sucht gerade in den Indexern (seit {t}) — dauert meist ein paar Minuten", queued: "Wartet auf andere Suchen ({n} davor)", fresh: "Angefragt — die Suche startet gleich", unsearched: "Angefragt — noch nicht gesucht; die geplante Suche heute Nacht (04:17) versucht es", pending: "Wartet auf Freigabe", problem: "Problem — siehe Anfragen/Aktivität", seerr: "Problem — Seerr konnte die Anfrage nicht an Radarr/Sonarr übergeben", importing: "Wird importiert…", imported: "Heruntergeladen — gleich in Jellyfin", locale: "de-CH" },
  };
  var FRESH = 5 * 60 * 1000;
  var SEARCH = "🔍";
  var WAIT = "🔎";
  var SPIN = "◌";
  var HALF = "◐";
  var PENDING = /^(4K )?(Pending|Ausstehend)$/;
  var PART = /^(4K\s)?(Partially Available|Teilweise verfügbar)$/i;
  var MEDIA_LINK = /^\/(movie|tv)\/(\d+)$/;
  var EPISODE = /^(.*): (?:Season|Staffel) (\d+) (?:Episode|Folge) (\d+)$/;
  var GERMAN = /^(Verarbeiten|Verfügbar|Teilweise verfügbar|Angefragt|Ausstehend|Gelöscht)$/;
  var POSTER = /\/t\/p\/[^/]+(\/[^/?&\s"]+)/;
  var CAP = 1500;
  var BAR = "div.relative.h-6.overflow-hidden.rounded-full.bg-gray-700";

  var RELEASED_FOR = 10 * 60 * 1000;
  var state = { waiting: null, requests: null, waitingAt: 0, waitingLoading: null, media: {}, posters: {}, loading: {}, released: {}, requested: {}, poll: 15 * 1000 };

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
    var obj = function (v) { return v && typeof v === "object" && !Array.isArray(v) ? v : {}; };
    var set = function (v) { var o = {}; (Array.isArray(v) ? v : []).forEach(function (k) { o[String(k)] = true; }); return o; };
    return { canWait: set(data.canWait), unreleased: data.unreleased, noRelease: obj(data.noRelease), searching: obj(data.searching), fresh: obj(data.fresh), pending: set(data.pending), queue: obj(data.queue), imported: set(data.imported), soFar: set(data.soFar), updated: Date.parse(data.updated) };
  }

  function busy(m) {
    if (!m) return false;
    if (m.status === 5) return true;
    for (var i = 0; i < m.items.length; i++) if (String(m.items[i].status).toLowerCase() !== "paused") return true;
    return false;
  }

  // which icons a title gets: ⚠️ problem, ⏳ approval, 🔍 search running / 🔎 none yet, 🌙 Can wait (not downloading, not available), 🧭 nothing found, 📅 not released yet; queue state for the status icon
  function flags(key, rs, m) {
    if (!rs || !key) return { moon: false, compass: false, cal: false, date: null };
    var has = Object.prototype.hasOwnProperty, done = !!(m && m.status === 5), now = Date.now();
    var q = !done && has.call(rs.queue, key) && rs.queue[key] && typeof rs.queue[key] === "object" ? rs.queue[key] : {};
    var loading = q.state === "downloading" || q.state === "importing";
    var search = !done && has.call(rs.searching, key) && rs.searching[key] && typeof rs.searching[key] === "object" ? rs.searching[key] : null;
    var fresh = null, at = state.requested[key];
    if (!search && !done && has.call(rs.fresh, key)) fresh = { at: rs.fresh[key] };
    else if (!search && !done && at && now - Date.parse(at) < FRESH && !(rs.updated >= Date.parse(at)) && !busy(m)) fresh = { at: at };
    var moon = rs.canWait[key] === true && !busy(m) && !done && !loading;
    var compass = has.call(rs.noRelease, key) && !busy(m) && !done;
    var cal = has.call(rs.unreleased, key) && !done;
    return { moon: moon, compass: compass, cal: cal, date: cal ? rs.unreleased[key] : null, search: search, fresh: fresh, pending: !done && rs.pending[key] === true,
      problem: q.state === "problem" ? q : null, importing: q.state === "importing", downloading: q.state === "downloading", imported: !done && rs.imported[key] === true, soFar: !done && rs.soFar[key] === true };
  }

  function clock(iso, l) {
    var d = new Date(iso || "");
    if (!iso || isNaN(d.getTime())) return "";
    try { return d.toLocaleTimeString(LIVE[l].locale, { hour: "2-digit", minute: "2-digit" }); } catch (e) { return ""; }
  }

  function liveList(f, l) {
    var L = LIVE[l], out = [];
    if (f.problem) out.push(["⚠️", f.problem.code === "seerr" ? L.seerr : L.problem + (f.problem.message ? ": " + f.problem.message : "")]);
    if (f.pending) out.push(["⏳", L.pending]);
    if (f.search) {
      var t = clock(f.search.started, l);
      out.push([SEARCH, f.search.status === "queued" ? L.queued.replace("{n}", String(+f.search.ahead || 0)) : t ? L.started.replace("{t}", t) : L.started.replace(/ \([^)]*\{t\}\)/, "")]);
    } else if (f.fresh) out.push([WAIT, f.fresh.at && Date.now() - Date.parse(f.fresh.at) < FRESH ? L.fresh : L.unsearched]);
    return out;
  }

  // status icon from Radarr/Sonarr's queue while Seerr shows none: importing / downloading spinner, file there ✅
  function queueStatus(f, l) {
    if (f.importing) return [SPIN, LIVE[l].importing];
    if (f.downloading) return [SPIN, STATUS[l].spin];
    return f.imported ? ["✅", LIVE[l].imported] : null;
  }

  function dateText(iso, l, short) {
    var d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    if (!d) return "";
    try { return new Date(Date.UTC(+d[1], +d[2] - 1, +d[3])).toLocaleDateString(FLAGS[l].locale, short ? { day: "numeric", month: "short", timeZone: "UTC" } : { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }); } catch (e) { return iso; }
  }

  // [icon, hint] in display order ⚠️⏳🔍🌙🧭📅 (badges: with the queue status after 🔍)
  // series complete so far: ✅ + next air date + 🌙 (the unaired rest downloads at night)
  function soFarList(f, l) {
    var out = [["✅", FLAGS[l].soFar]];
    if (f.cal) out.push(["📅", FLAGS[l].nextExp + (f.date ? " " + dateText(f.date, l, true) : "")]);
    if (f.moon) out.push(["🌙", FLAGS[l].nightShort]);
    return out;
  }

  function flagList(f, l, part, card) {
    if (f.soFar) return soFarList(f, l);
    var out = liveList(f, l), st = !card && queueStatus(f, l);
    if (st) out.push(st);
    if (f.moon) out.push(["🌙", FLAGS[l].moon]);
    if (f.compass) out.push(["🧭", FLAGS[l].compass]);
    if (f.cal) out.push(["📅", FLAGS[l].cal + (f.date ? " — " + (part ? FLAGS[l].nextEp + " " + dateText(f.date, l, true) : FLAGS[l].expected + " " + dateText(f.date, l)) : "")]);
    return out;
  }

  // one hint for a badge's icons together: 🌙 shortened when combined, 🧭 with the night when 🌙 is there
  function merged(list, l) {
    if (list.length === 1 || list[0][0] === "✅") return list.map(function (x) { return x[1]; }).join(" · ");
    if (list[0][0] === HALF) {
      var rest = list.slice(1), only = rest.length === 1 && rest[0][0] === "🌙";
      return [list[0][1]].concat(rest.map(function (x) { return x[0] === "🌙" ? FLAGS[l].moonShort + (only ? " — " + FLAGS[l].restNight : "") : x[0] === "🧭" ? FLAGS[l].compassShort : x[1]; })).join(" · ");
    }
    var moon = list.some(function (x) { return x[0] === "🌙"; });
    return list.map(function (x) { return x[0] === "🌙" ? FLAGS[l].moonShort : x[0] === "🧭" && moon ? x[1] + FLAGS[l].compassNight : x[1]; }).join(" · ");
  }

  function hideTip(doc) {
    var tip = doc.getElementById("hg-flag-tip");
    if (!tip) return;
    tip.style.display = "none";
    if (tip.__for && tip.__for.getAttribute("aria-describedby") === "hg-flag-tip") tip.__for.removeAttribute("aria-describedby");
  }

  // one static hint box anchored to the badge (below it; a status pill's above it, away from Seerr's popup), never following the mouse; Escape closes it
  function showTip(doc, el) {
    var tip = doc.getElementById("hg-flag-tip");
    if (!tip) {
      tip = doc.createElement("div");
      tip.id = "hg-flag-tip";
      tip.setAttribute("role", "tooltip");
      doc.body.appendChild(tip);
      doc.addEventListener("click", function (e) { if (e.target !== tip) hideTip(doc); });
      doc.addEventListener("keydown", function (e) { if (e.key === "Escape") hideTip(doc); });
      (doc.defaultView || window).addEventListener("scroll", function () { hideTip(doc); }, true);
    }
    if (tip.__for && tip.__for !== el && tip.__for.getAttribute("aria-describedby") === "hg-flag-tip") tip.__for.removeAttribute("aria-describedby");
    tip.textContent = el.__hgHint;
    tip.__for = el;
    if (!el.hasAttribute("aria-describedby")) el.setAttribute("aria-describedby", "hg-flag-tip");
    var r = el.getBoundingClientRect(), vw = (doc.defaultView && doc.defaultView.innerWidth) || 360;
    tip.style.display = "block";
    tip.style.left = Math.max(8, Math.min(Math.round(r.left), vw - 264)) + "px";
    placeTip(tip, el, el.__hgPop ? "above" : "below");
    return tip;
  }

  function placeTip(tip, el, side) {
    var r = el.getBoundingClientRect(), h = tip.offsetHeight;
    if (side === "above" && r.top - h - 6 < 4) side = "below";
    tip.setAttribute("data-side", side);
    tip.style.top = Math.round(side === "above" ? r.top - h - 6 : r.bottom + 6) + "px";
  }

  // mouse: the hint shows while hovering; keyboard focus shows it too; touch: the first tap shows it, a second tap goes through (link, card). pop = a status pill, possibly a Seerr popup trigger
  function tapHint(doc, el, text, pop) {
    el.__hgHint = text;
    el.__hgPop = !!pop;
    if (text) el.setAttribute("data-hg-hint", text); else el.removeAttribute("data-hg-hint");
    if (el.__hgTap) return;
    el.__hgTap = true;
    el.addEventListener("pointerdown", function (ev) { el.__hgPt = ev.pointerType; });
    el.addEventListener("pointerenter", function (ev) {
      if (ev.pointerType !== "mouse" || !el.__hgHint) return;
      state.hover = el;
      showTip(doc, el);
    });
    el.addEventListener("pointerleave", function (ev) {
      if (ev.pointerType !== "mouse") return;
      if (state.hover === el) state.hover = null;
      var tip = doc.getElementById("hg-flag-tip");
      if (tip && tip.__for === el) hideTip(doc);
    });
    el.addEventListener("focus", function () { if (el.__hgHint) showTip(doc, el); });
    el.addEventListener("blur", function () { var tip = doc.getElementById("hg-flag-tip"); if (tip && tip.__for === el) hideTip(doc); });
    el.addEventListener("click", function (ev) {
      var touch = el.__hgPt === "touch";
      el.__hgPt = null;
      if (!el.__hgHint || !touch) return;
      var tip = doc.getElementById("hg-flag-tip");
      if (tip && tip.__for === el && tip.style.display !== "none") { hideTip(doc); return; }
      ev.preventDefault();
      ev.stopPropagation();
      showTip(doc, el);
    });
  }

  // Seerr's popup follows the cursor: our hint stays put on the other side of the pill, so the two never cover each other
  function popAside(doc, node) {
    var el = state.hover, tip = doc.getElementById("hg-flag-tip");
    if (!el || !tip || tip.__for !== el || tip.style.display === "none" || !/\bz-50\b/.test(node.className || "") || !/\babsolute\b/.test(node.className || "")) return;
    var view = doc.defaultView, later = view && view.requestAnimationFrame ? function (f) { view.requestAnimationFrame(function () { view.requestAnimationFrame(f); }); } : function (f) { setTimeout(f, 32); };
    later(function () {
      if (!node.isConnected || tip.__for !== el || tip.style.display === "none") return;
      var p = node.getBoundingClientRect(), r = el.getBoundingClientRect();
      if (!p.height) return;
      var side = p.top + p.height / 2 < r.top + r.height / 2 ? "below" : "above";
      if (tip.getAttribute("data-side") !== side) placeTip(tip, el, side);
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

  function requested(key, at) {
    if (!(Date.parse(state.requested[key]) >= Date.parse(at))) state.requested[key] = at;
  }

  // the session caches stay small: oldest titles go first, posters of dropped titles with them
  function prune() {
    var keys = Object.keys(state.media);
    if (keys.length <= CAP && Object.keys(state.posters).length <= CAP) return;
    keys.sort(function (a, b) { return (state.media[a].at || 0) - (state.media[b].at || 0); });
    for (var i = 0; i < keys.length - CAP; i++) { delete state.media[keys[i]]; delete state.requested[keys[i]]; }
    var posters = Object.keys(state.posters);
    for (var j = 0; j < posters.length; j++) if (!state.media[state.posters[posters[j]]] || posters.length - j > CAP) delete state.posters[posters[j]];
  }

  // Collects what Seerr's API returned: download lists per title, poster -> title, title names.
  function remember(json) {
    var now = Date.now();
    (function walk(o, depth) {
      if (!o || typeof o !== "object" || depth > 6) return;
      if (Array.isArray(o)) { for (var i = 0; i < o.length && i < 500; i++) walk(o[i], depth + 1); return; }
      if (o.createdAt && o.status === 2 && o.media && o.media.tmdbId && (o.type === "movie" || o.type === "tv")) requested(o.type + "/" + o.media.tmdbId, o.createdAt);
      var info = o.mediaInfo && typeof o.mediaInfo === "object" ? o.mediaInfo : (Array.isArray(o.downloadStatus) ? o : null);
      if (info && info.tmdbId && Array.isArray(info.requests)) info.requests.forEach(function (r) { if (r && r.status === 2 && r.createdAt) requested(info.mediaType + "/" + info.tmdbId, r.createdAt); });
      if (info && info.tmdbId && (info.mediaType === "movie" || info.mediaType === "tv") && (info !== o || Array.isArray(info.downloadStatus))) {
        var key = info.mediaType + "/" + info.tmdbId, prev = state.media[key] || {};
        var names = info === o ? prev.names || [] : [o.title, o.name, o.originalTitle, o.originalName].filter(Boolean);
        state.media[key] = { items: Array.isArray(info.downloadStatus) ? info.downloadStatus : [], items4k: Array.isArray(info.downloadStatus4k) ? info.downloadStatus4k : [], status: info.status, names: names.length ? names : prev.names || [], at: now };
        if (info !== o && o.posterPath) state.posters[o.posterPath] = key;
      }
      for (var k in o) if (k !== "downloadStatus" && k !== "downloadStatus4k" && Object.prototype.hasOwnProperty.call(o, k)) walk(o[k], depth + 1);
    })(json, 0);
    prune();
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
    // login / setup pages have no Seerr session: the data is refused there, fetched once the SPA moves on
    if (typeof window !== "undefined" && /^\/(login|setup|resetpassword)(\/|$)/.test(window.location.pathname)) return Promise.resolve();
    var requests = getJSON(REQUESTS_URL).then(function (d) {
      var before = state.requests && state.requests.searching, after = d && d.searching;
      // a search just ended: Seerr's download state for that title is fetched again (spinner, ✅)
      if (before && typeof before === "object") for (var k in before) if (!(after && after[k]) && state.media[k]) state.media[k].at = 0;
      state.requests = d;
    }, function () { state.requests = null; });
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
    var own = doc.querySelector(".media-status span:not([data-hg-night-own]):not([data-hg-flag-own])");
    if (own && GERMAN.test(own.textContent.trim())) return "de";
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
    root.__hgFlagsKey = null;
    root.removeAttribute("data-hg-hint");
    root.removeAttribute("data-hg-night");
  }

  function magnifier(doc, live) {
    var ns = "http://www.w3.org/2000/svg", span = doc.createElement("span"), svg = doc.createElementNS(ns, "svg");
    span.setAttribute("data-hg-search", live ? "live" : "wait");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    var c = doc.createElementNS(ns, "circle"), ln = doc.createElementNS(ns, "line");
    c.setAttribute("cx", "10"); c.setAttribute("cy", "10"); c.setAttribute("r", "6.2"); c.setAttribute("fill", "none"); c.setAttribute("stroke", "currentColor"); c.setAttribute("stroke-width", "2.8");
    ln.setAttribute("x1", "14.6"); ln.setAttribute("y1", "14.6"); ln.setAttribute("x2", "20.5"); ln.setAttribute("y2", "20.5"); ln.setAttribute("stroke", "currentColor"); ln.setAttribute("stroke-width", "3.2"); ln.setAttribute("stroke-linecap", "round");
    svg.appendChild(c);
    svg.appendChild(ln);
    span.appendChild(svg);
    return span;
  }

  var DRAWN = {};
  DRAWN[SPIN] = true; DRAWN[HALF] = true; DRAWN[SEARCH] = true; DRAWN[WAIT] = true;

  function iconEl(doc, tok) {
    if (tok === SPIN) return spinner(doc);
    if (tok === HALF) return half(doc);
    if (tok === SEARCH || tok === WAIT) return magnifier(doc, tok === SEARCH);
    var t = doc.createElement("span");
    t.textContent = tok;
    return t;
  }

  function setFlags(doc, b, place, f, l, part) {
    var list = part ? [[HALF, STATUS[l].part]].concat(flagList(f, l, true, true)) : flagList(f, l), want = list.map(function (x) { return x[0]; }).join(""), same = want + "|" + (list.length ? merged(list, l) : "");
    if ((b.getAttribute("data-hg-flags") || "") === want && (!want || b.__hgFlagsKey === same)) return;
    b.__hgFlagsKey = same;
    var old = b.querySelectorAll("[data-hg-flag-own]");
    for (var i = 0; i < old.length; i++) old[i].parentNode.removeChild(old[i]);
    b.__hgHint = null;
    b.removeAttribute("data-hg-hint");
    if (!want) { b.removeAttribute("data-hg-flags"); return; }
    for (var j = 0; j < list.length; j++) {
      var e = doc.createElement("span");
      e.setAttribute("data-hg-flag-own", "");
      e.setAttribute("role", "img");
      e.setAttribute("aria-label", list[j][1]);
      e.style.cssText = "margin-left:.25rem";
      if (DRAWN[list[j][0]]) e.appendChild(iconEl(doc, list[j][0])); else e.textContent = list[j][0];
      place.appendChild(e);
    }
    b.style.cursor = "help";
    tapHint(doc, b, merged(list, l), true);
    b.setAttribute("data-hg-flags", want);
  }

  function listed(rs, key) {
    var has = Object.prototype.hasOwnProperty;
    return rs.canWait[key] || rs.pending[key] || rs.imported[key] || has.call(rs.unreleased, key) || has.call(rs.noRelease, key) || has.call(rs.searching, key) || has.call(rs.fresh, key) || has.call(rs.queue, key);
  }

  // Seerr's "Partially Available" on a series complete so far reads "Available so far" (Seerr's text back when that ends)
  function relabel(b, row, label) {
    var span = row.querySelector(":scope > span");
    if (!span) return;
    if (label) {
      if (!b.hasAttribute("data-hg-orig")) b.setAttribute("data-hg-orig", span.textContent.trim());
      if (span.textContent !== label) span.textContent = label;
    } else if (b.hasAttribute("data-hg-orig")) {
      span.textContent = b.getAttribute("data-hg-orig");
      b.removeAttribute("data-hg-orig");
    }
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
      if (key && rs && listed(rs, key)) loadMedia(key);
      text = badge.getAttribute("data-hg-orig") || text;
      var f = flags(key, rs, m), part = PART.test(text);
      relabel(badge, seen[k][1], f.soFar && part ? FLAGS[lang(doc, text.replace(/^4K\s/, ""))].soFarLabel : null);
      if (f.soFar) part = false;
      if (part) badge.setAttribute("data-hg-part", ""); else badge.removeAttribute("data-hg-part");
      if (badge.getAttribute("data-hg-night") !== null) f.moon = false;
      // Seerr's own spinner / progress bar is there (its popup tells the download): nothing of ours
      if (badge.querySelector("svg animateTransform") || badge.querySelector(":scope > div.absolute.left-0.top-0.z-10")) f = { moon: false, compass: false, cal: false };
      if (part || (m && m.status === 4)) f.imported = false;
      if (/^(4K\s)?(Available|Verfügbar)$/.test(text)) f = { moon: false, compass: false, cal: false };
      setFlags(doc, badge, seen[k][1], f, lang(doc, text.replace(/^4K\s/, "")), part);
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
      row.appendChild(own(doc, "span", (is4k ? "4K " : "") + t.short));
      tapHint(doc, b, t.label + " — " + t.tip, true);
      b.setAttribute("data-hg-night", "");
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
      var pm = POSTER.exec(src), key = pm ? state.posters[pm[1]] || null : null;
      if (!pm) for (var p in state.posters) if (src.indexOf(p) >= 0) { key = state.posters[p]; break; }
      var m = key && state.media[key], f = flags(key, rs, m), l = lang(doc);
      if (m && m.at === 0) loadMedia(key);
      var kind = nativeKind(holder), list = flagList(f, l, kind === "part", true), qs = queueStatus(f, l);
      if (m && allWaiting(m.items, set) && !f.moon) list.unshift(["🌙", TEXT[l].label.replace(/\s*🌙$/, "") + " — " + TEXT[l].tip]);
      var status = kind && (kind !== "spin" || !(m && allWaiting(m.items, set)));
      if (f.soFar) { status = true; kind = kind || "queue"; }
      else if (kind && status) list.unshift(kind === "spin" && f.importing ? [SPIN, LIVE[l].importing] : statusEntry(kind, m, l));
      else if (!kind && qs && !(m && allWaiting(m.items, set))) { list.unshift(qs); status = true; kind = "queue"; }
      if (!f.soFar && kind === "part" && m && busy(m) && !allWaiting(m.items, set)) list.splice(1, 0, statusEntry("spin", m, l));
      var want = list.map(function (x) { return x[0]; }).join("");
      if (!want) { restore(holder); continue; }
      var tips = merged(list, l);
      if (holder.getAttribute("data-hg-night-icons") === want && holder.getAttribute("data-hg-hint") === tips) continue;
      restore(holder);
      for (var c = 0; c < holder.children.length; c++) hide(holder.children[c]);
      var icon = own(doc, "span", want, "");
      if (list.some(function (x) { return DRAWN[x[0]]; })) {
        icon.textContent = "";
        list.forEach(function (x) { icon.appendChild(iconEl(doc, x[0])); });
      }
      holder.appendChild(icon);
      if (kind && status) holder.setAttribute("data-hg-night-solid", "");
      holder.setAttribute(list.length > 1 ? "data-hg-night-pill" : "data-hg-night-moon", "");
      icon.setAttribute("role", "img");
      icon.setAttribute("aria-label", tips);
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
    var bars = doc.querySelectorAll(BAR);
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
      status.parentNode.insertBefore(badge, status.nextSibling);
      block.setAttribute("data-hg-night", "");
    }
  }

  function style(doc) {
    if (doc.getElementById("hg-night-style")) return;
    var s = doc.createElement("style");
    s.id = "hg-night-style";
    s.textContent = "[data-hg-night-hide]{display:none !important}[data-hg-night-grey]{background:#4b5563 !important;transition:none !important}[data-hg-night-moon]{display:flex !important;align-items:center;justify-content:center;box-sizing:border-box;width:1.625rem !important;height:1.625rem !important;padding:0 !important}[data-hg-night-moon] > [data-hg-night-own]{display:block;font-size:.95rem;line-height:1;text-align:center}[data-hg-night-pill]{display:flex !important;align-items:center;justify-content:center;box-sizing:border-box;width:auto !important;height:1.625rem !important;padding:0 .3rem !important}[data-hg-night-pill] > [data-hg-night-own]{display:block;font-size:.95rem;line-height:1;letter-spacing:.1rem;white-space:nowrap}[data-hg-night-solid]{background-color:rgba(99,102,241,.8) !important;border-color:#818cf8 !important;--tw-ring-color:#818cf8 !important;color:#eef2ff !important}[data-hg-half]{display:inline-block;width:14px;height:14px;box-sizing:border-box;border-radius:50%;border:2px solid #fff;background:linear-gradient(90deg,#fff 50%,transparent 50%);vertical-align:middle}[data-hg-night-own] > [data-hg-half] + *{margin-left:.2rem}[data-hg-spin]{display:inline-block;width:1rem;height:1rem;vertical-align:middle;line-height:0}[data-hg-spin] svg{display:block;width:100%;height:100%;animation:hg-spin .8s steps(8) infinite}@keyframes hg-spin{to{transform:rotate(360deg)}}a[data-hg-part],span[data-hg-part]{background-color:rgba(2,132,199,.8) !important;border-color:#0ea5e9 !important;color:#e0f2fe !important}a[data-hg-part]:hover{background-color:#0284c7 !important}[data-hg-flag-own] > [data-hg-half]{width:.75rem;height:.75rem}[data-hg-flag-own] > [data-hg-spin],[data-hg-flag-own] > [data-hg-search]{width:.85rem;height:.85rem}[data-hg-search]{display:inline-block;width:1rem;height:1rem;vertical-align:middle;line-height:0}[data-hg-search] svg{display:block;width:100%;height:100%}[data-hg-search=\"wait\"] svg{opacity:.8}[data-hg-search=\"live\"] svg{animation:hg-pulse 1.4s ease-in-out infinite}@keyframes hg-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(.84)}}[data-hg-night-own] > [data-hg-search] + *,[data-hg-night-own] > * + [data-hg-search]{margin-left:.15rem}@media (prefers-reduced-motion:reduce){[data-hg-spin] svg,[data-hg-search=\"live\"] svg{animation:none}}[data-hg-night][data-hg-night-icons]{pointer-events:auto;cursor:help}#hg-flag-tip{position:fixed;z-index:10000;max-width:16rem;padding:.4rem .6rem;border-radius:.5rem;background:#111827;color:#f3f4f6;border:1px solid #4b5563;font-size:.75rem;line-height:1.1rem;white-space:pre-line;box-shadow:0 4px 12px rgba(0,0,0,.4)}";
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
    if (typeof window !== "undefined" && doc.querySelector("[data-hg-search]") && !pollTimer) pollTimer = setTimeout(function () { pollTimer = null; refresh(); }, state.poll);
  }

  // a 🔍/🔎 on the page: request-state.json again every 15 s (rewritten every 15 s while a search runs)
  var pollTimer = null;

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

  var api = { lang: function () { return lang(document); }, icons: icons, hintText: function (list) { return merged(list, lang(document)); }, hint: function (el, text) { tapHint(document, el, text); }, requestSets: requestSets, flags: flags, waitingSet: waitingSet, isWaiting: isWaiting, allWaiting: allWaiting, remember: remember, badgeKey: badgeKey, blockItems: blockItems, apply: apply, refresh: refresh, release: release, state: state };
  if (typeof window === "undefined") return;
  window.hgNightLabel = api;
  try {
    hookXHR(window.XMLHttpRequest);
    if (!window.MutationObserver || !document.body) return;
    schedule();
    // runs before the next paint: a popup Seerr just added is patched at once (no flash), the rest is debounced
    new MutationObserver(function (records) {
      var due = false, blocks = false;
      for (var i = 0; i < records.length; i++) {
        var r = records[i], t = r.target;
        for (var n = 0; n < r.addedNodes.length; n++) {
          var a = r.addedNodes[n];
          if (a.nodeType !== 1) continue;
          if (a.parentNode === document.body) popAside(document, a);
          if (state.waitingAt && a.querySelector && (a.matches(BAR) || a.querySelector(BAR))) blocks = true;
        }
        if (!due && !(t.closest && t.closest("[data-hg-night],[data-hg-flags]"))) due = true;
      }
      if (blocks) { try { var set = waitingSet(state.waiting, Date.now()); if (set && Object.keys(set).length) downloadBlocks(document, set, window.location); } catch (e) { /* never break Seerr */ } }
      if (due) schedule();
    }).observe(document.body, { childList: true, subtree: true });
    setInterval(refresh, TTL);
  } catch (e) { /* never break Seerr */ }
})();
