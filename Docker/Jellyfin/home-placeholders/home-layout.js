(function () {
  // Home layout on top of empty-sections.js (window.HssHomeExt): group order with the wide rows as dividers, the My List band, our own "Because you watched" and "Coming soon" rows.
  var BYW = "HssBecauseYouWatched";
  var COMING = "HssComingSoon";
  // [row, the row it goes before]; null = last. Applied in order, a missing target keeps the row where it is.
  var LAYOUT = [
    [COMING, "MyList"],
    [BYW, "HiddenGems"],
    ["DiscoverStudios", "RecentlyWatchedMovies"],
    ["DiscoverNetworks", BYW],
    ["DiscoverMovieGenres", "FinishTheseCollections"],
    ["DiscoverSeriesGenres", null]
  ];
  var DIVIDERS = ["MyList", "DiscoverStudios", "DiscoverNetworks", "DiscoverMovieGenres", "DiscoverSeriesGenres"];
  var LIKED_RATING = 7;
  var RECENT_MS = 30 * 86400000;
  var POOL_LIMIT = 200;
  var POOL_TTL_MS = 600000;
  var SIMILAR_FETCH = 30;
  var SIMILAR_MIN = 3;
  var SIMILAR_LIMIT = 20;
  var TRIES = 3;
  var LAST_PREFIX = "hss-byw:";
  var TEXT = {
    en: { name: "Because you watched", hint: "Nothing here yet — finish a film or series to get recommendations.", coming: "Coming soon", comingHint: "Nothing coming in the next months.", movie: "Movie" },
    de: { name: "Weil du geschaut hast", hint: "Noch nichts hier – schau einen Film oder eine Serie fertig, dann gibt es Empfehlungen.", coming: "Demnächst", comingHint: "In den nächsten Monaten erscheint nichts Neues.", movie: "Film" }
  };
  var COMING_FRESH_MS = 600000;
  var COMING_TTL_MS = 6 * 3600000;
  var COMING_PREFIX = "hss-coming:";
  var LIB_TTL_MS = 600000;
  var POSTER = /^\/HomeScreen\/CachedImage\/[0-9A-Za-z]+$/;

  function langOf(lang) {
    return String(lang || "").toLowerCase().indexOf("de") === 0 ? "de" : "en";
  }

  function bywName(seedName, lang) {
    var de = langOf(lang) === "de";
    if (!seedName) return TEXT[de ? "de" : "en"].name;
    return de ? "Weil du " + seedName + " geschaut hast" : "Because you watched " + seedName;
  }

  function layout(ids) {
    var out = ids.slice();
    if (out.indexOf(COMING) === -1) out.push(COMING);
    if (out.indexOf(BYW) === -1) out.push(BYW);
    for (var i = 0; i < LAYOUT.length; i++) {
      var id = LAYOUT[i][0];
      var before = LAYOUT[i][1];
      var at = out.indexOf(id);
      if (at === -1) continue;
      if (before === null) {
        out.splice(at, 1);
        out.push(id);
        continue;
      }
      if (out.indexOf(before) === -1) continue;
      out.splice(at, 1);
      out.splice(out.indexOf(before), 0, id);
    }
    return out;
  }

  function playedAt(item) {
    var d = item && item.UserData && item.UserData.LastPlayedDate;
    var t = d ? Date.parse(d) : NaN;
    return isNaN(t) ? null : t;
  }

  function likedOf(item) {
    var u = (item && item.UserData) || {};
    return !!(u.IsFavorite || u.Likes === true || (typeof u.Rating === "number" && u.Rating >= LIKED_RATING));
  }

  // played: films and fully watched series; episodes: played episodes (their series count as watched); favs: favourite films and series.
  function buildPool(played, episodes, favs) {
    var by = {};
    var order = [];
    function add(id, name, at, liked) {
      if (!id || !name) return;
      if (!by[id]) {
        by[id] = { Id: id, Name: name, at: null, liked: false };
        order.push(id);
      }
      var p = by[id];
      if (at !== null && (p.at === null || at > p.at)) p.at = at;
      p.liked = p.liked || liked;
    }
    var i;
    for (i = 0; i < (played || []).length; i++) add(played[i].Id, played[i].Name, playedAt(played[i]), likedOf(played[i]));
    for (i = 0; i < (episodes || []).length; i++) add(episodes[i].SeriesId, episodes[i].SeriesName, playedAt(episodes[i]), false);
    var fav = {};
    for (i = 0; i < (favs || []).length; i++) if (likedOf(favs[i])) fav[favs[i].Id] = true;
    return order.map(function (id) {
      if (fav[id]) by[id].liked = true;
      return by[id];
    });
  }

  function weightOf(p, now) {
    var recent = p.at !== null && now - p.at <= RECENT_MS;
    return (p.liked ? 4 : 1) * (recent ? 2 : 1);
  }

  // rand: () => [0, 1); avoid: seed ids not to pick (the last one, ones already tried).
  function pickSeed(pool, rand, avoid, now) {
    var skip = avoid || [];
    var list = (pool || []).filter(function (p) { return skip.indexOf(p.Id) === -1; });
    if (!list.length) return null;
    var total = 0;
    var w = list.map(function (p) { var x = weightOf(p, now); total += x; return x; });
    var r = rand() * total;
    for (var i = 0; i < list.length; i++) {
      r -= w[i];
      if (r < 0) return list[i];
    }
    return list[list.length - 1];
  }

  function similarOf(items, seedId) {
    return (items || []).filter(function (i) {
      return i && i.Id && i.Id !== seedId && (i.Type === "Movie" || i.Type === "Series") && !(i.UserData && i.UserData.Played);
    }).slice(0, SIMILAR_LIMIT);
  }

  function slim(item) {
    return { Id: item.Id, Name: item.Name, ServerId: item.ServerId, Type: item.Type, MediaType: item.MediaType, ProductionYear: item.ProductionYear, ImageTags: item.ImageTags, BackdropImageTags: item.BackdropImageTags };
  }

  function loadPool(api, userId) {
    var path = "Users/" + userId + "/Items";
    function query(q) {
      var all = { Recursive: true, Limit: POOL_LIMIT, EnableImages: false };
      for (var k in q) all[k] = q[k];
      return api.getJSON(api.getUrl(path, all)).then(function (r) { return (r && r.Items) || []; });
    }
    return Promise.all([
      query({ IncludeItemTypes: "Movie,Series", IsPlayed: true, SortBy: "DatePlayed", SortOrder: "Descending" }),
      query({ IncludeItemTypes: "Episode", IsPlayed: true, SortBy: "DatePlayed", SortOrder: "Descending" }),
      query({ IncludeItemTypes: "Movie,Series", IsFavorite: true })
    ]).then(function (r) { return buildPool(r[0], r[1], r[2]); });
  }

  function loadSimilar(api, userId, seedId) {
    return api.getJSON(api.getUrl("Items/" + seedId + "/Similar", { userId: userId, Limit: SIMILAR_FETCH, Fields: "PrimaryImageAspectRatio", EnableImageTypes: "Primary,Backdrop,Thumb", ImageTypeLimit: 1 }))
      .then(function (r) { return similarOf((r && r.Items) || [], seedId).map(slim); });
  }

  // A new seed for each home load: tries up to TRIES seeds until one has enough unwatched similar titles.
  function loadRow(api, userId, pool, rand, last, now) {
    var tried = last ? [last] : [];
    function attempt(n) {
      // Only one title watched: it may repeat.
      var seed = pickSeed(pool, rand, tried, now) || (n === 0 && last ? pickSeed(pool, rand, [], now) : null);
      if (!seed || n >= TRIES) return Promise.resolve({ seed: null, items: [] });
      tried.push(seed.Id);
      return loadSimilar(api, userId, seed.Id).then(function (items) {
        if (items.length >= SIMILAR_MIN) return { seed: seed, items: items };
        return attempt(n + 1);
      }, function () { return attempt(n + 1); });
    }
    return attempt(0);
  }

  // The plugin's Upcoming results (its date text "… - 11.11.2026" is in the server's zone, else the premiere date).
  function releaseOf(item) {
    var f = item && item.ProviderIds && item.ProviderIds.FormattedDate;
    var m = f && /(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$/.exec(f);
    if (m) return new Date(+m[3], +m[2] - 1, +m[1]);
    var t = item && item.PremiereDate ? Date.parse(item.PremiereDate) : NaN;
    if (isNaN(t)) return null;
    var d = new Date(t);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }

  // Monday-based week number.
  function weekOf(d) {
    return Math.floor((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86400000 + 3) / 7);
  }

  function shortDate(d, lang, now) {
    var de = langOf(lang) === "de";
    var opts = { weekday: "short", day: "numeric", month: "short" };
    if (d.getFullYear() !== (now || new Date()).getFullYear()) opts.year = "numeric";
    var text = d.toLocaleDateString(de ? "de-CH" : "en-GB", opts);
    return de ? text : text.replace(/,/g, "");
  }

  // shows, movies: the plugin's UpcomingShows / UpcomingMovies items; pop: {"s:<Sonarr id>" | "m:<Radarr id>": TMDB popularity}. One card per series (its soonest episode), soonest week first, the most popular first within a week.
  function comingSoon(shows, movies, pop) {
    var out = [];
    var seen = {};
    var i;
    var eps = (shows || []).map(function (e, n) { return { e: e, at: releaseOf(e), n: n }; })
      .sort(function (a, b) { return (a.at ? a.at.getTime() : Infinity) - (b.at ? b.at.getTime() : Infinity) || a.n - b.n; });
    for (i = 0; i < eps.length; i++) {
      var e = eps[i].e;
      var ids = (e && e.ProviderIds) || {};
      var sid = ids.SonarrSeriesId || (e && e.SeriesName);
      var at = eps[i].at;
      if (!sid || !at || seen[sid]) continue;
      seen[sid] = true;
      out.push({ key: "s:" + sid, name: e.SeriesName || e.Name || "", season: typeof e.ParentIndexNumber === "number" ? e.ParentIndexNumber : null,
        episode: typeof e.IndexNumber === "number" ? e.IndexNumber : null, at: at, poster: ids.SonarrPoster || null });
    }
    for (i = 0; i < (movies || []).length; i++) {
      var m = movies[i];
      var mids = (m && m.ProviderIds) || {};
      var mat = releaseOf(m);
      if (!mat || !mids.RadarrMovieId) continue;
      out.push({ key: "m:" + mids.RadarrMovieId, name: m.Name || "", movie: true, at: mat, poster: mids.RadarrPoster || null });
    }
    var p = pop || {};
    out.sort(function (a, b) {
      return weekOf(a.at) - weekOf(b.at) || (p[b.key] || 0) - (p[a.key] || 0) || a.at - b.at || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
    });
    return out.map(function (c) { return { key: c.key, name: c.name, season: c.season, episode: c.movie ? null : c.episode, movie: !!c.movie, at: c.at.getTime(), poster: c.poster }; });
  }

  function two(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function comingLine(c, lang, now) {
    var date = shortDate(new Date(c.at), lang, now);
    if (c.movie) return TEXT[langOf(lang)].movie + " \u00b7 " + date;
    if (c.season === null || c.season === undefined) return date;
    return "S" + two(c.season) + (typeof c.episode === "number" ? "E" + two(c.episode) : "") + " \u00b7 " + date;
  }

  function normName(s) {
    return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  }

  // The user's films and series by provider id and by name, per type.
  function libIndex(items) {
    var idx = { Movie: { tmdb: {}, tvdb: {}, imdb: {}, name: {} }, Series: { tmdb: {}, tvdb: {}, imdb: {}, name: {} } };
    for (var i = 0; i < (items || []).length; i++) {
      var it = items[i];
      var t = it && idx[it.Type];
      if (!t || !it.Id) continue;
      var p = it.ProviderIds || {};
      if (p.Tmdb) t.tmdb[String(p.Tmdb)] = it;
      if (p.Tvdb) t.tvdb[String(p.Tvdb)] = it;
      if (p.Imdb) t.imdb[String(p.Imdb)] = it;
      var n = normName(it.Name);
      if (n && !t.name[n]) t.name[n] = it;
    }
    return idx;
  }

  // info: window.HssUpcomingInfo (upcoming-popularity.py: the arr's TMDB/TVDB/IMDb ids; the plugin's items carry only Sonarr/Radarr ids). Name only when no ids are known.
  function matchComing(c, info, idx) {
    if (!idx) return null;
    var t = idx[c.movie ? "Movie" : "Series"];
    var ids = info && info[c.key];
    if (ids && (ids.tmdb || ids.tvdb || ids.imdb)) {
      var keys = ["tmdb", "tvdb", "imdb"];
      for (var i = 0; i < keys.length; i++) if (ids[keys[i]] && t[keys[i]][String(ids[keys[i]])]) return t[keys[i]][String(ids[keys[i]])];
      return null;
    }
    return t.name[normName(c.name)] || null;
  }

  var COMING_CARD = '<div class="cardBox cardBox-bottompadded"><div class="cardScalable"><div class="cardPadder cardPadder-overflowPortrait"></div>' +
    '<div class="cardImageContainer coveredImage cardContent"></div></div>' +
    '<div class="cardText cardTextCentered cardText-first"><bdi></bdi></div><div class="cardText cardTextCentered cardText-secondary"><bdi></bdi></div></div>';

  var COMING_LINK = COMING_CARD.replace('<div class="cardImageContainer coveredImage cardContent"></div>', '<a data-action="link" class="cardImageContainer coveredImage cardContent itemAction"></a>');

  // ctx: {lib: libIndex of the user's library, info: window.HssUpcomingInfo}. In the library = Jellyfin's own item card (link on desktop, item button on TV);
  // else a button that opens the details overlay (details.js), without request buttons: the title is already in Sonarr/Radarr.
  function comingCard(lang, now, ctx) {
    return function (doc, c, api, tv) {
      var jf = ctx ? matchComing(c, ctx.info, ctx.lib) : null;
      var card = doc.createElement(tv || !jf ? "button" : "div");
      card.className = "card overflowPortraitCard hssComingCard" + (tv ? " show-focus show-animation" : "") + (jf ? (tv ? " itemAction" : "") : " hssComingOpen");
      if (tv || !jf) card.setAttribute("type", "button");
      card.innerHTML = jf && !tv ? COMING_LINK : COMING_CARD;
      card.setAttribute("aria-label", c.name);
      if (jf) {
        card.setAttribute("data-id", jf.Id);
        card.setAttribute("data-serverid", jf.ServerId || "");
        card.setAttribute("data-type", jf.Type);
        card.setAttribute("data-isfolder", jf.Type === "Series" ? "true" : "false");
        card.setAttribute("data-context", "home");
        if (tv) card.setAttribute("data-action", "link");
        else card.querySelector(".cardImageContainer").setAttribute("href", "#/details?id=" + encodeURIComponent(jf.Id) + "&serverId=" + encodeURIComponent(jf.ServerId || ""));
      } else {
        card.setAttribute("data-hss-key", "coming/" + c.key);
        card.hssComing = c;
        card.hssLine = comingLine(c, lang, now);
      }
      var box = card.querySelector(".cardImageContainer");
      if (c.poster && POSTER.test(c.poster)) {
        var url = String(api.getUrl(c.poster.slice(1))).replace(/["\\\n]/g, encodeURIComponent);
        box.style.backgroundImage = 'url("' + url + '")';
        card.hssPoster = url;
      }
      var texts = card.querySelectorAll(".cardText bdi");
      texts[0].textContent = c.name;
      texts[1].textContent = comingLine(c, lang, now);
      var P = jf && !tv && (doc.defaultView || {}).HssPlayTv;
      var pb = P && P.button(doc, { id: jf.Id, type: jf.Type, name: jf.Name || c.name }, lang);
      if (pb) card.querySelector(".cardBox").appendChild(doc.createElement("div")).appendChild(pb).parentNode.className = "hssComingActions";
      return card;
    };
  }

  function loadComing(api, userId, pop) {
    function section(id) {
      return api.getJSON(api.getUrl("HomeScreen/Section/" + id, { UserId: userId })).then(function (r) { return (r && r.Items) || []; });
    }
    return Promise.all([section("UpcomingShows"), section("UpcomingMovies")]).then(function (r) { return comingSoon(r[0], r[1], pop); });
  }

  var CSS = [
    ".hssComingCard { cursor: default; }",
    "button.hssComingCard { margin: 0; padding: 0; border: 0; background: none; color: inherit; font: inherit; text-align: inherit; }",
    ".hssComingOpen { cursor: pointer; }",
    ".hssComingActions { text-align: center; }",
    // My List: a full-width tinted band with compact posters, the strongest divider.
    '.verticalSection.MyList:not(.hide), [data-hss-placeholder="MyList"] { position: relative; margin: 1.6em 0 1.8em; padding: 0.9em 0 0.5em; background: linear-gradient(100deg, rgba(0, 164, 220, 0.20), rgba(170, 92, 195, 0.16) 55%, rgba(170, 92, 195, 0.05)); border-top: 1px solid rgba(255, 255, 255, 0.10); border-bottom: 1px solid rgba(255, 255, 255, 0.10); }',
    '.verticalSection.MyList .sectionTitle, [data-hss-placeholder="MyList"] .sectionTitle { text-transform: uppercase; letter-spacing: 0.12em; font-size: 1.05em; font-weight: 700; }',
    '.verticalSection.MyList .sectionTitle::after, [data-hss-placeholder="MyList"] .sectionTitle::after { color: #ff5c8a; }',
    ".verticalSection.MyList .overflowPortraitCard { width: clamp(6.2rem, 8.4vw, 9.5rem); }",
    ".verticalSection.MyList .overflowBackdropCard { width: clamp(10rem, 14vw, 16rem); }",
    ".verticalSection.MyList .cardText-secondary { display: none; }",
    ".verticalSection.MyList .cardText { font-size: 0.85em; }",
    "@media (max-width: 600px) { .verticalSection.MyList .overflowPortraitCard { width: 27vw; } .verticalSection.MyList .overflowBackdropCard { width: 46vw; } }",
    // Wide-card rows: a little air and a hairline above, so they read as the break between groups.
    ".verticalSection.DiscoverStudios, .verticalSection.DiscoverNetworks, .verticalSection.DiscoverMovieGenres, .verticalSection.DiscoverSeriesGenres, " +
      '[data-hss-placeholder^="Discover"] { margin-top: 0.8em; margin-bottom: 1.2em; padding-top: 0.8em; border-top: 1px solid rgba(255, 255, 255, 0.07); }'
  ].join("\n");

  function styles(doc) {
    if (doc.getElementById("hss-layout-style")) return;
    var style = doc.createElement("style");
    style.id = "hss-layout-style";
    style.textContent = CSS;
    (doc.head || doc.documentElement).appendChild(style);
  }

  function create(win) {
    var rows = typeof win.WeakMap === "function" ? new win.WeakMap() : null;
    var pool = null;
    var poolAt = 0;
    var poolUser = null;
    var poolLoading = null;

    function storage() {
      try {
        return win.localStorage;
      } catch (e) {
        return null;
      }
    }

    function lastSeed(userId) {
      try {
        var s = storage();
        return (s && s.getItem(LAST_PREFIX + userId)) || null;
      } catch (e) {
        return null;
      }
    }

    function saveSeed(userId, id) {
      try {
        var s = storage();
        if (s && id) s.setItem(LAST_PREFIX + userId, id);
      } catch (e) {}
    }

    function getPool(api, userId) {
      if (pool && poolUser === userId && Date.now() - poolAt < POOL_TTL_MS) return Promise.resolve(pool);
      if (poolLoading) return poolLoading;
      poolLoading = loadPool(api, userId).then(function (p) {
        pool = p;
        poolAt = Date.now();
        poolUser = userId;
        poolLoading = null;
        return p;
      }, function (e) {
        poolLoading = null;
        throw e;
      });
      return poolLoading;
    }

    var lib = null;
    var libAt = 0;
    var libUser = null;
    var libLoading = null;

    function getLib(api, userId) {
      if (lib && libUser === userId && Date.now() - libAt < LIB_TTL_MS) return Promise.resolve(lib);
      if (libLoading) return libLoading;
      libLoading = api.getJSON(api.getUrl("Users/" + userId + "/Items", { Recursive: true, IncludeItemTypes: "Movie,Series", Fields: "ProviderIds", EnableImages: false, EnableUserData: false }))
        .then(function (r) {
          lib = libIndex((r && r.Items) || []);
          libAt = Date.now();
          libUser = userId;
          libLoading = null;
          return lib;
        }, function () {
          libLoading = null;
          return lib;
        });
      return libLoading;
    }

    win.document.addEventListener("click", function (e) {
      var t = e.target && e.target.closest ? e.target.closest(".hssComingOpen") : null;
      if (!t || !t.hssComing || !win.HssDetails) return;
      e.preventDefault();
      e.stopPropagation();
      var c = t.hssComing;
      var info = (win.HssUpcomingInfo || {})[c.key] || {};
      win.HssDetails.open({ id: info.d || null, key: t.getAttribute("data-hss-key"), from: t, fallback: { title: c.name, poster: t.hssPoster || null }, line: t.hssLine });
    });

    function rowOf(container, id) {
      var all = rows ? rows.get(container) : container.hssLayoutRows;
      return all && all[id];
    }

    function setRow(container, id, r) {
      var all = (rows ? rows.get(container) : container.hssLayoutRows) || {};
      all[id] = r;
      if (rows) rows.set(container, all);
      else container.hssLayoutRows = all;
    }

    // Coming soon: per-user browser cache (shown up to 6 h, refreshed after 10 min), so the row paints at once.
    function cachedComing(userId) {
      try {
        var s = storage();
        var v = s && JSON.parse(s.getItem(COMING_PREFIX + userId) || "null");
        return v && Array.isArray(v.items) && typeof v.at === "number" ? v : null;
      } catch (e) {
        return null;
      }
    }

    function saveComing(userId, items) {
      try {
        var s = storage();
        if (s) s.setItem(COMING_PREFIX + userId, JSON.stringify({ at: Date.now(), items: items }));
      } catch (e) {}
    }

    function startBecause(container, api, tools) {
      var userId = api.getCurrentUserId();
      var r = { done: false, seed: null, items: [] };
      setRow(container, BYW, r);
      getPool(api, userId).then(function (p) {
        return loadRow(api, userId, p, Math.random, lastSeed(userId), Date.now());
      }).then(function (res) {
        r.seed = res.seed;
        r.items = res.items;
        if (res.seed) saveSeed(userId, res.seed.Id);
      }, function () {}).then(function () {
        r.done = true;
        if (container.isConnected) tools.repaint();
      });
    }

    function startComing(container, api, tools) {
      var userId = api.getCurrentUserId();
      var c = cachedComing(userId);
      var age = c ? Date.now() - c.at : Infinity;
      var r = { done: age <= COMING_TTL_MS, items: age <= COMING_TTL_MS ? c.items : [], lib: lib && libUser === userId ? lib : null };
      setRow(container, COMING, r);
      getLib(api, userId).then(function (l) {
        if (l === r.lib) return;
        r.lib = l;
        if (r.done && container.isConnected) tools.repaint();
      });
      if (age <= COMING_FRESH_MS) return;
      loadComing(api, userId, win.HssUpcomingPop).then(function (items) {
        r.items = items;
        saveComing(userId, items);
      }, function () {}).then(function () {
        r.done = true;
        if (container.isConnected) tools.repaint();
      });
    }

    return {
      ids: [COMING, BYW],
      layout: layout,
      name: function (id, lang, container) {
        if (id === COMING) return TEXT[langOf(lang)].coming;
        var r = container && rowOf(container, id);
        return bywName(r && r.seed ? r.seed.Name : null, lang);
      },
      hint: function (id, lang) {
        return TEXT[langOf(lang)][id === COMING ? "comingHint" : "hint"];
      },
      loading: function (id, container) {
        var r = container && rowOf(container, id);
        return !r || !r.done;
      },
      sync: function (container, api, lang, tools) {
        styles(container.ownerDocument);
        if (!rowOf(container, COMING)) startComing(container, api, tools);
        if (!rowOf(container, BYW)) startBecause(container, api, tools);
        var c = rowOf(container, COMING);
        if (c.done) {
          var ctx = { lib: c.lib, info: win.HssUpcomingInfo };
          tools.syncRow(container, COMING, c.items, TEXT[langOf(lang)].coming, api, comingCard(lang, null, ctx), function (x) {
            var jf = matchComing(x, ctx.info, ctx.lib);
            return x.key + "@" + x.at + "@" + x.episode + "@" + langOf(lang) + "@" + (jf ? jf.Id : "") + (win.HssPlayTv ? "@p" : "");
          });
        }
        var b = rowOf(container, BYW);
        if (b.done) tools.syncRecent(container, BYW, b.items, bywName(b.seed ? b.seed.Name : null, lang), api);
      }
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { COMING: COMING, comingSoon: comingSoon, comingLine: comingLine, comingCard: comingCard, libIndex: libIndex, matchComing: matchComing, releaseOf: releaseOf, weekOf: weekOf, loadComing: loadComing, BYW: BYW, DIVIDERS: DIVIDERS, layout: layout, bywName: bywName, buildPool: buildPool, weightOf: weightOf, pickSeed: pickSeed, similarOf: similarOf, loadPool: loadPool, loadRow: loadRow, likedOf: likedOf, styles: styles, create: create, CSS: CSS };
  } else {
    (window.HssHomeExt = window.HssHomeExt || []).push(create(window));
  }
})();
