(function () {
  var SETTLE_MS = 1200;
  // The TV layout's clock changes the DOM every second, so a plain debounce would never fire there.
  var MAX_WAIT_MS = 2500;
  var CACHE_MS = 60000;
  var HINTS = {
    en: {
      _: "Nothing here yet.",
      ContinueWatching: "Nothing to continue — start something to see it here.",
      NextUp: "No series in progress yet.",
      RecentlyWatchedMovies: "Nothing here yet — films you finish show up here.",
      RecentlyWatchedSeries: "Nothing here yet — series you finish completely and complete film collections show up here.",
      BecauseYouWatched: "Nothing here yet — finish a film to get recommendations.",
      HiddenGems: "No highly rated unwatched titles right now.",
      FinishTheseCollections: "No film collection started yet.",
      MyList: "Nothing here yet — mark films or series with ♥ to see them here.",
      CollectionsSection: "No film collections yet.",
      Studio: "Nothing here yet.",
      Starring: "Nothing here yet — watch a few titles to get actor picks.",
      DirectedBy: "Nothing here yet — watch a few films to get director picks.",
      UpcomingShows: "No episodes airing in the next month.",
      UpcomingMovies: "No films releasing in the next months."
    },
    de: {
      _: "Noch nichts hier.",
      ContinueWatching: "Nichts fortzusetzen – starte etwas, dann erscheint es hier.",
      NextUp: "Noch keine angefangene Serie.",
      RecentlyWatchedMovies: "Noch nichts hier – Filme, die du fertig schaust, erscheinen hier.",
      RecentlyWatchedSeries: "Noch nichts hier – Serien, die du ganz fertig schaust, und komplett geschaute Filmreihen erscheinen hier.",
      BecauseYouWatched: "Noch nichts hier – schau einen Film fertig, dann gibt es Empfehlungen.",
      HiddenGems: "Gerade keine hoch bewerteten, ungesehenen Titel.",
      FinishTheseCollections: "Noch keine Filmreihe angefangen.",
      MyList: "Noch nichts hier – markiere Filme oder Serien mit ♥, dann erscheinen sie hier.",
      CollectionsSection: "Noch keine Filmreihen.",
      Studio: "Noch nichts hier.",
      Starring: "Noch nichts hier – schau ein paar Titel, dann gibt es Tipps nach Schauspielern.",
      DirectedBy: "Noch nichts hier – schau ein paar Filme, dann gibt es Tipps nach Regie.",
      UpcomingShows: "Im nächsten Monat laufen keine neuen Folgen.",
      UpcomingMovies: "In den nächsten Monaten erscheinen keine Filme."
    }
  };

  function langOf(lang) {
    return String(lang || "").toLowerCase().indexOf("de") === 0 ? "de" : "en";
  }

  function hintFor(id, lang) {
    var t = HINTS[langOf(lang)];
    return t[id] || t._;
  }

  // The recently watched rows are built here, not by the plugin: RegisterSection would need an unauthenticated public endpoint.
  var MOVIES = "RecentlyWatchedMovies";
  var SERIES = "RecentlyWatchedSeries";
  var RECENT_ROWS = [MOVIES, SERIES];
  var RECENT_AFTER = "RecentlyAddedShows";
  var RECENT_DAYS = 60;
  var RECENT_LIMIT = 20;
  var RECENT_NAMES = {
    en: { RecentlyWatchedMovies: "Recently Watched Movies", RecentlyWatchedSeries: "Recently Watched TV Series & Collections" },
    de: { RecentlyWatchedMovies: "Kürzlich angesehene Filme", RecentlyWatchedSeries: "Kürzlich angesehene Serien & Filmreihen" }
  };

  function recentName(id, lang) {
    return RECENT_NAMES[langOf(lang)][id];
  }

  function withRecent(enabled) {
    var out = enabled.filter(function (id) { return RECENT_ROWS.indexOf(id) === -1; });
    var at = out.indexOf(RECENT_AFTER);
    var args = [at === -1 ? out.length : at + 1, 0].concat(RECENT_ROWS);
    out.splice.apply(out, args);
    return out;
  }

  function playedAt(item) {
    var d = item && item.UserData && item.UserData.LastPlayedDate;
    var t = d ? Date.parse(d) : NaN;
    return isNaN(t) ? null : t;
  }

  function sinceOf(now) {
    return (now || new Date()).getTime() - RECENT_DAYS * 86400000;
  }

  function newest(picks) {
    picks.sort(function (a, b) { return b.at - a.at; });
    return picks.slice(0, RECENT_LIMIT).map(function (p) { return p.item; });
  }

  function pickMovies(movies, now) {
    var since = sinceOf(now);
    var picks = [];
    for (var i = 0; i < (movies || []).length; i++) {
      var t = playedAt(movies[i]);
      if (t !== null && t >= since && movies[i].Id) picks.push({ item: movies[i], at: t });
    }
    return newest(picks);
  }

  function seriesCandidates(episodes, now) {
    var since = sinceOf(now);
    var out = {};
    for (var i = 0; i < (episodes || []).length; i++) {
      var e = episodes[i];
      var t = playedAt(e);
      if (!e.SeriesId || e.ParentIndexNumber === 0 || t === null || t < since) continue;
      if (!out[e.SeriesId] || t > out[e.SeriesId]) out[e.SeriesId] = t;
    }
    return out;
  }

  function unplayedOf(item) {
    var n = item && item.UserData && item.UserData.UnplayedItemCount;
    return typeof n === "number" ? n : null;
  }

  function completeCollection(item) {
    return unplayedOf(item) === 0 && (item.RecursiveItemCount || item.ChildCount || 0) > 0;
  }

  // ignored: unwatched specials and unaired episodes that still count in Jellyfin's UnplayedItemCount
  function pickSeries(series, candidates, ignored, collections, now) {
    var since = sinceOf(now);
    var skip = {};
    var seen = {};
    var i;
    for (i = 0; i < (ignored || []).length; i++) {
      var e = ignored[i];
      if (!e || !e.SeriesId || seen[e.Id]) continue;
      seen[e.Id] = true;
      skip[e.SeriesId] = (skip[e.SeriesId] || 0) + 1;
    }
    var picks = [];
    for (i = 0; i < (series || []).length; i++) {
      var s = series[i];
      var left = unplayedOf(s);
      if (!s || !candidates[s.Id] || left === null || left - (skip[s.Id] || 0) > 0) continue;
      picks.push({ item: s, at: candidates[s.Id] });
    }
    for (i = 0; i < (collections || []).length; i++) {
      var c = collections[i];
      if (c && c.item && c.at !== null && c.at >= since) picks.push(c);
    }
    return newest(picks);
  }

  function loadRecent(api, userId, now) {
    var path = "Users/" + userId + "/Items";
    function query(q) {
      var all = { Recursive: true };
      for (var k in q) all[k] = q[k];
      return api.getJSON(api.getUrl(path, all)).then(function (r) { return (r && r.Items) || []; });
    }
    var played = { IsPlayed: true, SortBy: "DatePlayed", SortOrder: "Descending" };
    function playedQuery(extra) {
      var q = {};
      for (var k in played) q[k] = played[k];
      for (var e in extra) q[e] = extra[e];
      return query(q);
    }
    var left = { IncludeItemTypes: "Episode", IsPlayed: false, ExcludeLocationTypes: "Virtual", EnableImages: false, EnableUserData: false };
    function leftQuery(extra) {
      var q = {};
      for (var k in left) q[k] = left[k];
      for (var e in extra) q[e] = extra[e];
      return query(q);
    }
    return Promise.all([
      playedQuery({ IncludeItemTypes: "Movie", Limit: RECENT_LIMIT }),
      playedQuery({ IncludeItemTypes: "Episode", Limit: 300, EnableImages: false }),
      query({ IncludeItemTypes: "BoxSet", Fields: "RecursiveItemCount" })
    ]).then(function (r) {
      var candidates = seriesCandidates(r[1], now);
      var ids = Object.keys(candidates);
      var series = !ids.length ? Promise.resolve([[], []]) : query({ Ids: ids.join(","), Fields: "RecursiveItemCount" }).then(function (items) {
        var unwatched = items.some(function (s) { return unplayedOf(s) > 0; });
        if (!unwatched) return [items, []];
        return Promise.all([leftQuery({ ParentIndexNumber: 0 }), leftQuery({ IsUnaired: true })]).then(function (x) { return [items, x[0].concat(x[1])]; });
      });
      // The API has no multi-parent filter: one Limit=1 request per fully watched collection.
      var cols = Promise.all(r[2].filter(completeCollection).map(function (c) {
        return playedQuery({ ParentId: c.Id, Limit: 1, EnableImages: false }).then(function (m) { return { item: c, at: m.length ? playedAt(m[0]) : null }; });
      }));
      return Promise.all([series, cols]).then(function (x) {
        var out = {};
        out[MOVIES] = pickMovies(r[0], now);
        out[SERIES] = pickSeries(x[0][0], candidates, x[0][1], x[1], now);
        return out;
      });
    });
  }

  function imageOf(item) {
    if (item.ImageTags && item.ImageTags.Thumb) return { type: "Thumb", tag: item.ImageTags.Thumb };
    if (item.BackdropImageTags && item.BackdropImageTags.length) return { type: "Backdrop", tag: item.BackdropImageTags[0] };
    if (item.ImageTags && item.ImageTags.Primary) return { type: "Primary", tag: item.ImageTags.Primary };
    return null;
  }

  var RECENT_SECTION = '<div class="sectionTitleContainer sectionTitleContainer-cards padded-left"><h2 class="sectionTitle sectionTitle-cards"></h2></div>' +
    '<div is="emby-scroller" class="padded-top-focusscale padded-bottom-focusscale" data-centerfocus="true">' +
    '<div is="emby-itemscontainer" class="itemsContainer scrollSlider focuscontainer-x" data-monitor="videoplayback,markplayed"></div></div>';
  var CARD_TEXT = '<div class="cardText cardTextCentered cardText-first"><bdi></bdi></div><div class="cardText cardTextCentered cardText-secondary"><bdi></bdi></div>';
  // Same markup as Jellyfin's cardBuilder (overflowBackdropCard), so its click, play and focus handling applies.
  var CARD = '<div class="cardBox cardBox-bottompadded"><div class="cardScalable"><div class="cardPadder cardPadder-overflowBackdrop"></div>' +
    '<a data-action="link" class="cardImageContainer coveredImage cardContent itemAction"></a>' +
    '<div class="cardOverlayContainer itemAction" data-action="link"><button is="paper-icon-button-light" type="button" class="cardOverlayButton cardOverlayButton-hover itemAction paper-icon-button-light cardOverlayFab-primary" data-action="resume">' +
    '<span class="material-icons cardOverlayButtonIcon cardOverlayButtonIcon-hover play_arrow" aria-hidden="true"></span></button></div></div>' + CARD_TEXT + '</div>';
  var CARD_TV = '<div class="cardBox cardBox-bottompadded"><div class="cardScalable"><div class="cardPadder cardPadder-overflowBackdrop"></div>' +
    '<div class="cardImageContainer coveredImage cardContent"></div></div>' + CARD_TEXT + '</div>';

  function recentCard(doc, item, api, tv) {
    var card = doc.createElement(tv ? "button" : "div");
    card.className = tv ? "card overflowBackdropCard show-focus show-animation card-withuserdata itemAction" : "card overflowBackdropCard card-hoverable card-withuserdata";
    if (tv) {
      card.setAttribute("type", "button");
      card.setAttribute("data-action", "link");
    }
    card.innerHTML = tv ? CARD_TV : CARD;
    card.setAttribute("data-id", item.Id);
    card.setAttribute("data-serverid", item.ServerId || "");
    card.setAttribute("data-type", item.Type);
    card.setAttribute("data-isfolder", item.Type === "Series" || item.Type === "BoxSet" ? "true" : "false");
    if (item.MediaType) card.setAttribute("data-mediatype", item.MediaType);
    card.setAttribute("data-context", "home");
    card.setAttribute("aria-label", item.Name || "");
    var box = card.querySelector(".cardImageContainer");
    if (!tv) box.setAttribute("href", "#/details?id=" + encodeURIComponent(item.Id) + "&serverId=" + encodeURIComponent(item.ServerId || ""));
    var img = imageOf(item);
    if (img) box.style.backgroundImage = 'url("' + String(api.getScaledImageUrl(item.Id, { type: img.type, tag: img.tag, fillWidth: 326, fillHeight: 184, quality: 96 })).replace(/["\\\n]/g, encodeURIComponent) + '")';
    var texts = card.querySelectorAll(".cardText bdi");
    texts[0].textContent = item.Name || "";
    texts[1].textContent = item.ProductionYear ? String(item.ProductionYear) : "";
    return card;
  }

  function syncRecent(container, id, items, name, api) {
    var doc = container.ownerDocument;
    var sec = container.querySelector(".verticalSection." + id + "[data-hss-client]");
    if (!items.length) {
      if (sec) sec.remove();
      return;
    }
    var tv = doc.documentElement.classList.contains("layout-tv");
    var key = (tv ? "tv:" : "") + items.map(function (i) { return i.Id; }).join(",");
    if (sec && sec.getAttribute("data-hss-client") === key && sec.querySelector("h2").textContent === name) return;
    if (!sec) {
      sec = doc.createElement("div");
      sec.className = "verticalSection " + id;
      sec.innerHTML = RECENT_SECTION;
      // Insert at the final spot: moving it later re-attaches Jellyfin's scroller and doubles its scroll buttons.
      var ph = container.querySelector('[data-hss-placeholder="' + id + '"]');
      if (ph) sec.style.order = ph.style.order;
      container.insertBefore(sec, ph);
    }
    sec.setAttribute("data-hss-client", key);
    sec.querySelector("h2").textContent = name;
    var list = sec.querySelector(".itemsContainer");
    while (list.firstChild) list.removeChild(list.firstChild);
    for (var i = 0; i < items.length; i++) {
      var card = recentCard(doc, items[i], api, tv);
      card.setAttribute("data-index", String(i));
      list.appendChild(card);
    }
  }

  function upcomingSeries(container) {
    var cards = container.querySelectorAll(".upcoming-show-card");
    var seen = {};
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      var name = card.querySelector(".cardText-first .textActionButton");
      var key = name ? name.textContent : "";
      if (!key) continue;
      if (seen[key]) {
        if (card.style.display !== "none") card.style.display = "none";
        continue;
      }
      seen[key] = true;
      var ep = card.querySelector(".cardText-secondary .textActionButton");
      var m = ep && /^(S\d+)E\d+/.exec(ep.textContent);
      if (m && ep.textContent !== m[1]) {
        ep.title = ep.textContent;
        ep.textContent = m[1];
      }
    }
  }

  function upcomingDates(container, lang, now) {
    var locale = langOf(lang) === "de" ? "de-CH" : "en-GB";
    var year = (now || new Date()).getFullYear();
    var els = container.querySelectorAll('[class*="upcoming-"][class*="-card"] .cardText-tertiary .textActionButton');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.hasAttribute("data-hss-date")) continue;
      var m = /(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$/.exec(el.textContent);
      if (!m) continue;
      var d = new Date(+m[3], +m[2] - 1, +m[1]);
      var opts = { weekday: "short", day: "numeric", month: "short" };
      if (d.getFullYear() !== year) opts.year = "numeric";
      var text = d.toLocaleDateString(locale, opts);
      el.setAttribute("data-hss-date", "");
      el.title = el.textContent;
      el.textContent = locale === "en-GB" ? text.replace(/,/g, "") : text;
    }
  }

  function namesOf(resp) {
    var out = {};
    var items = resp && resp.Items;
    if (!items || !items.length) return out;
    for (var i = 0; i < items.length; i++) {
      if (items[i] && typeof items[i].Section === "string" && items[i].DisplayText) out[items[i].Section] = String(items[i].DisplayText);
    }
    return out;
  }

  function enabledOf(resp) {
    var list = resp && resp.EnabledSections;
    if (!list || !list.length) return [];
    var out = [];
    for (var i = 0; i < list.length; i++) {
      if (typeof list[i] === "string" && /^[A-Za-z0-9]+$/.test(list[i])) out.push(list[i]);
    }
    return out;
  }

  function realOf(container, id) {
    var out = [];
    var kids = container.children;
    for (var i = 0; i < kids.length; i++) {
      var el = kids[i];
      if (el.hasAttribute("data-hss-placeholder") || !el.classList.contains("verticalSection")) continue;
      for (var j = 0; j < el.classList.length; j++) {
        var c = el.classList[j];
        if (c === id || c.indexOf(id + "-") === 0) { out.push(el); break; }
      }
    }
    return out;
  }

  function filled(el) {
    var items = el.querySelector(".itemsContainer");
    if (el.classList.contains("hide") || !items) return false;
    for (var i = 0; i < items.children.length; i++) if (items.children[i].style.display !== "none") return true;
    return false;
  }

  function build(doc, id, name, hint) {
    var box = doc.createElement("div");
    box.className = "verticalSection hssEmptySection";
    box.setAttribute("data-hss-placeholder", id);
    var head = doc.createElement("div");
    head.className = "sectionTitleContainer sectionTitleContainer-cards padded-left";
    var h2 = doc.createElement("h2");
    h2.className = "sectionTitle sectionTitle-cards";
    h2.textContent = name;
    head.appendChild(h2);
    var p = doc.createElement("div");
    p.className = "padded-left padded-right";
    p.setAttribute("data-hss-hint", "");
    p.style.opacity = "0.6";
    p.style.margin = "0.25em 0 1.5em";
    p.textContent = hint;
    box.appendChild(head);
    box.appendChild(p);
    return box;
  }

  function place(container, el, after) {
    var order = after ? after.style.order : "0";
    if (el.style.order !== order) el.style.order = order;
    var next = after ? after.nextSibling : container.firstChild;
    if (next !== el) container.insertBefore(el, next);
  }

  function render(container, enabled, names, lang) {
    var doc = container.ownerDocument;
    var anchor = null;
    var wanted = {};
    for (var i = 0; i < enabled.length; i++) {
      var id = enabled[i];
      wanted[id] = true;
      var real = realOf(container, id);
      var any = false;
      for (var j = 0; j < real.length; j++) if (filled(real[j])) any = true;
      var ph = container.querySelector('[data-hss-placeholder="' + id + '"]');
      if (any) {
        if (ph) ph.remove();
        var last = real[real.length - 1];
        if (last.hasAttribute("data-hss-client")) place(container, last, anchor);
        anchor = last;
        continue;
      }
      var name = (names && names[id]) || id;
      var hint = hintFor(id, lang);
      if (!ph) ph = build(doc, id, name, hint);
      else {
        if (ph.querySelector("h2").textContent !== name) ph.querySelector("h2").textContent = name;
        if (ph.querySelector("[data-hss-hint]").textContent !== hint) ph.querySelector("[data-hss-hint]").textContent = hint;
      }
      place(container, ph, real.length ? real[real.length - 1] : anchor);
      anchor = ph;
    }
    var all = container.querySelectorAll("[data-hss-placeholder]");
    for (var k = 0; k < all.length; k++) if (!wanted[all[k].getAttribute("data-hss-placeholder")]) all[k].remove();
  }

  // Sections are locked server-side (home-sections.py); Jellyfin's own pickers do nothing under Modular Home.
  var LOCKED_SELECTOR = '.homeScreenSettingsContainer .selectContainer:has(select[id^="selectHomeSection"])';

  function lockHomeSettings(doc) {
    if (doc.getElementById("hss-lock-style")) return;
    var style = doc.createElement("style");
    style.id = "hss-lock-style";
    style.textContent = LOCKED_SELECTOR + " { display: none !important; }";
    (doc.head || doc.documentElement).appendChild(style);
  }

  function start(win) {
    var doc = win.document;
    lockHomeSettings(doc);
    var cache = null;
    var timer = null;
    var since = 0;

    function data(api) {
      var userId = api.getCurrentUserId();
      if (cache && cache.userId === userId && Date.now() - cache.at < CACHE_MS) return cache.promise;
      var lang = doc.documentElement.lang || win.navigator.language || "en";
      var promise = Promise.all([
        api.getJSON(api.getUrl("ModularHomeViews/UserSettings", { userId: userId })),
        api.getJSON(api.getUrl("ModularHomeViews/Sections", { language: lang })),
        loadRecent(api, userId).catch(function () { return {}; })
      ]).then(function (r) {
        var names = namesOf(r[1]);
        var enabled = enabledOf(r[0]);
        if (enabled.length) {
          enabled = withRecent(enabled);
          for (var n = 0; n < RECENT_ROWS.length; n++) names[RECENT_ROWS[n]] = recentName(RECENT_ROWS[n], lang);
        }
        return { enabled: enabled, names: names, lang: lang, recent: r[2] };
      });
      cache = { userId: userId, at: Date.now(), promise: promise };
      promise.catch(function () { cache = null; });
      return promise;
    }

    function run() {
      timer = null;
      since = 0;
      var api = win.ApiClient;
      if (!api || !win.HssPageMeta || !api.getCurrentUserId || !api.getCurrentUserId()) return;
      var containers = doc.querySelectorAll(".homeSectionsContainer");
      var live = [];
      for (var i = 0; i < containers.length; i++) if (containers[i].querySelector(".verticalSection[data-page]")) live.push(containers[i]);
      if (!live.length) return;
      var lang = doc.documentElement.lang || win.navigator.language || "en";
      observer.disconnect();
      try {
        for (var u = 0; u < live.length; u++) {
          upcomingSeries(live[u]);
          upcomingDates(live[u], lang);
        }
      } finally {
        observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
      }
      data(api).then(function (d) {
        if (!d.enabled.length) return;
        observer.disconnect();
        try {
          for (var j = 0; j < live.length; j++) {
            render(live[j], d.enabled, d.names, d.lang);
            for (var k = 0; k < RECENT_ROWS.length; k++) {
              var items = d.recent[RECENT_ROWS[k]] || [];
              syncRecent(live[j], RECENT_ROWS[k], items, d.names[RECENT_ROWS[k]], api);
            }
            render(live[j], d.enabled, d.names, d.lang);
          }
        } finally {
          observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
        }
      }).catch(function () {});
    }

    var observer = new win.MutationObserver(function () {
      var now = Date.now();
      if (!since) since = now;
      if (timer) win.clearTimeout(timer);
      timer = win.setTimeout(run, Math.max(0, Math.min(SETTLE_MS, since + MAX_WAIT_MS - now)));
    });
    observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { LOCKED_SELECTOR: LOCKED_SELECTOR, lockHomeSettings: lockHomeSettings, render: render, upcomingDates: upcomingDates, upcomingSeries: upcomingSeries, hintFor: hintFor, namesOf: namesOf, enabledOf: enabledOf, start: start, pickMovies: pickMovies, seriesCandidates: seriesCandidates, pickSeries: pickSeries, completeCollection: completeCollection, loadRecent: loadRecent, withRecent: withRecent, recentName: recentName, syncRecent: syncRecent, RECENT_DAYS: RECENT_DAYS, RECENT_LIMIT: RECENT_LIMIT };
  } else if (window.MutationObserver && document.body) {
    start(window);
  } else if (window.MutationObserver) {
    document.addEventListener("DOMContentLoaded", function () { start(window); });
  }
})();
