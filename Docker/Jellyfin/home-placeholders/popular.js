(function () {
  // Pages "Popular movies" / "Popular shows", their nav entries and the home rows Top 10 Shows / Movies in Switzerland (Jellyfin README "Popular movies and shows").
  // Data: window.HssPopular from scripts/popular.py; the user's own library and requests through their own session.
  var API = "hss-popular/";
  var TMDB_IMG = "https://image.tmdb.org/t/p/";
  var HOME = [
    { id: "Top10Shows", key: "shows", kind: "tv", after: "RecentlyAddedShows" },
    { id: "Top10Movies", key: "movies", kind: "movie", after: "RecentlyWatchedSeries" }
  ];
  // Nav order follows the library order (Movies, Shows).
  var NAV = [
    { kind: "movie", label: "pageMovies", icon: "movie" },
    { kind: "tv", label: "pageShows", icon: "live_tv" }
  ];
  var TEXT = {
    en: {
      Top10Shows: "Top 10 Shows in Switzerland", Top10Movies: "Top 10 Movies in Switzerland", pageMovies: "Popular movies", pageShows: "Popular shows", close: "Close",
      tonight: "\uD83C\uDF19 Tonight", now: "⚡ Now", tonightHint: "Download tonight (cheaper power)", nowHint: "Download now (daytime power price)",
      limit: "Limit reached: {n} requests per {d} days", left: "{n} requests left this week", failed: "Request failed", season: "S",
      dailyFull: "Today's requests for everyone are used up, again tomorrow",
      night: "\uD83C\uDF19 Tonight", searching: "\uD83D\uDD0D Searching", unreleased: "\uD83D\uDCC5 Not released yet", downloading: "⬇️ Downloading", requested: "✓ Requested", declined: "Declined",
      empty: "Nothing on the list right now.", found: "available",
      details: "details", seasonLong: "Season {n}",
      none: "No release yet", checked: "checked {d} · rechecked daily", unchecked: "not checked yet · checked tonight", unknown: "Title not identified",
      source: "Sources: Netflix Top 10 Switzerland (weekly), TMDB popularity, where to stream in Switzerland by JustWatch (via TMDB). Only titles in the library or with a release found are shown."
    },
    de: {
      Top10Shows: "Top 10 Serien in der Schweiz", Top10Movies: "Top 10 Filme in der Schweiz", pageMovies: "Beliebte Filme", pageShows: "Beliebte Serien", close: "Schliessen",
      tonight: "\uD83C\uDF19 Heute Nacht", now: "⚡ Jetzt", tonightHint: "Heute Nacht herunterladen (günstigerer Strom)", nowHint: "Jetzt herunterladen (Tagesstrompreis)",
      limit: "Limite erreicht: {n} Anfragen pro {d} Tage", left: "Noch {n} Anfragen diese Woche", failed: "Anfrage fehlgeschlagen", season: "S",
      dailyFull: "Die Anfragen für heute sind für alle aufgebraucht, morgen wieder",
      night: "\uD83C\uDF19 Heute Nacht", searching: "\uD83D\uDD0D Wird gesucht", unreleased: "\uD83D\uDCC5 Noch nicht erschienen", downloading: "⬇️ Lädt herunter", requested: "✓ Angefragt", declined: "Abgelehnt",
      empty: "Gerade nichts auf der Liste.", found: "verfügbar",
      details: "Details", seasonLong: "Staffel {n}",
      none: "Noch kein Release", checked: "geprüft {d} · täglich neu geprüft", unchecked: "noch nicht geprüft · wird heute Nacht geprüft", unknown: "Titel nicht erkannt",
      source: "Quellen: Netflix Top 10 Schweiz (wöchentlich), TMDB-Beliebtheit, Streaming-Angebot in der Schweiz von JustWatch (über TMDB). Nur Titel aus der Bibliothek oder mit gefundenem Release."
    }
  };
  var ICON = { night: "\uD83C\uDF19", now: "\u26A1" };
  var TRENDING_PATH = "M16 6l2.29 2.29-4.88 4.88-4-4L2 16.59 3.41 18l6-6 4 4 6.3-6.29L22 12V6z";

  function langOf(lang) {
    return String(lang || "").toLowerCase().indexOf("de") === 0 ? "de" : "en";
  }

  function tr(lang, key, vars) {
    var s = TEXT[langOf(lang)][key] || TEXT.en[key] || key;
    for (var k in vars || {}) s = s.split("{" + k + "}").join(String(vars[k]));
    return s;
  }

  function titleOf(item, lang) {
    var t = item.t || {};
    return (langOf(lang) === "de" && t.de) || t.en || t.de || "";
  }

  function posterUrl(item, lang) {
    var p = item.p || {};
    var path = (langOf(lang) === "de" && p.de) || p.en;
    return typeof path === "string" && /^\/[A-Za-z0-9_.-]+$/.test(path) ? TMDB_IMG + "w342" + path : null;
  }

  function libraryMap(items) {
    var out = {};
    for (var i = 0; i < (items || []).length; i++) {
      var it = items[i];
      var tmdb = it && it.ProviderIds && it.ProviderIds.Tmdb;
      if (!tmdb || (it.Type !== "Movie" && it.Type !== "Series")) continue;
      out[(it.Type === "Movie" ? "movie/" : "tv/") + tmdb] = { id: it.Id, server: it.ServerId || "" };
    }
    return out;
  }

  // Every rank: the user's own titles, requestable ones and those without a release yet; owned but not in this user's library view = hidden.
  function visible(items, lib) {
    var out = [];
    for (var i = 0; i < (items || []).length; i++) {
      var it = items[i];
      var jf = it.id ? (lib || {})[it.k + "/" + it.id] || null : null;
      if (jf || (it.f && it.x) || (it.n && !it.o)) out.push({ item: it, jf: jf });
    }
    return out;
  }

  function dayOf(iso, lang) {
    var d = new Date(iso + "T12:00:00");
    return isNaN(d.getTime()) ? iso : d.toLocaleDateString(langOf(lang) === "de" ? "de-CH" : "en-GB", { day: "numeric", month: "short" });
  }

  function el(doc, tag, cls, text) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // TV: icon only, the labels do not fit the poster width there; fillRow adds a legend.
  function reqButton(doc, lang, mode, tv) {
    var b = el(doc, "button", "hssTopBtn hssTopBtn-" + mode + (tv ? " hssTopBtn-icon" : ""), tv ? ICON[mode] : tr(lang, mode === "night" ? "tonight" : "now"));
    b.setAttribute("type", "button");
    return b;
  }

  // Opener of the details overlay: stable per title, so the focus finds the card again after a repaint.
  function detailKey(it) {
    return it.id ? it.k + "/" + it.id : "rank/" + it.k + "/" + it.r;
  }

  // The request part of a card, also shown in the details overlay (tv: icon-only buttons).
  function actionsFor(doc, it, lang, tv, states) {
    var name = titleOf(it, lang);
    var actions = el(doc, "div", "hssTopActions");
    if (!it.x) {
      ["night", "now"].forEach(function (mode) {
        var off = reqButton(doc, lang, mode, tv);
        off.disabled = true;
        actions.appendChild(off);
      });
      actions.appendChild(el(doc, "div", "hssTopHint", it.u ? tr(lang, "unknown") : it.c ? tr(lang, "checked", { d: dayOf(it.c, lang) }) : tr(lang, "unchecked")));
      return actions;
    }
    var st = states && states.entries ? states.entries[it.x] : null;
    if (st && st.state !== "requestable") {
      var label = st.state === "requested" ? tr(lang, st.badge || "requested") : st.state === "declined" ? tr(lang, "declined") : tr(lang, "requested");
      actions.appendChild(el(doc, "div", "hssTopState", label));
      return actions;
    }
    var full = states && typeof states.left === "number" && states.left <= 0;
    var capped = !full && !!(states && states.dailyFull);
    ["night", "now"].forEach(function (mode) {
      var b = reqButton(doc, lang, mode, tv);
      b.setAttribute("data-hss-x", it.x);
      b.setAttribute("data-hss-mode", mode);
      b.setAttribute("title", tr(lang, mode === "night" ? "tonightHint" : "nowHint"));
      b.setAttribute("aria-label", name + ": " + tr(lang, mode === "night" ? "tonightHint" : "nowHint"));
      if (full || capped) b.disabled = true;
      actions.appendChild(b);
    });
    if (full) actions.appendChild(el(doc, "div", "hssTopHint", tr(lang, "limit", { n: states.limit, d: states.days })));
    else if (capped) actions.appendChild(el(doc, "div", "hssTopHint", tr(lang, "dailyFull")));
    return actions;
  }

  // states: answer of GET hss-popular/state ({entries: {id: {state, badge}}, left, limit, days}) or null while unknown.
  function card(doc, entry, lang, tv, states) {
    var it = entry.item;
    var name = titleOf(it, lang);
    var c;
    if (entry.jf) {
      c = el(doc, "a", "hssTopCard");
      c.setAttribute("href", "#/details?id=" + encodeURIComponent(entry.jf.id) + "&serverId=" + encodeURIComponent(entry.jf.server));
    } else {
      c = el(doc, "div", "hssTopCard hssTopDim" + (it.x ? "" : " hssTopNone"));
    }
    c.setAttribute("aria-label", it.r + ". " + name);
    c.setAttribute("title", name + (it.y ? " (" + it.y + ")" : ""));
    var rank = el(doc, "div", String(it.r).length > 1 ? "hssTopRank hssTopRank-2" : "hssTopRank", String(it.r));
    rank.setAttribute("aria-hidden", "true");
    c.appendChild(rank);
    var poster;
    if (entry.jf) {
      poster = el(doc, "div", "hssTopPoster");
    } else {
      poster = el(doc, "button", "hssTopPoster hssTopOpen");
      poster.setAttribute("type", "button");
      poster.setAttribute("data-hss-key", detailKey(it));
      poster.setAttribute("aria-label", name + ": " + tr(lang, "details"));
      poster.hssItem = it;
    }
    var url = posterUrl(it, lang);
    if (url) poster.style.backgroundImage = 'url("' + url + '")';
    else poster.appendChild(el(doc, "div", "hssTopNoPoster", name));
    if (it.k === "tv" && it.s) poster.appendChild(el(doc, "div", "hssTopSeason", tr(lang, "season") + it.s));
    c.appendChild(poster);
    if (entry.jf) return c;
    if (!it.x) {
      poster.appendChild(el(doc, "div", "hssTopBadge", tr(lang, "none")));
    } else {
      var res = el(doc, "div", "hssTopBadge", it.f);
      res.setAttribute("title", it.f + " " + tr(lang, "found"));
      poster.appendChild(res);
    }
    c.appendChild(actionsFor(doc, it, lang, tv, states));
    return c;
  }

  function rowKey(entries, lang, states) {
    return langOf(lang) + "|" + entries.map(function (e) {
      var st = states && states.entries && e.item.x ? states.entries[e.item.x] : null;
      return [e.item.k, e.item.id, e.item.s, e.item.r, e.jf ? e.jf.id : "", st ? st.state + st.badge : ""].join(":");
    }).join(",") + "|" + (states ? states.left + ":" + !!states.dailyFull : "");
  }

  // Jellyfin's .overflowPortraitCard widths (vw per min-width in px), so the posters match Hidden Gems; below 700 px the CSS default applies.
  var CARD_VW = [[2100, 9.3], [1920, 10.41], [1600, 11.6], [1400, 13.3], [1200, 15.5], [800, 18.5], [700, 23.1]];

  function fitPoster(viewport) {
    for (var i = 0; i < CARD_VW.length; i++) if (viewport >= CARD_VW[i][0]) return Math.round(CARD_VW[i][1] * viewport / 100);
    return null;
  }

  function measure(list) {
    var win = list.ownerDocument.defaultView;
    if (!win) return undefined;
    return fitPoster(win.innerWidth);
  }

  function applyFit(list, p) {
    if (p === undefined) return;
    if (p) list.style.setProperty("--hss-p", p + "px");
    else list.style.removeProperty("--hss-p");
  }

  function fitRow(list) {
    applyFit(list, measure(list));
  }

  // All reads first, then all writes: a write between two reads forces a full layout of the page per row.
  function fitAll(root) {
    var lists = root.querySelectorAll(".hssTopScroll");
    var sizes = [];
    for (var i = 0; i < lists.length; i++) sizes.push(measure(lists[i]));
    for (var j = 0; j < lists.length; j++) applyFit(lists[j], sizes[j]);
    return sizes[0];
  }

  // The page's rows all have the viewport's width: a size measured once per width is set before the page is attached (one layout instead of two).
  function prefit(root, memo, width) {
    if (!memo || memo.width !== width) return false;
    var lists = root.querySelectorAll(".hssTopScroll");
    for (var i = 0; i < lists.length; i++) applyFit(lists[i], memo.p);
    return true;
  }

  function fillRow(sec, entries, lang, tv, states) {
    var list = sec.querySelector(".hssTopScroll");
    while (list.firstChild) list.removeChild(list.firstChild);
    for (var i = 0; i < entries.length; i++) list.appendChild(card(sec.ownerDocument, entries[i], lang, tv, states));
    var title = sec.querySelector(".sectionTitleContainer");
    var legend = title && title.querySelector(".hssTopLegend");
    if (tv && title && list.querySelector(".hssTopBtn")) {
      if (!legend) legend = title.appendChild(el(sec.ownerDocument, "div", "hssTopLegend"));
      legend.textContent = ICON.night + " " + tr(lang, "tonightHint") + " \u00B7 " + ICON.now + " " + tr(lang, "nowHint");
    } else if (legend) legend.remove();
    if (sec.isConnected) fitRow(list);
  }

  var ROW = '<div class="sectionTitleContainer sectionTitleContainer-cards padded-left"><h2 class="sectionTitle sectionTitle-cards"></h2></div>' +
    '<div class="hssTopScroll focuscontainer-x"></div>';

  function syncHome(container, data, lib, lang, states) {
    var doc = container.ownerDocument;
    var tv = doc.documentElement.classList.contains("layout-tv");
    for (var i = 0; i < HOME.length; i++) {
      var h = HOME[i];
      var entries = visible(((data && data.home) || {})[h.key], lib);
      var sec = container.querySelector(".verticalSection." + h.id + "[data-hss-client]");
      if (!entries.length) {
        if (sec) sec.remove();
        continue;
      }
      var key = rowKey(entries, lang, states);
      if (sec && sec.getAttribute("data-hss-client") === key) continue;
      if (!sec) {
        sec = el(doc, "div", "verticalSection hssTopRow " + h.id);
        sec.innerHTML = ROW;
        var anchor = container.querySelector(".verticalSection." + h.after);
        if (anchor) sec.style.order = anchor.style.order;
        container.insertBefore(sec, anchor ? anchor.nextSibling : null);
      }
      sec.setAttribute("data-hss-client", key);
      sec.querySelector("h2").textContent = tr(lang, h.id);
      fillRow(sec, entries, lang, tv, states);
    }
  }

  // Slots for the home layout (empty-sections.js reads window.HssExtraRows): only rows with data.
  function extraRows(data) {
    var out = [];
    for (var i = 0; i < HOME.length; i++) {
      var h = HOME[i];
      if (!data || !data.home || !(data.home[h.key] || []).length) continue;
      out.push({ id: h.id, after: h.after, name: { en: TEXT.en[h.id], de: TEXT.de[h.id] } });
    }
    return out;
  }

  function navKind(kind) {
    return kind === "movie" ? NAV[0] : NAV[1];
  }

  function navSel(kind) {
    return '.hssPopularNav[data-hss-kind="' + kind + '"]';
  }

  function tagNav(e, n) {
    e.classList.add("hssPopularNav");
    e.setAttribute("data-hss-kind", n.kind);
    e.setAttribute("href", "#/home");
    return e;
  }

  function setLabel(e, label) {
    var t = e.querySelector(".navMenuOptionText, .MuiListItemText-primary");
    if (t) {
      if (t.textContent !== label) t.textContent = label;
    } else if (e.tagName === "BUTTON") {
      if (e.getAttribute("title") !== label) e.setAttribute("title", label);
      if (e.getAttribute("aria-label") !== label) e.setAttribute("aria-label", label);
    } else {
      var last = null;
      for (var n = 0; n < e.childNodes.length; n++) if (e.childNodes[n].nodeType === 3) last = e.childNodes[n];
      if (!last) last = e.appendChild(e.ownerDocument.createTextNode(""));
      if (last.nodeValue !== label) last.nodeValue = label;
    }
  }

  // One entry per kind in each place, in NAV order: a missing one goes after the previous kind's entry, else before the next kind's, else where place() puts the first.
  function ensure(scope, build, place, wrap) {
    wrap = wrap || function (e) { return e; };
    var prev = null;
    for (var i = 0; i < NAV.length; i++) {
      var have = scope.querySelector(navSel(NAV[i].kind));
      if (!have) {
        have = build(NAV[i]);
        var next = null;
        for (var j = i + 1; j < NAV.length && !next; j++) next = scope.querySelector(navSel(NAV[j].kind));
        if (prev) wrap(prev).parentNode.insertBefore(wrap(have), wrap(prev).nextSibling);
        else if (next) wrap(next).parentNode.insertBefore(wrap(have), wrap(next));
        else place(wrap(have));
      }
      prev = have;
    }
  }

  // The open page's entries take the real selected classes (the theme's own look); Jellyfin's selected entry is neutralised by CSS on the body class, which React re-renders cannot undo.
  function markNav(doc, open) {
    if (doc.body) doc.body.classList.toggle("hssPopularOpen", !!open);
    var all = doc.querySelectorAll(".hssPopularNav[data-hss-kind]");
    for (var i = 0; i < all.length; i++) {
      var e = all[i];
      if (e.tagName === "BUTTON") continue;
      var on = !!open && e.getAttribute("data-hss-kind") === open;
      if (e.closest(".MuiDrawer-paper, .MuiStack-root")) e.classList.toggle("Mui-selected", on);
      if (e.classList.contains("navMenuOption")) e.classList.toggle("navMenuOption-selected", on);
      if (on) e.setAttribute("aria-current", "page");
      else e.removeAttribute("aria-current");
    }
  }

  function navLinks(doc, lang, open) {
    var stale = doc.querySelectorAll(".hssPopularNav:not([data-hss-kind])");
    for (var s = 0; s < stale.length; s++) (stale[s].closest(".MuiDrawer-paper li") || stale[s]).remove();
    var bar = null;
    var links = doc.querySelectorAll(".MuiStack-root > a[href*='collectionType=tvshows']");
    if (links.length) bar = links[links.length - 1];
    if (bar) {
      ensure(bar.parentNode, function (n) {
        var a = bar.cloneNode(true);
        a.className = bar.className;
        var path = a.querySelector("svg path");
        if (path) path.setAttribute("d", TRENDING_PATH);
        for (var k = a.childNodes.length - 1; k >= 0; k--) if (a.childNodes[k].nodeType === 3) a.removeChild(a.childNodes[k]);
        return tagNav(a, n);
      }, function (a) { bar.parentNode.insertBefore(a, bar.nextSibling); });
    }
    var menu = doc.querySelector(".mainDrawer .customMenuOptions");
    if (menu) {
      ensure(menu, function (n) {
        var m = el(doc, "a", "navMenuOption lnkMediaFolder emby-button");
        m.innerHTML = '<span class="material-icons navMenuOptionIcon trending_up" aria-hidden="true"></span><span class="navMenuOptionText"></span>';
        return tagNav(m, n);
      }, function (m) { menu.appendChild(m); });
    }
    var favs = doc.querySelectorAll(".MuiDrawer-paper a[href='#/home?tab=1']");
    for (var f = 0; f < favs.length; f++) {
      var li = favs[f].closest("li");
      if (!li) continue;
      ensure(li.parentNode, function (n) {
        var copy = li.cloneNode(true);
        var link = copy.querySelector("a");
        link.classList.remove("Mui-selected");
        var icon = link.querySelector("svg path");
        if (icon) icon.setAttribute("d", TRENDING_PATH);
        tagNav(link, n);
        return link;
      }, function (item) { li.parentNode.insertBefore(item, li.nextSibling); }, function (link) { return link.closest("li"); });
    }
    var right = doc.documentElement.classList.contains("layout-tv") && doc.querySelector(".skinHeader .headerRight");
    if (right) {
      ensure(right, function (n) {
        var b = el(doc, "button", "headerButton headerButtonRight paper-icon-button-light");
        b.setAttribute("type", "button");
        b.innerHTML = '<span class="material-icons ' + n.icon + '" aria-hidden="true"></span>';
        tagNav(b, n);
        b.removeAttribute("href");
        return b;
      }, function (b) { right.insertBefore(b, right.querySelector(".headerSearchButton")); });
    }
    var all = doc.querySelectorAll(".hssPopularNav[data-hss-kind]");
    for (var i = 0; i < all.length; i++) setLabel(all[i], tr(lang, navKind(all[i].getAttribute("data-hss-kind")).label));
    markNav(doc, open || null);
  }

  // kind "movie" or "tv": the matching Netflix Top 10, then the rows of that kind (rows without a kind, published before the split, on both pages).
  function buildPage(doc, data, lib, lang, tv, states, kind) {
    var nav = navKind(kind);
    var page = el(doc, "div", "hssPopularPage focuscontainer");
    page.setAttribute("data-hss-kind", nav.kind);
    var head = el(doc, "div", "hssPopularHead padded-left padded-right");
    head.appendChild(el(doc, "h1", "sectionTitle", tr(lang, nav.label)));
    var close = el(doc, "button", "hssPopularClose paper-icon-button-light");
    close.setAttribute("type", "button");
    close.setAttribute("aria-label", tr(lang, "close"));
    close.innerHTML = '<span class="material-icons close" aria-hidden="true"></span>';
    head.appendChild(close);
    page.appendChild(head);
    var rows = HOME.filter(function (h) { return h.kind === nav.kind; })
      .map(function (h) { return { id: h.id, name: { en: TEXT.en[h.id], de: TEXT.de[h.id] }, items: ((data && data.home) || {})[h.key] }; })
      .concat(((data && data.rows) || []).filter(function (r) { return !r.kind || r.kind === nav.kind; }));
    for (var i = 0; i < rows.length; i++) {
      var entries = visible(rows[i].items, lib);
      if (!entries.length) continue;
      var sec = el(doc, "div", "verticalSection hssTopRow");
      sec.innerHTML = ROW;
      sec.setAttribute("data-hss-row", rows[i].id);
      var h2 = sec.querySelector("h2");
      if (rows[i].logo && /^\/[A-Za-z0-9_.-]+$/.test(rows[i].logo)) {
        var img = el(doc, "img", "hssTopLogo");
        img.setAttribute("src", TMDB_IMG + "w92" + rows[i].logo);
        img.setAttribute("alt", "");
        h2.parentNode.insertBefore(img, h2);
      }
      h2.textContent = (rows[i].name && (rows[i].name[langOf(lang)] || rows[i].name.en)) || "";
      fillRow(sec, entries, lang, tv, states);
      page.appendChild(sec);
    }
    if (page.querySelectorAll(".hssTopRow").length === 0) page.appendChild(el(doc, "div", "padded-left", tr(lang, "empty")));
    page.appendChild(el(doc, "div", "hssPopularSource padded-left padded-right", tr(lang, "source")));
    return page;
  }

  function request(win, api, x, mode) {
    return win.fetch(api.getUrl(API + "request"), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Emby-Token": api.accessToken() },
      body: JSON.stringify({ id: x, mode: mode })
    }).then(function (r) {
      return r.json().then(function (b) { return { status: r.status, body: b }; }, function () { return { status: r.status, body: {} }; });
    });
  }

  var CSS = [
    ".hssTopScroll { --hss-p: 7rem; display: flex; flex-wrap: nowrap; align-items: flex-start; overflow-x: auto; overflow-y: hidden; gap: 0.9em; padding: 0.6em 3.3% 1.6em max(3.3%, 1.2em); scroll-snap-type: x proximity; scrollbar-width: thin; }",
    ".hssTopCard { position: relative; flex: 0 0 auto; display: grid; grid-template-columns: auto var(--hss-p); align-items: end; color: inherit; text-decoration: none; scroll-snap-align: start; outline: none; }",
    ".hssTopRank { grid-column: 1; grid-row: 1; width: calc(var(--hss-p) * 0.75); margin-right: calc(var(--hss-p) * -0.25); text-align: right; white-space: nowrap; font: 900 calc(var(--hss-p) * 1.15)/0.8 'Arial Black', 'Helvetica Neue', Arial, sans-serif; letter-spacing: -0.06em; color: transparent; -webkit-text-stroke: 0.03em rgba(255, 255, 255, 0.78); position: relative; z-index: 0; user-select: none; }",
    ".hssTopPoster { grid-column: 2; grid-row: 1; position: relative; z-index: 1; width: var(--hss-p); aspect-ratio: 2 / 3; border-radius: 0.35em; background: #22252c center / cover no-repeat; box-shadow: -0.4em 0 1em rgba(0, 0, 0, 0.55); overflow: hidden; transition: transform 0.15s; }",
    ".hssTopCard:hover .hssTopPoster, .hssTopCard:focus .hssTopPoster, .hssTopCard:focus-within .hssTopPoster { transform: scale(1.04); box-shadow: 0 0 0 0.15em #00a4dc, -0.4em 0 1em rgba(0, 0, 0, 0.55); }",
    ".hssTopDim .hssTopPoster { filter: saturate(0.55) brightness(0.62); }",
    ".hssTopDim:hover .hssTopPoster, .hssTopDim:focus-within .hssTopPoster { filter: saturate(0.8) brightness(0.85); }",
    ".hssTopNone .hssTopPoster, .hssTopNone:hover .hssTopPoster, .hssTopNone:focus-within .hssTopPoster { filter: none; }",
    ".hssTopNone .hssTopPoster::before { content: ''; position: absolute; inset: 0; background: inherit; filter: grayscale(1) brightness(0.45); }",
    ".hssTopNone .hssTopPoster::after { content: ''; position: absolute; inset: 0; z-index: 1; pointer-events: none; background: linear-gradient(to top right, transparent calc(50% - 0.2em), rgba(229, 57, 53, 0.85) calc(50% - 0.18em), rgba(229, 57, 53, 0.85) calc(50% + 0.18em), transparent calc(50% + 0.2em)); }",
    ".hssTopNone .hssTopPoster > * { z-index: 2; }",
    ".hssTopNone .hssTopRank { -webkit-text-stroke-color: rgba(255, 255, 255, 0.4); }",
    ".hssTopRank-2 { width: calc(var(--hss-p) * 0.85); margin-right: calc(var(--hss-p) * -0.05); font-size: calc(var(--hss-p) * 0.8); letter-spacing: -0.1em; }",
    ".hssTopBadge, .hssTopSeason { position: absolute; top: 0.45em; font-size: 0.72em; font-weight: 600; padding: 0.15em 0.5em; border-radius: 1em; background: rgba(0, 0, 0, 0.78); color: #fff; }",
    ".hssTopBadge { left: 0.45em; } .hssTopSeason { right: 0.45em; }",
    "button.hssTopPoster { display: block; margin: 0; padding: 0; border: 0; font: inherit; color: inherit; text-align: inherit; cursor: pointer; outline: none; }",
    ".hssTopNoPoster { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; text-align: center; padding: 0.5em; font-weight: 700; }",
    ".hssTopActions { grid-column: 2; grid-row: 2; display: flex; flex-wrap: wrap; gap: 0.35em; margin-top: 0.45em; width: var(--hss-p); }",
    ".hssTopBtn { flex: 1 1 auto; font: inherit; font-size: 0.78em; padding: 0.35em 0.4em; border-radius: 0.3em; border: 1px solid rgba(255, 255, 255, 0.25); background: rgba(255, 255, 255, 0.08); color: #fff; cursor: pointer; white-space: nowrap; }",
    ".hssTopBtn:hover, .hssTopBtn:focus { background: #00a4dc; border-color: #00a4dc; outline: none; }",
    ".hssTopBtn[disabled] { opacity: 0.4; cursor: default; background: none; }",
    ".hssTopState, .hssTopHint { font-size: 0.78em; opacity: 0.85; }",
    ".hssTopLogo { height: 1.6em; width: auto; margin-right: 0.5em; vertical-align: middle; border-radius: 0.25em; }",
    ".hssTopRow .sectionTitleContainer { display: flex; align-items: center; }",
    // Below the header (its nav stays usable; a nav click changes the route and closes the page).
    ".hssPopularPage { position: fixed; top: 0; right: 0; bottom: 0; left: 0; z-index: 999; overflow-y: auto; background: #101010; padding: 4.6em 0 3em; }",
    ".layout-tv .hssPopularPage { padding-top: 6.5em; }",
    ".hssPopularHead { display: flex; align-items: center; justify-content: space-between; }",
    ".hssPopularHead h1 { margin: 0.3em 0; font-size: 1.8em; }",
    ".hssPopularSource { margin-top: 2em; font-size: 0.8em; opacity: 0.55; }",
    ".hssTopBtn { min-width: 0; overflow: hidden; text-overflow: ellipsis; }",
    ".hssTopBtn-icon { flex: 1 1 0; font-size: 1.05em; padding: 0.3em 0; text-align: center; }",
    ".hssTopLegend { margin-left: 1.2em; font-size: 0.9em; opacity: 0.7; }",
    "body.hssPopularOpen .MuiDrawer-paper .Mui-selected:not(.hssPopularNav) { background-color: transparent !important; }",
    "body.hssPopularOpen .MuiStack-root > .Mui-selected:not(.hssPopularNav) { background-color: transparent !important; }",
    "body.hssPopularOpen .navMenuOption-selected:not(.hssPopularNav) { background: transparent !important; color: inherit !important; }"
  ].join("\n");

  function styles(doc) {
    if (doc.getElementById("hss-popular-style")) return;
    var s = doc.createElement("style");
    s.id = "hss-popular-style";
    s.textContent = CSS;
    (doc.head || doc.documentElement).appendChild(s);
  }

  var BACK_KEYS = { Escape: 1, Esc: 1, Backspace: 1, GoBack: 1, BrowserBack: 1 };
  var BACK_CODES = { 27: 1, 8: 1, 461: 1, 10009: 1 };
  var LIB_TTL_MS = 300000;
  var STATE_TTL_MS = 60000;

  function start(win) {
    var doc = win.document;
    var data = null;
    styles(doc);
    var lib = null, libAt = 0, libUser = null, libLoading = null;
    var states = null, statesAt = 0, statesLoading = null;
    var timer = null;
    var fitMemo = null;

    function lang() { return doc.documentElement.lang || win.navigator.language || "en"; }
    function tv() { return doc.documentElement.classList.contains("layout-tv"); }

    function loadLib(api) {
      var uid = api.getCurrentUserId();
      if (lib && libUser === uid && Date.now() - libAt < LIB_TTL_MS) return Promise.resolve(lib);
      if (libLoading) return libLoading;
      libLoading = api.getJSON(api.getUrl("Users/" + uid + "/Items", { Recursive: true, IncludeItemTypes: "Movie,Series", Fields: "ProviderIds", EnableImages: false, EnableUserData: false }))
        .then(function (r) { lib = libraryMap((r && r.Items) || []); libAt = Date.now(); libUser = uid; libLoading = null; return lib; },
          function () { libLoading = null; return lib || {}; });
      return libLoading;
    }

    function loadStates(api, force) {
      if (!force && states && Date.now() - statesAt < STATE_TTL_MS) return Promise.resolve(states);
      if (statesLoading) return statesLoading;
      statesLoading = win.fetch(api.getUrl(API + "state"), { headers: { "X-Emby-Token": api.accessToken() } })
        .then(function (r) { return r.status === 200 ? r.json() : null; })
        .then(function (s) { states = s; statesAt = Date.now(); statesLoading = null; return s; }, function () { statesLoading = null; return states; });
      return statesLoading;
    }

    function api() {
      var a = win.ApiClient;
      return a && a.getCurrentUserId && a.getCurrentUserId() ? a : null;
    }

    function paintHome() {
      var a = api();
      if (!a) return;
      var containers = doc.querySelectorAll(".homeSectionsContainer");
      var live = [];
      // Jellyfin keeps visited pages in the DOM: only the home page on screen, so other pages trigger no work and no requests.
      for (var i = 0; i < containers.length; i++) if (!containers[i].closest(".hide") && containers[i].querySelector(".verticalSection[data-page]")) live.push(containers[i]);
      if (!live.length) return;
      loadLib(a).then(function (l) {
        live.forEach(function (c) { syncHome(c, data, l, lang(), states); });
        return loadStates(a, false).then(function () { live.forEach(function (c) { if (c.isConnected) syncHome(c, data, l, lang(), states); }); });
      });
    }

    function repaintPage() {
      var page = doc.querySelector(".hssPopularPage");
      if (!page || !lib || page.hssStates === states) return;
      var y = page.scrollTop;
      var sel = "a.hssTopCard, button";
      var at = Array.prototype.indexOf.call(page.querySelectorAll(sel), doc.activeElement);
      var next = buildPage(doc, data, lib, lang(), tv(), states, page.getAttribute("data-hss-kind"));
      next.hssStates = states;
      var known = prefit(next, fitMemo, win.innerWidth);
      page.replaceWith(next);
      if (!known) fitMemo = { width: win.innerWidth, p: fitAll(next) };
      next.scrollTop = y;
      next.hssFrom = page.hssFrom;
      var again = next.querySelectorAll(sel);
      if (at >= 0 && again[at]) again[at].focus();
      else if (tv() && next.querySelector("a.hssTopCard, .hssTopOpen, .hssTopBtn")) next.querySelector("a.hssTopCard, .hssTopOpen, .hssTopBtn").focus();
    }

    // A double tap or a close while the library loads: only the latest open still attaches a page.
    var opening = 0;

    function openPage(from) {
      var a = api();
      if (!a) return;
      closePage(false);
      var kind = from.getAttribute("data-hss-kind");
      var mine = ++opening;
      loadLib(a).then(function (l) {
        if (mine !== opening) return;
        var page = buildPage(doc, data, l, lang(), tv(), states, kind);
        page.hssStates = states;
        page.hssFrom = from;
        openedAt = win.location.href;
        var known = prefit(page, fitMemo, win.innerWidth);
        doc.body.appendChild(page);
        if (!known) fitMemo = { width: win.innerWidth, p: fitAll(page) };
        markNav(doc, kind);
        if (tv()) { var f = page.querySelector("a.hssTopCard, .hssTopOpen, .hssTopBtn"); if (f) f.focus(); }
        loadStates(a, false).then(repaintPage);
      });
    }

    function closePage(refocus) {
      opening++;
      var page = doc.querySelector(".hssPopularPage");
      if (!page) return false;
      page.remove();
      markNav(doc, null);
      if (refocus && page.hssFrom && page.hssFrom.isConnected) page.hssFrom.focus();
      return true;
    }

    // Details overlay (details.js) on top of the page or home: the same request buttons, full labels.
    function openDetails(btn) {
      var it = btn.hssItem;
      var d = win.HssDetails;
      if (!it || !d) return;
      var l = lang();
      d.open({
        id: it.d || null, key: btn.getAttribute("data-hss-key"), from: btn,
        fallback: { title: titleOf(it, l), year: it.y, poster: posterUrl(it, l) },
        line: it.k === "tv" && it.s ? tr(l, "seasonLong", { n: it.s }) : null,
        actions: function (doc) { return actionsFor(doc, it, lang(), false, states); }
      });
    }

    function detailsOpen() {
      return !!(win.HssDetails && win.HssDetails.isOpen && win.HssDetails.isOpen());
    }

    function press(btn) {
      var a = api();
      if (!a || btn.disabled) return;
      var row = btn.closest(".hssTopActions");
      var buttons = row.querySelectorAll("button");
      for (var i = 0; i < buttons.length; i++) buttons[i].disabled = true;
      request(win, a, btn.getAttribute("data-hss-x"), btn.getAttribute("data-hss-mode")).then(function (r) {
        if (r.status !== 200 && r.status !== 409 && r.status !== 429) throw new Error(String(r.status));
        return loadStates(a, true);
      }).then(function () { repaintPage(); paintHome(); if (win.HssDetails) win.HssDetails.refresh(); }, function () {
        for (var i = 0; i < buttons.length; i++) buttons[i].disabled = false;
        var hint = row.querySelector(".hssTopHint") || row.appendChild(el(doc, "div", "hssTopHint"));
        hint.textContent = tr(lang(), "failed");
      });
    }

    doc.addEventListener("click", function (e) {
      var t = e.target && e.target.closest ? e.target : null;
      if (!t) return;
      var nav = t.closest(".hssPopularNav");
      if (nav) {
        e.preventDefault();
        e.stopPropagation();
        var drawer = doc.querySelector(".mainDrawer");
        if (drawer && nav.closest(".mainDrawer") && win.Emby && win.Emby.Page) { try { win.Emby.Page.closeDrawer && win.Emby.Page.closeDrawer(); } catch (err) {} }
        var backdrop = nav.closest(".MuiDrawer-root") && nav.closest(".MuiDrawer-root").querySelector(".MuiBackdrop-root");
        if (backdrop) backdrop.click();
        openPage(nav);
        return;
      }
      var btn = t.closest(".hssTopBtn");
      if (btn) {
        e.preventDefault();
        e.stopPropagation();
        press(btn);
        return;
      }
      var opener = t.closest(".hssTopOpen");
      if (opener) {
        e.preventDefault();
        e.stopPropagation();
        openDetails(opener);
        return;
      }
      if (t.closest(".hssDetails")) return;
      if (t.closest(".hssPopularClose")) closePage(true);
      else if (t.closest(".hssPopularPage a.hssTopCard")) win.setTimeout(function () { closePage(false); }, 0);
      // The logo leads to the route that is already open (#/ redirects to #/home): no route change to close on.
      else if (!t.closest(".hssPopularPage") && t.closest("a[href], .headerHomeButton, .headerBackButton, .emby-tab-button")) closePage(false);
    }, true);
    win.addEventListener("keydown", function (e) {
      if (!doc.querySelector(".hssPopularPage") || detailsOpen()) return;
      if (!BACK_KEYS[e.key] && !BACK_CODES[e.keyCode]) return;
      if (e.target && /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      closePage(true);
    }, true);
    win.addEventListener("hashchange", function () { closePage(false); });
    var resizing = null;
    win.addEventListener("resize", function () {
      if (resizing) win.clearTimeout(resizing);
      resizing = win.setTimeout(function () { resizing = null; fitAll(doc); }, 200);
    });

    // The data entry may load after this script: picked up on the next change.
    function register() {
      if (data || !win.HssPopular) return !!data;
      data = win.HssPopular;
      win.HssExtraRows = (win.HssExtraRows || []).filter(function (r) { return HOME.every(function (h) { return h.id !== r.id; }); }).concat(extraRows(data));
      return true;
    }

    // Jellyfin's router changes the hash with pushState (no hashchange event): a route change seen here closes the page.
    var openedAt = null;

    function tick() {
      timer = null;
      if (openedAt && openedAt !== win.location.href) { openedAt = null; closePage(false); }
      if (!register()) return;
      observer.disconnect();
      try {
        var open = doc.querySelector(".hssPopularPage");
        navLinks(doc, lang(), open && open.getAttribute("data-hss-kind"));
      } finally {
        watch();
      }
      paintHome();
    }

    function watch() {
      observer.observe(doc.body, { childList: true, subtree: true });
    }

    var observer = new win.MutationObserver(function () {
      if (!timer) timer = win.setTimeout(tick, 300);
    });
    watch();
    tick();
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { CSS: CSS, actionsFor: actionsFor, detailKey: detailKey, fitPoster: fitPoster, fitAll: fitAll, libraryMap: libraryMap, visible: visible, card: card, fillRow: fillRow, prefit: prefit, posterUrl: posterUrl, syncHome: syncHome, extraRows: extraRows, navLinks: navLinks, buildPage: buildPage, request: request, start: start };
  } else if (window.MutationObserver && document.body) {
    start(window);
  } else if (window.MutationObserver) {
    document.addEventListener("DOMContentLoaded", function () { start(window); });
  }
})();
