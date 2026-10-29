(function () {
  // "Currently popular" page, its nav entries and the home rows Top 10 Shows / Movies in Switzerland (Jellyfin README "Currently popular").
  // Data: window.HssPopular from scripts/popular.py; the user's own library and requests through their own session.
  var API = "hss-popular/";
  var TMDB_IMG = "https://image.tmdb.org/t/p/";
  var HOME = [
    { id: "Top10Shows", key: "shows", after: "RecentlyAddedShows" },
    { id: "Top10Movies", key: "movies", after: "RecentlyWatchedSeries" }
  ];
  var TEXT = {
    en: {
      Top10Shows: "Top 10 Shows in Switzerland", Top10Movies: "Top 10 Movies in Switzerland", page: "Currently popular", close: "Close",
      tonight: "🌙 Tonight", now: "⚡ Now", tonightHint: "Download tonight (cheaper power)", nowHint: "Download now (daytime power price)",
      limit: "Limit reached: {n} requests per {d} days", left: "{n} requests left this week", failed: "Request failed", season: "S",
      night: "🌙 Tonight", searching: "🔍 Searching", unreleased: "📅 Not released yet", downloading: "⬇️ Downloading", requested: "✓ Requested", declined: "Declined",
      empty: "Nothing on the list right now.", found: "available",
      source: "Sources: Netflix Top 10 Switzerland (weekly), TMDB popularity, where to stream in Switzerland by JustWatch (via TMDB). Only titles in the library or with a release found are shown."
    },
    de: {
      Top10Shows: "Top 10 Serien in der Schweiz", Top10Movies: "Top 10 Filme in der Schweiz", page: "Gerade beliebt", close: "Schliessen",
      tonight: "🌙 Heute Nacht", now: "⚡ Jetzt", tonightHint: "Heute Nacht herunterladen (günstigerer Strom)", nowHint: "Jetzt herunterladen (Tagesstrompreis)",
      limit: "Limite erreicht: {n} Anfragen pro {d} Tage", left: "Noch {n} Anfragen diese Woche", failed: "Anfrage fehlgeschlagen", season: "S",
      night: "🌙 Heute Nacht", searching: "🔍 Wird gesucht", unreleased: "📅 Noch nicht erschienen", downloading: "⬇️ Lädt herunter", requested: "✓ Angefragt", declined: "Abgelehnt",
      empty: "Gerade nichts auf der Liste.", found: "verfügbar",
      source: "Quellen: Netflix Top 10 Schweiz (wöchentlich), TMDB-Beliebtheit, Streaming-Angebot in der Schweiz von JustWatch (über TMDB). Nur Titel aus der Bibliothek oder mit gefundenem Release."
    }
  };
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

  // Titles the user has (their own library view) or that can be requested; owned elsewhere but not visible to this user = hidden.
  function visible(items, lib) {
    var out = [];
    for (var i = 0; i < (items || []).length; i++) {
      var it = items[i];
      var jf = (lib || {})[it.k + "/" + it.id] || null;
      if (jf || (it.f && it.x)) out.push({ item: it, jf: jf });
    }
    return out;
  }

  function el(doc, tag, cls, text) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
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
      c = el(doc, "div", "hssTopCard hssTopDim");
    }
    c.setAttribute("aria-label", it.r + ". " + name);
    c.setAttribute("title", name + (it.y ? " (" + it.y + ")" : ""));
    var rank = el(doc, "div", "hssTopRank", String(it.r));
    rank.setAttribute("aria-hidden", "true");
    c.appendChild(rank);
    var poster = el(doc, "div", "hssTopPoster");
    var url = posterUrl(it, lang);
    if (url) poster.style.backgroundImage = 'url("' + url + '")';
    else poster.appendChild(el(doc, "div", "hssTopNoPoster", name));
    if (it.k === "tv" && it.s) poster.appendChild(el(doc, "div", "hssTopSeason", tr(lang, "season") + it.s));
    c.appendChild(poster);
    if (entry.jf) return c;
    poster.appendChild(el(doc, "div", "hssTopBadge", it.f + " " + tr(lang, "found")));
    var actions = el(doc, "div", "hssTopActions");
    c.appendChild(actions);
    var st = states && states.entries ? states.entries[it.x] : null;
    if (st && st.state !== "requestable") {
      var label = st.state === "requested" ? tr(lang, st.badge || "requested") : st.state === "declined" ? tr(lang, "declined") : tr(lang, "requested");
      actions.appendChild(el(doc, "div", "hssTopState", label));
      return c;
    }
    var full = states && typeof states.left === "number" && states.left <= 0;
    ["night", "now"].forEach(function (mode) {
      var b = el(doc, "button", "hssTopBtn hssTopBtn-" + mode, tr(lang, mode === "night" ? "tonight" : "now"));
      b.setAttribute("type", "button");
      b.setAttribute("data-hss-x", it.x);
      b.setAttribute("data-hss-mode", mode);
      b.setAttribute("title", tr(lang, mode === "night" ? "tonightHint" : "nowHint"));
      b.setAttribute("aria-label", name + ": " + tr(lang, mode === "night" ? "tonightHint" : "nowHint"));
      if (full) b.disabled = true;
      actions.appendChild(b);
    });
    if (full) actions.appendChild(el(doc, "div", "hssTopHint", tr(lang, "limit", { n: states.limit, d: states.days })));
    return c;
  }

  function rowKey(entries, lang, states) {
    return langOf(lang) + "|" + entries.map(function (e) {
      var st = states && states.entries && e.item.x ? states.entries[e.item.x] : null;
      return [e.item.k, e.item.id, e.item.s, e.item.r, e.jf ? e.jf.id : "", st ? st.state + st.badge : ""].join(":");
    }).join(",") + "|" + (states ? states.left : "");
  }

  function fillRow(sec, entries, lang, tv, states) {
    var list = sec.querySelector(".itemsContainer");
    while (list.firstChild) list.removeChild(list.firstChild);
    for (var i = 0; i < entries.length; i++) list.appendChild(card(sec.ownerDocument, entries[i], lang, tv, states));
  }

  var ROW = '<div class="sectionTitleContainer sectionTitleContainer-cards padded-left"><h2 class="sectionTitle sectionTitle-cards"></h2></div>' +
    '<div class="itemsContainer hssTopScroll focuscontainer-x"></div>';

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

  function navLinks(doc, lang) {
    var label = tr(lang, "page");
    var bar = null;
    var links = doc.querySelectorAll(".MuiStack-root > a[href*='collectionType=tvshows']");
    if (links.length) bar = links[links.length - 1];
    if (bar && !bar.parentNode.querySelector(".hssPopularNav")) {
      var a = bar.cloneNode(true);
      a.className = bar.className + " hssPopularNav";
      a.setAttribute("href", "#/home");
      var path = a.querySelector("svg path");
      if (path) path.setAttribute("d", TRENDING_PATH);
      for (var n = a.childNodes.length - 1; n >= 0; n--) if (a.childNodes[n].nodeType === 3) a.removeChild(a.childNodes[n]);
      a.appendChild(doc.createTextNode(label));
      bar.parentNode.insertBefore(a, bar.nextSibling);
    }
    var menu = doc.querySelector(".mainDrawer .customMenuOptions");
    if (menu && !menu.querySelector(".hssPopularNav")) {
      var m = el(doc, "a", "navMenuOption lnkMediaFolder emby-button hssPopularNav");
      m.setAttribute("href", "#/home");
      m.innerHTML = '<span class="material-icons navMenuOptionIcon trending_up" aria-hidden="true"></span><span class="navMenuOptionText"></span>';
      m.querySelector(".navMenuOptionText").textContent = label;
      menu.appendChild(m);
    }
    var favs = doc.querySelectorAll(".MuiDrawer-paper a[href='#/home?tab=1']");
    for (var f = 0; f < favs.length; f++) {
      var li = favs[f].closest("li");
      if (!li || li.parentNode.querySelector(".hssPopularNav")) continue;
      var copy = li.cloneNode(true);
      var link = copy.querySelector("a");
      link.classList.remove("Mui-selected");
      link.classList.add("hssPopularNav");
      link.setAttribute("href", "#/home");
      var icon = link.querySelector("svg path");
      if (icon) icon.setAttribute("d", TRENDING_PATH);
      var text = link.querySelector(".MuiListItemText-primary");
      if (text) text.textContent = label;
      li.parentNode.insertBefore(copy, li.nextSibling);
    }
    var right = doc.documentElement.classList.contains("layout-tv") && doc.querySelector(".skinHeader .headerRight");
    if (right && !right.querySelector(".hssPopularNav")) {
      var b = el(doc, "button", "headerButton headerButtonRight paper-icon-button-light hssPopularNav");
      b.setAttribute("type", "button");
      b.setAttribute("title", label);
      b.setAttribute("aria-label", label);
      b.innerHTML = '<span class="material-icons trending_up" aria-hidden="true"></span>';
      right.insertBefore(b, right.querySelector(".headerSearchButton"));
    }
    var all = doc.querySelectorAll(".hssPopularNav");
    for (var i = 0; i < all.length; i++) {
      var txt = all[i].querySelector(".navMenuOptionText");
      if (txt && txt.textContent !== label) txt.textContent = label;
    }
  }

  function buildPage(doc, data, lib, lang, tv, states) {
    var page = el(doc, "div", "hssPopularPage focuscontainer");
    var head = el(doc, "div", "hssPopularHead padded-left padded-right");
    head.appendChild(el(doc, "h1", "sectionTitle", tr(lang, "page")));
    var close = el(doc, "button", "hssPopularClose paper-icon-button-light");
    close.setAttribute("type", "button");
    close.setAttribute("aria-label", tr(lang, "close"));
    close.innerHTML = '<span class="material-icons close" aria-hidden="true"></span>';
    head.appendChild(close);
    page.appendChild(head);
    var rows = HOME.map(function (h) { return { id: h.id, name: { en: TEXT.en[h.id], de: TEXT.de[h.id] }, items: ((data && data.home) || {})[h.key] }; }).concat((data && data.rows) || []);
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
    ".hssTopScroll { display: flex; flex-wrap: nowrap; overflow-x: auto; overflow-y: hidden; gap: 1.6em; padding: 0.6em 3.3% 1.6em max(3.3%, 1.2em); scroll-snap-type: x proximity; scrollbar-width: thin; }",
    ".hssTopCard { --hss-p: clamp(7.2em, 10.5vw, 11.5em); position: relative; flex: 0 0 auto; display: grid; grid-template-columns: auto var(--hss-p); align-items: end; color: inherit; text-decoration: none; scroll-snap-align: start; outline: none; }",
    ".hssTopRank { grid-column: 1; grid-row: 1; font: 900 calc(var(--hss-p) * 1.32)/0.78 'Arial Black', 'Helvetica Neue', Arial, sans-serif; letter-spacing: -0.09em; color: transparent; -webkit-text-stroke: 0.028em rgba(255, 255, 255, 0.78); margin-right: -0.2em; padding-left: 0.06em; position: relative; z-index: 0; user-select: none; }",
    ".hssTopPoster { grid-column: 2; grid-row: 1; position: relative; z-index: 1; width: var(--hss-p); aspect-ratio: 2 / 3; border-radius: 0.35em; background: #22252c center / cover no-repeat; box-shadow: -0.4em 0 1em rgba(0, 0, 0, 0.55); overflow: hidden; transition: transform 0.15s; }",
    ".hssTopCard:hover .hssTopPoster, .hssTopCard:focus .hssTopPoster, .hssTopCard:focus-within .hssTopPoster { transform: scale(1.04); box-shadow: 0 0 0 0.15em #00a4dc, -0.4em 0 1em rgba(0, 0, 0, 0.55); }",
    ".hssTopDim .hssTopPoster { filter: saturate(0.55) brightness(0.62); }",
    ".hssTopDim:hover .hssTopPoster, .hssTopDim:focus-within .hssTopPoster { filter: saturate(0.8) brightness(0.85); }",
    ".hssTopBadge, .hssTopSeason { position: absolute; top: 0.45em; font-size: 0.72em; font-weight: 600; padding: 0.15em 0.5em; border-radius: 1em; background: rgba(0, 0, 0, 0.78); color: #fff; }",
    ".hssTopBadge { left: 0.45em; } .hssTopSeason { right: 0.45em; }",
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
    ".layout-tv .hssTopCard { --hss-p: 10em; } .layout-mobile .hssTopCard { --hss-p: 7em; }"
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
      for (var i = 0; i < containers.length; i++) if (containers[i].querySelector(".verticalSection[data-page]")) live.push(containers[i]);
      if (!live.length) return;
      loadLib(a).then(function (l) {
        live.forEach(function (c) { syncHome(c, data, l, lang(), states); });
        return loadStates(a, false).then(function () { live.forEach(function (c) { if (c.isConnected) syncHome(c, data, l, lang(), states); }); });
      });
    }

    function repaintPage() {
      var page = doc.querySelector(".hssPopularPage");
      if (!page || !lib) return;
      var y = page.scrollTop;
      var sel = "a.hssTopCard, button";
      var at = Array.prototype.indexOf.call(page.querySelectorAll(sel), doc.activeElement);
      var next = buildPage(doc, data, lib, lang(), tv(), states);
      page.replaceWith(next);
      next.scrollTop = y;
      next.hssFrom = page.hssFrom;
      var again = next.querySelectorAll(sel);
      if (at >= 0 && again[at]) again[at].focus();
      else if (tv() && next.querySelector("a.hssTopCard, .hssTopBtn")) next.querySelector("a.hssTopCard, .hssTopBtn").focus();
    }

    function openPage(from) {
      var a = api();
      if (!a) return;
      closePage(false);
      loadLib(a).then(function (l) {
        var page = buildPage(doc, data, l, lang(), tv(), states);
        page.hssFrom = from;
        doc.body.appendChild(page);
        if (tv()) { var f = page.querySelector("a.hssTopCard, .hssTopBtn"); if (f) f.focus(); }
        loadStates(a, false).then(repaintPage);
      });
    }

    function closePage(refocus) {
      var page = doc.querySelector(".hssPopularPage");
      if (!page) return false;
      page.remove();
      if (refocus && page.hssFrom && page.hssFrom.isConnected) page.hssFrom.focus();
      return true;
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
      }).then(function () { repaintPage(); paintHome(); }, function () {
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
      if (t.closest(".hssPopularClose")) closePage(true);
      else if (t.closest(".hssPopularPage a.hssTopCard")) win.setTimeout(function () { closePage(false); }, 0);
    }, true);
    win.addEventListener("keydown", function (e) {
      if (!doc.querySelector(".hssPopularPage")) return;
      if (!BACK_KEYS[e.key] && !BACK_CODES[e.keyCode]) return;
      if (e.target && /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      closePage(true);
    }, true);
    win.addEventListener("hashchange", function () { closePage(false); });

    // The data entry may load after this script: picked up on the next change.
    function register() {
      if (data || !win.HssPopular) return !!data;
      data = win.HssPopular;
      win.HssExtraRows = (win.HssExtraRows || []).filter(function (r) { return HOME.every(function (h) { return h.id !== r.id; }); }).concat(extraRows(data));
      return true;
    }

    function tick() {
      timer = null;
      if (!register()) return;
      observer.disconnect();
      try {
        navLinks(doc, lang());
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
    module.exports = { libraryMap: libraryMap, visible: visible, card: card, posterUrl: posterUrl, syncHome: syncHome, extraRows: extraRows, navLinks: navLinks, buildPage: buildPage, request: request, start: start };
  } else if (window.MutationObserver && document.body) {
    start(window);
  } else if (window.MutationObserver) {
    document.addEventListener("DOMContentLoaded", function () { start(window); });
  }
})();
