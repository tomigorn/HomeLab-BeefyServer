(function () {
  var SETTLE_MS = 1200;
  // The TV layout's clock changes the DOM every second, so a plain debounce would never fire there.
  var MAX_WAIT_MS = 2500;
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
      UpcomingShows: "No episodes airing in the next month.",
      UpcomingMovies: "No films releasing in the next months.",
      DiscoverStudios: "No studios yet — they show up once films are in the library.",
      DiscoverNetworks: "No networks yet — they show up once series are in the library.",
      DiscoverMovieGenres: "No film genres yet.",
      DiscoverSeriesGenres: "No series genres yet."
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
      UpcomingShows: "Im nächsten Monat laufen keine neuen Folgen.",
      UpcomingMovies: "In den nächsten Monaten erscheinen keine Filme.",
      DiscoverStudios: "Noch keine Studios – sie erscheinen, sobald Filme in der Bibliothek sind.",
      DiscoverNetworks: "Noch keine Sender – sie erscheinen, sobald Serien in der Bibliothek sind.",
      DiscoverMovieGenres: "Noch keine Film-Genres.",
      DiscoverSeriesGenres: "Noch keine Serien-Genres."
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
    return insertAfter(enabled, RECENT_ROWS, RECENT_AFTER);
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

  var STUDIOS = "DiscoverStudios";
  var NETWORKS = "DiscoverNetworks";
  var MOVIE_GENRES = "DiscoverMovieGenres";
  var SERIES_GENRES = "DiscoverSeriesGenres";
  var DISCOVER_ROWS = [STUDIOS, NETWORKS, MOVIE_GENRES, SERIES_GENRES];
  var DISCOVER_AFTER = "Studio";
  var DISCOVER_LIMIT = 20;
  var DISCOVER_NAMES = {
    en: { DiscoverStudios: "Studios", DiscoverNetworks: "Networks", DiscoverMovieGenres: "Movie Genres", DiscoverSeriesGenres: "Series Genres" },
    de: { DiscoverStudios: "Studios", DiscoverNetworks: "Sender", DiscoverMovieGenres: "Film-Genres", DiscoverSeriesGenres: "Serien-Genres" }
  };

  function discoverName(id, lang) {
    return DISCOVER_NAMES[langOf(lang)][id];
  }

  function insertAfter(enabled, rows, after) {
    var out = enabled.filter(function (id) { return rows.indexOf(id) === -1; });
    var at = out.indexOf(after);
    out.splice.apply(out, [at === -1 ? out.length : at + 1, 0].concat(rows));
    return out;
  }

  function withDiscover(enabled) {
    return insertAfter(enabled, DISCOVER_ROWS, DISCOVER_AFTER);
  }

  // only: studio ids allowed (the networks); counts titles per studio or genre.
  function countBy(items, field, only) {
    var by = {};
    for (var i = 0; i < (items || []).length; i++) {
      var list = items[i][field] || [];
      for (var j = 0; j < list.length; j++) {
        var p = list[j];
        if (!p || !p.Id || (only && only.indexOf(p.Id) === -1)) continue;
        if (!by[p.Id]) by[p.Id] = { Id: p.Id, Name: p.Name || "", count: 0 };
        by[p.Id].count++;
      }
    }
    var out = Object.keys(by).map(function (k) { return by[k]; });
    out.sort(function (a, b) { return b.count - a.count || (a.Name < b.Name ? -1 : a.Name > b.Name ? 1 : 0); });
    return out.slice(0, DISCOVER_LIMIT);
  }

  // groups: the server's studio or network cards in precedence order ({name, ids}; a bare id = one studio). Each title counts only for its first card.
  function assignCards(items, groups) {
    if (!Array.isArray(groups)) return [];
    var cards = groups.map(function (g, i) {
      var one = typeof g === "string";
      return { name: one ? null : g.name || null, ids: one ? [g] : g.ids || [], items: [], rank: i };
    });
    for (var i = 0; i < (items || []).length; i++) {
      var mine = (items[i].Studios || []).map(function (s) { return s && s.Id; });
      for (var k = 0; k < cards.length; k++) {
        if (cards[k].ids.some(function (id) { return mine.indexOf(id) !== -1; })) {
          cards[k].items.push(items[i].Id);
          break;
        }
      }
    }
    var out = cards.filter(function (c) { return c.items.length; });
    out.sort(function (a, b) { return b.items.length - a.items.length || a.rank - b.rank; });
    return out.slice(0, DISCOVER_LIMIT).map(function (c) { return { name: c.name, ids: c.ids, items: c.items }; });
  }

  function pickBackdrops(genres, items) {
    var ranked = (items || []).filter(function (i) { return i.BackdropImageTags && i.BackdropImageTags.length; });
    ranked.sort(function (a, b) { return (b.CommunityRating || 0) - (a.CommunityRating || 0); });
    var used = {};
    var out = {};
    for (var g = 0; g < genres.length; g++) {
      var first = null;
      var pick = null;
      for (var i = 0; i < ranked.length && !pick; i++) {
        var has = (ranked[i].GenreItems || []).some(function (x) { return x.Id === genres[g].Id; });
        if (!has) continue;
        if (!first) first = ranked[i];
        if (!used[ranked[i].Id]) pick = ranked[i];
      }
      pick = pick || first;
      if (!pick) continue;
      used[pick.Id] = true;
      out[genres[g].Id] = { Id: pick.Id, tag: pick.BackdropImageTags[0] };
    }
    return out;
  }

  // [shadow, highlight] of the duotone, Seerr-like.
  var GENRE_COLORS = {
    action: ["#7f1d1d", "#fca5a5"], adventure: ["#4c1d95", "#d8b4fe"], "action & adventure": ["#7c2d12", "#fdba74"],
    animation: ["#1e3a8a", "#93c5fd"], comedy: ["#713f12", "#fde047"], crime: ["#1f2937", "#cbd5e1"],
    documentary: ["#134e4a", "#5eead4"], drama: ["#831843", "#f9a8d4"], family: ["#14532d", "#86efac"],
    fantasy: ["#581c87", "#e9d5ff"], "sci-fi & fantasy": ["#164e63", "#67e8f9"], history: ["#78350f", "#fcd34d"],
    horror: ["#450a0a", "#f87171"], music: ["#701a75", "#f0abfc"], mystery: ["#312e81", "#a5b4fc"],
    romance: ["#881337", "#fda4af"], "science fiction": ["#0c4a6e", "#7dd3fc"], thriller: ["#27272a", "#a1a1aa"],
    war: ["#365314", "#bef264"], "war & politics": ["#3f6212", "#d9f99d"], western: ["#7c2d12", "#fed7aa"],
    kids: ["#065f46", "#6ee7b7"], reality: ["#9a3412", "#fdba74"], sport: ["#166534", "#bbf7d0"],
    "tv movie": ["#334155", "#cbd5e1"], news: ["#1e293b", "#94a3b8"], soap: ["#9d174d", "#fbcfe8"], talk: ["#0f766e", "#99f6e4"]
  };
  var GENRE_ALIASES = {
    abenteuer: "adventure", komödie: "comedy", krimi: "crime", dokumentarfilm: "documentary", dokumentation: "documentary",
    familie: "family", historie: "history", musik: "music", liebesfilm: "romance", kriegsfilm: "war", zeichentrick: "animation"
  };
  var GENRE_SPARE = [["#1e3a8a", "#bfdbfe"], ["#14532d", "#bbf7d0"], ["#7c2d12", "#fed7aa"], ["#4a044e", "#f5d0fe"], ["#0f172a", "#e2e8f0"], ["#713f12", "#fef08a"]];

  function genreColors(name) {
    var key = String(name || "").toLowerCase();
    key = GENRE_ALIASES[key] || key;
    if (GENRE_COLORS[key]) return GENRE_COLORS[key];
    var h = 0;
    for (var i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) % 9973;
    return GENRE_SPARE[h % GENRE_SPARE.length];
  }

  // cards: {studios, networks} from studio-logos.py (window.HssStudios / HssNetworks); none = no row.
  function loadDiscover(api, userId, cards) {
    var path = "Users/" + userId + "/Items";
    function titles(type) {
      return api.getJSON(api.getUrl(path, { IncludeItemTypes: type, Recursive: true, Fields: "Studios,Genres", EnableImageTypes: "Backdrop", ImageTypeLimit: 1, EnableUserData: false }))
        .then(function (r) { return (r && r.Items) || []; });
    }
    return Promise.all([
      api.getJSON(api.getUrl("UserViews", { userId: userId })).then(function (r) { return (r && r.Items) || []; }),
      titles("Movie"),
      titles("Series")
    ]).then(function (r) {
      function lib(type) {
        var v = r[0].filter(function (x) { return x.CollectionType === type; });
        return v.length === 1 ? v[0].Id : null;
      }
      var movies = r[1];
      var shows = r[2];
      var studios = assignCards(movies, cards && cards.studios);
      var nets = assignCards(shows, cards && cards.networks);
      var ids = [];
      studios.concat(nets).forEach(function (c) { ids = ids.concat(c.ids); });
      var named = {};
      movies.concat(shows).forEach(function (i) { (i.Studios || []).forEach(function (s) { if (s && s.Id) named[s.Id] = s.Name; }); });
      var logos = !ids.length ? Promise.resolve([]) : api.getJSON(api.getUrl(path, { Ids: ids.join(","), EnableImageTypes: "Logo", ImageTypeLimit: 1, EnableUserData: false }))
        .then(function (x) { return (x && x.Items) || []; });
      return logos.then(function (found) {
        var tag = {};
        for (var i = 0; i < found.length; i++) if (found[i].ImageTags && found[i].ImageTags.Logo) tag[found[i].Id] = found[i].ImageTags.Logo;
        var server = (movies[0] || shows[0] || {}).ServerId || "";
        function studioCards(list, parent) {
          return list.map(function (c) {
            var rep = c.ids.filter(function (id) { return tag[id]; })[0] || c.ids.filter(function (id) { return named[id]; })[0] || c.ids[0];
            return { Id: rep, Name: c.name || named[rep] || "", Type: "Studio", ServerId: server, ParentId: parent, logo: tag[rep] || null, items: c.items };
          });
        }
        function genreCards(items, parent) {
          var genres = countBy(items, "GenreItems");
          var art = pickBackdrops(genres, items);
          return genres.map(function (g) { return { Id: g.Id, Name: g.Name, Type: "Genre", ServerId: server, ParentId: parent, backdrop: art[g.Id] || null }; });
        }
        var out = {};
        out[STUDIOS] = studioCards(studios, lib("movies"));
        out[NETWORKS] = studioCards(nets, lib("tvshows"));
        out[MOVIE_GENRES] = genreCards(movies, lib("movies"));
        out[SERIES_GENRES] = genreCards(shows, lib("tvshows"));
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

  function discoverHref(card) {
    var key = card.Type === "Studio" ? "studioId" : "genreId";
    return "#/list?" + key + "=" + encodeURIComponent(card.Id) + "&serverId=" + encodeURIComponent(card.ServerId || "") + (card.ParentId ? "&parentId=" + encodeURIComponent(card.ParentId) : "");
  }

  function cssUrl(url) {
    return 'url("' + String(url).replace(/["\\\n]/g, encodeURIComponent) + '")';
  }

  var DISCOVER_CARD = '<div class="cardBox"><div class="cardScalable"><div class="cardPadder cardPadder-overflowBackdrop"></div>' +
    '<a data-action="link" class="cardImageContainer cardContent itemAction"></a></div></div>';
  var DISCOVER_CARD_TV = '<div class="cardBox"><div class="cardScalable"><div class="cardPadder cardPadder-overflowBackdrop"></div>' +
    '<div class="cardImageContainer cardContent"></div></div></div>';

  // Studio and network cards open their own list (openList): Jellyfin's studio page would also show the titles counted under a bigger studio.
  var DISCOVER_CARD_OPEN = '<div class="cardBox"><div class="cardScalable"><div class="cardPadder cardPadder-overflowBackdrop"></div>' +
    '<div class="cardImageContainer cardContent hssOpen" role="button" tabindex="0"></div></div></div>';

  function discoverCard(doc, item, api, tv) {
    var card = doc.createElement(tv ? "button" : "div");
    var studio = item.Type === "Studio";
    var kind = studio ? "hssStudioCard" : "hssGenreCard";
    card.className = (tv ? "card overflowBackdropCard show-focus show-animation " + (studio ? "hssOpen " : "itemAction ") : "card overflowBackdropCard card-hoverable ") + "hssDiscoverCard " + kind;
    if (tv) {
      card.setAttribute("type", "button");
      if (!studio) card.setAttribute("data-action", "link");
    }
    card.innerHTML = tv ? DISCOVER_CARD_TV : studio ? DISCOVER_CARD_OPEN : DISCOVER_CARD;
    card.setAttribute("data-id", item.Id);
    card.setAttribute("data-serverid", item.ServerId || "");
    card.setAttribute("data-type", item.Type);
    card.setAttribute("data-isfolder", "true");
    if (item.ParentId) card.setAttribute("data-parentid", item.ParentId);
    card.setAttribute("data-context", "home");
    card.setAttribute("aria-label", item.Name || "");
    var box = card.querySelector(".cardImageContainer");
    if (!tv && !studio) box.setAttribute("href", discoverHref(item));
    if (studio) {
      card.setAttribute("data-hss-items", (item.items || []).join(","));
      var count = doc.createElement("div");
      count.className = "hssCount";
      count.textContent = String((item.items || []).length);
      var inner = doc.createElement("div");
      if (item.logo) {
        inner.className = "hssLogo";
        inner.style.backgroundImage = cssUrl(api.getScaledImageUrl(item.Id, { type: "Logo", tag: item.logo, maxWidth: 480, quality: 96 }));
      } else {
        inner.className = "hssName";
        inner.textContent = item.Name || "";
      }
      box.appendChild(inner);
      box.appendChild(count);
      return card;
    }
    var colors = genreColors(item.Name);
    var duo = doc.createElement("div");
    duo.className = "hssDuo";
    duo.style.setProperty("--hss-dark", colors[0]);
    duo.style.setProperty("--hss-light", colors[1]);
    if (item.backdrop) {
      var img = doc.createElement("div");
      img.className = "hssDuoImg";
      img.style.backgroundImage = cssUrl(api.getScaledImageUrl(item.backdrop.Id, { type: "Backdrop", tag: item.backdrop.tag, fillWidth: 480, fillHeight: 270, quality: 90 }));
      duo.appendChild(img);
    }
    var tint = doc.createElement("div");
    tint.className = "hssDuoTint";
    duo.appendChild(tint);
    var name = doc.createElement("div");
    name.className = "hssGenreName";
    name.textContent = item.Name || "";
    box.appendChild(duo);
    box.appendChild(name);
    return card;
  }

  function discoverKey(item) {
    return [item.Id, item.ParentId || "", item.logo || "", item.backdrop ? item.backdrop.Id + "/" + item.backdrop.tag : "", item.Name, (item.items || []).join(" ")].join("|");
  }

  function syncDiscover(container, id, items, name, api) {
    syncRow(container, id, items, name, api, discoverCard, discoverKey);
  }

  function syncRecent(container, id, items, name, api) {
    syncRow(container, id, items, name, api, recentCard, function (i) { return i.Id; });
  }

  function syncRow(container, id, items, name, api, make, keyOf) {
    var doc = container.ownerDocument;
    var sec = container.querySelector(".verticalSection." + id + "[data-hss-client]");
    if (!items.length) {
      if (sec) sec.remove();
      return;
    }
    var tv = doc.documentElement.classList.contains("layout-tv");
    var key = (tv ? "tv:" : "") + items.map(keyOf).join(",");
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
      var card = make(doc, items[i], api, tv);
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

  function modesOf(resp) {
    var out = {};
    var items = (resp && resp.Items) || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i] && typeof items[i].Section === "string" && items[i].ViewMode) out[items[i].Section] = String(items[i].ViewMode);
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

  var SKELETON_CARDS = 6;

  function skeleton(doc, mode) {
    var shape = mode === "Portrait" ? "overflowPortrait" : "overflowBackdrop";
    var row = doc.createElement("div");
    row.className = "itemsContainer scrollSlider hssSkeleton padded-left";
    var card = '<div class="card ' + shape + 'Card"><div class="cardBox cardBox-bottompadded"><div class="cardScalable"><div class="cardPadder cardPadder-' + shape + '"></div>' +
      '<div class="cardImageContainer hssSkelImg"></div></div><div class="cardText">&nbsp;</div><div class="cardText">&nbsp;</div></div></div>';
    var html = "";
    for (var i = 0; i < SKELETON_CARDS; i++) html += card;
    row.innerHTML = html;
    return row;
  }

  function setLoading(ph, on, mode) {
    var hint = ph.querySelector("[data-hss-hint]");
    var skel = ph.querySelector(".hssSkeleton");
    var shape = mode === "Portrait" ? "Portrait" : "Landscape";
    if (on) {
      if (!ph.hasAttribute("data-hss-loading")) ph.setAttribute("data-hss-loading", "");
      if (hint.style.display !== "none") hint.style.display = "none";
      if (skel && skel.getAttribute("data-shape") !== shape) { skel.remove(); skel = null; }
      if (!skel) {
        skel = skeleton(ph.ownerDocument, mode);
        skel.setAttribute("data-shape", shape);
        ph.appendChild(skel);
      }
      return;
    }
    if (ph.hasAttribute("data-hss-loading")) ph.removeAttribute("data-hss-loading");
    if (hint.style.display) hint.style.display = "";
    if (skel) skel.remove();
  }

  function place(container, el, after) {
    var order = after ? after.style.order : "0";
    if (el.style.order !== order) el.style.order = order;
    var next = after ? after.nextSibling : container.firstChild;
    if (next !== el) container.insertBefore(el, next);
  }

  // loading(id): the section has not answered yet, so its slot shows a skeleton row; modes: section id -> "Portrait" | "Landscape".
  function render(container, enabled, names, lang, loading, modes) {
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
      setLoading(ph, !!(loading && loading(id)), modes && modes[id]);
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

  var FULL = "position: absolute; top: 0; right: 0; bottom: 0; left: 0;";
  var UPCOMING_WAIT = ".verticalSection.UpcomingShows:not([data-hss-ready]) .itemsContainer, .verticalSection.UpcomingMovies:not([data-hss-ready]) .itemsContainer";
  var HOME_CSS = [
    // Hidden with its space kept until upcomingNow rewrote the cards; the delayed animation shows the raw cards if the script never runs.
    UPCOMING_WAIT + " { visibility: hidden; animation: hssReveal 0s 1.5s forwards; }",
    "@keyframes hssReveal { to { visibility: visible; } }",
    ".hssSkeleton { overflow: hidden; flex-wrap: nowrap; pointer-events: none; }",
    ".hssSkelImg { background: rgba(255, 255, 255, 0.06); border-radius: 0.3em; }",
    ".hssDiscoverCard .cardImageContainer { border-radius: 0.4em; overflow: hidden; }",
    ".hssStudioCard .cardImageContainer { background: #1c2029; box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12); }",
    ".hssStudioCard:hover .cardImageContainer, .hssStudioCard:focus .cardImageContainer { background: #2a303c; }",
    ".hssLogo { position: absolute; top: 20%; right: 14%; bottom: 20%; left: 14%; background: center / contain no-repeat; }",
    ".hssName, .hssGenreName { " + FULL + " display: flex; align-items: center; justify-content: center; padding: 0 0.6em; text-align: center; color: #fff; line-height: 1.15; }",
    ".hssName { font-weight: 700; font-size: 1.25em; }",
    ".hssCount { position: absolute; right: 0.6em; bottom: 0.4em; font-size: 0.85em; font-weight: 600; color: rgba(255, 255, 255, 0.7); }",
    ".hssList { position: fixed; top: 0; right: 0; bottom: 0; left: 0; z-index: 1000; overflow-y: auto; background: #101010; padding: 1em 0 3em; }",
    ".hssListHead { display: flex; align-items: center; justify-content: space-between; }",
    ".hssList .itemsContainer { display: flex; flex-wrap: wrap; }",
    ".hssDuo { " + FULL + " background: var(--hss-light); isolation: isolate; filter: brightness(0.8); }",
    ".hssDuoImg { " + FULL + " background: center / cover no-repeat; filter: grayscale(1) contrast(1.15); mix-blend-mode: multiply; }",
    ".hssDuoTint { " + FULL + " background: var(--hss-dark); mix-blend-mode: lighten; }",
    ".hssGenreName { font-weight: 800; font-size: 1.6em; text-shadow: 0 1px 8px rgba(0, 0, 0, 0.7); }",
    '.verticalSection.MyList .sectionTitle::after, [data-hss-placeholder="MyList"] .sectionTitle::after { content: "favorite"; font-family: "Material Icons"; font-weight: normal; font-style: normal; font-size: 0.9em; line-height: 1; vertical-align: -0.12em; margin-left: 0.35em; display: inline-block; text-transform: none; letter-spacing: normal; -webkit-font-feature-settings: "liga"; font-feature-settings: "liga"; }'
  ].join("\n");

  function homeStyles(doc) {
    if (doc.getElementById("hss-home-style")) return;
    var style = doc.createElement("style");
    style.id = "hss-home-style";
    style.textContent = HOME_CSS;
    (doc.head || doc.documentElement).appendChild(style);
  }

  function upcomingNow(container, lang, now) {
    var secs = container.querySelectorAll(".verticalSection.UpcomingShows:not([data-hss-ready]), .verticalSection.UpcomingMovies:not([data-hss-ready])");
    for (var i = 0; i < secs.length; i++) {
      var items = secs[i].querySelector(".itemsContainer");
      if (!items || !items.children.length) continue;
      upcomingSeries(secs[i]);
      upcomingDates(secs[i], lang, now);
      secs[i].setAttribute("data-hss-ready", "");
    }
  }

  // Per-user stale-while-revalidate cache: the rows paint at once from the last load, the stale parts reload in the background.
  var CACHE_PREFIX = "hss-home:";
  var RECENT_FRESH_MS = 60000;
  var RECENT_TTL_MS = 600000;
  var DISCOVER_TTL_MS = 86400000;
  var PARTS = ["base", "recent", "discover"];
  var SLOT_WAIT_MS = 4000;
  var SLOT_SLACK_MS = 1000;

  function readCache(storage, userId) {
    try {
      var raw = storage && storage.getItem(CACHE_PREFIX + userId);
      var v = raw ? JSON.parse(raw) : null;
      return v && typeof v === "object" ? v : null;
    } catch (e) {
      return null;
    }
  }

  function writeCache(storage, userId, value) {
    try {
      if (storage) storage.setItem(CACHE_PREFIX + userId, JSON.stringify(value));
    } catch (e) {}
  }

  function ageOf(part, now) {
    return part && typeof part.at === "number" ? now - part.at : Infinity;
  }

  function netsKey(cards) {
    return JSON.stringify([(cards && cards.studios) || null, (cards && cards.networks) || null]);
  }

  // nets: the server's cards ({studios, networks}); the discover rows reload when they changed.
  function staleParts(c, now, nets) {
    return PARTS.filter(function (p) {
      if (p === "discover") return ageOf(c && c.discover, now) > DISCOVER_TTL_MS || c.discover.nets !== netsKey(nets);
      return ageOf(c && c[p], now) > RECENT_FRESH_MS;
    });
  }

  // The plugin's paging shows a spinner and pins the scroll at the bottom while it asks for a next page, even when page 1 held every section.
  function endPaging(meta, container) {
    if (!meta) return false;
    if (!meta.Finished && meta.UsePagination && meta.Page === 1 && meta.ResultsPerPage > 0) {
      var n = container.querySelectorAll('.verticalSection[data-page="1"]').length;
      if (n && n < meta.ResultsPerPage) meta.Finished = true;
    }
    return meta.Finished === true;
  }

  function usable(c, now) {
    return !!c && ageOf(c.base, now) <= RECENT_TTL_MS && ageOf(c.recent, now) <= RECENT_TTL_MS && ageOf(c.discover, now) < Infinity;
  }

  function slim(item) {
    return { Id: item.Id, Name: item.Name, ServerId: item.ServerId, Type: item.Type, MediaType: item.MediaType, ProductionYear: item.ProductionYear, ImageTags: item.ImageTags, BackdropImageTags: item.BackdropImageTags };
  }

  function slimRows(rows) {
    var out = {};
    for (var k in rows) out[k] = (rows[k] || []).map(slim);
    return out;
  }

  function cardsOf(win) {
    return { studios: win.HssStudios, networks: win.HssNetworks };
  }

  var BACK_KEYS = { Escape: 1, Esc: 1, Backspace: 1, GoBack: 1, BrowserBack: 1 };
  var BACK_CODES = { 27: 1, 8: 1, 461: 1, 10009: 1 };
  var LIST_PAGE = '<div class="hssListHead padded-left padded-right"><h2 class="sectionTitle sectionTitle-cards"></h2>' +
    '<button type="button" is="paper-icon-button-light" class="hssListClose paper-icon-button-light" aria-label="Close"><span class="material-icons close" aria-hidden="true"></span></button></div>' +
    '<div is="emby-itemscontainer" class="itemsContainer vertical-wrap padded-left padded-right"></div>';

  // A full-screen list of exactly the card's titles, with the user's session; Escape / back closes it.
  function openList(win, from, card) {
    var doc = win.document;
    var api = win.ApiClient;
    var ids = (card.getAttribute("data-hss-items") || "").split(",").filter(Boolean);
    closeList(doc, false);
    if (!api || !ids.length) return;
    var tv = doc.documentElement.classList.contains("layout-tv");
    var page = doc.createElement("div");
    page.className = "hssList focuscontainer";
    page.innerHTML = LIST_PAGE;
    page.querySelector("h2").textContent = (card.getAttribute("aria-label") || "") + " \u00b7 " + ids.length;
    page.hssFrom = from;
    doc.body.appendChild(page);
    api.getJSON(api.getUrl("Users/" + api.getCurrentUserId() + "/Items", { Ids: ids.join(","), SortBy: "SortName", Fields: "PrimaryImageAspectRatio", EnableImageTypes: "Primary,Backdrop,Thumb", ImageTypeLimit: 1, EnableUserData: false })).then(function (r) {
      if (!page.parentNode) return;
      var list = page.querySelector(".itemsContainer");
      var items = (r && r.Items) || [];
      for (var i = 0; i < items.length; i++) {
        var c = recentCard(doc, items[i], api, tv);
        c.setAttribute("data-index", String(i));
        list.appendChild(c);
      }
      if (tv && list.firstChild) list.firstChild.focus();
    }, function () {});
  }

  function closeList(doc, refocus) {
    var page = doc.querySelector(".hssList");
    if (!page) return false;
    page.remove();
    if (refocus && page.hssFrom && page.hssFrom.isConnected) page.hssFrom.focus();
    return true;
  }

  function listEvents(win) {
    var doc = win.document;
    doc.addEventListener("click", function (e) {
      var t = e.target && e.target.closest ? e.target : null;
      if (!t) return;
      var from = t.closest(".hssOpen");
      var card = from && from.closest(".hssDiscoverCard[data-hss-items]");
      if (card) {
        e.preventDefault();
        e.stopPropagation();
        openList(win, from, card);
        return;
      }
      if (t.closest(".hssListClose")) closeList(doc, true);
      else if (t.closest(".hssList .card")) win.setTimeout(function () { closeList(doc, false); }, 0);
    }, true);
    win.addEventListener("keydown", function (e) {
      var t = e.target;
      if (!doc.querySelector(".hssList")) {
        if ((e.key === "Enter" || e.key === " ") && t && t.classList && t.classList.contains("hssOpen") && t.tagName !== "BUTTON") {
          e.preventDefault();
          t.click();
        }
        return;
      }
      if (!BACK_KEYS[e.key] && !BACK_CODES[e.keyCode]) return;
      if (t && /^(INPUT|TEXTAREA)$/.test(t.tagName)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      closeList(doc, true);
    }, true);
    win.addEventListener("popstate", function () { closeList(doc, false); });
    win.addEventListener("hashchange", function () { closeList(doc, false); });
  }

  function start(win) {
    var doc = win.document;
    listEvents(win);
    lockHomeSettings(doc);
    homeStyles(doc);
    var mem = {};
    var inflight = null;
    var pending = null;
    var late = null;
    var timer = null;
    var since = 0;

    function storage() {
      try {
        return win.localStorage;
      } catch (e) {
        return null;
      }
    }

    function langNow() {
      return doc.documentElement.lang || win.navigator.language || "en";
    }

    function state(userId, lang) {
      var c = mem[userId] || readCache(storage(), userId);
      return c && c.lang === lang ? c : null;
    }

    function fetchParts(api, userId, lang, parts) {
      var jobs = parts.map(function (p) {
        if (p === "base") {
          return Promise.all([
            api.getJSON(api.getUrl("ModularHomeViews/UserSettings", { userId: userId })),
            api.getJSON(api.getUrl("ModularHomeViews/Sections", { language: lang }))
          ]).then(function (r) { return { enabled: enabledOf(r[0]), names: namesOf(r[1]), modes: modesOf(r[1]) }; });
        }
        if (p === "recent") return loadRecent(api, userId).then(function (rows) { return { rows: slimRows(rows) }; });
        var cards = cardsOf(win);
        return loadDiscover(api, userId, cards).then(function (rows) { return { rows: rows, nets: netsKey(cards) }; });
      });
      return Promise.all(jobs.map(function (j) { return j.then(null, function () { return null; }); })).then(function (vals) {
        var old = state(userId, lang) || {};
        var next = { lang: lang, base: old.base, recent: old.recent, discover: old.discover };
        var at = Date.now();
        for (var i = 0; i < parts.length; i++) {
          if (!vals[i]) continue;
          vals[i].at = at;
          next[parts[i]] = vals[i];
        }
        mem[userId] = next;
        writeCache(storage(), userId, next);
        return next;
      });
    }

    function liveContainers() {
      var containers = doc.querySelectorAll(".homeSectionsContainer");
      var live = [];
      for (var i = 0; i < containers.length; i++) if (containers[i].querySelector(".verticalSection[data-page]")) live.push(containers[i]);
      return live;
    }

    // When each plugin section answered (resource timing, nothing patched); a slot whose answer is missing stays a skeleton, at most SLOT_WAIT_MS.
    var answered = {};
    var seen = typeof win.WeakMap === "function" ? new win.WeakMap() : null;
    if (win.PerformanceObserver) {
      try {
        new win.PerformanceObserver(function (list) {
          var entries = list.getEntries();
          var hit = false;
          for (var i = 0; i < entries.length; i++) {
            var m = /\/HomeScreen\/Section\/([A-Za-z0-9]+)/.exec(entries[i].name);
            if (m) {
              answered[m[1]] = entries[i].startTime;
              hit = true;
            }
          }
          if (hit) soon();
        }).observe({ type: "resource", buffered: true });
      } catch (e) {}
    }

    function firstSeen(container) {
      var t = win.performance && win.performance.now ? win.performance.now() : Date.now();
      if (!seen) return t;
      if (!seen.has(container)) seen.set(container, t);
      return seen.get(container);
    }

    function loadingFor(container, st) {
      var since = firstSeen(container);
      var now = win.performance && win.performance.now ? win.performance.now() : Date.now();
      var listed = endPaging(win.HssPageMeta, container);
      return function (id) {
        if (RECENT_ROWS.indexOf(id) !== -1) return !st.recent;
        if (DISCOVER_ROWS.indexOf(id) !== -1) return !st.discover;
        if (listed && !realOf(container, id).length) return false;
        if (now - since > SLOT_WAIT_MS) return false;
        return !(answered[id] >= since - SLOT_SLACK_MS);
      };
    }

    function paint(api, st) {
      if (!st || !st.base || !st.base.enabled || !st.base.enabled.length) return;
      var live = liveContainers();
      if (!live.length) return;
      var lang = st.lang;
      var enabled = withDiscover(withRecent(st.base.enabled));
      var names = {};
      for (var k in st.base.names) names[k] = st.base.names[k];
      for (var n = 0; n < RECENT_ROWS.length; n++) names[RECENT_ROWS[n]] = recentName(RECENT_ROWS[n], lang);
      for (var d = 0; d < DISCOVER_ROWS.length; d++) names[DISCOVER_ROWS[d]] = discoverName(DISCOVER_ROWS[d], lang);
      var recent = (st.recent && st.recent.rows) || {};
      var discover = (st.discover && st.discover.rows) || {};
      observer.disconnect();
      try {
        for (var j = 0; j < live.length; j++) {
          var loading = loadingFor(live[j], st);
          var modes = st.base.modes || {};
          render(live[j], enabled, names, lang, loading, modes);
          for (var r = 0; r < RECENT_ROWS.length; r++) syncRecent(live[j], RECENT_ROWS[r], recent[RECENT_ROWS[r]] || [], names[RECENT_ROWS[r]], api);
          for (var s = 0; s < DISCOVER_ROWS.length; s++) syncDiscover(live[j], DISCOVER_ROWS[s], discover[DISCOVER_ROWS[s]] || [], names[DISCOVER_ROWS[s]], api);
          render(live[j], enabled, names, lang, loading, modes);
        }
      } finally {
        watch();
      }
    }

    function quick() {
      var lang = langNow();
      var containers = doc.querySelectorAll(".homeSectionsContainer");
      observer.disconnect();
      try {
        for (var i = 0; i < containers.length; i++) {
          endPaging(win.HssPageMeta, containers[i]);
          upcomingNow(containers[i], lang);
        }
      } finally {
        watch();
      }
      var api = win.ApiClient;
      if (!api || !win.HssPageMeta || !api.getCurrentUserId || !api.getCurrentUserId()) return;
      var st = state(api.getCurrentUserId(), lang);
      if (usable(st, Date.now())) paint(api, st);
      refresh(api, st, lang);
    }

    function refresh(api, st, lang) {
      var parts = staleParts(st, Date.now(), cardsOf(win));
      if (!parts.length || inflight || !liveContainers().length) return;
      inflight = fetchParts(api, api.getCurrentUserId(), lang, parts).then(function (next) {
        inflight = null;
        paint(api, next);
      }, function () { inflight = null; });
    }

    function soon() {
      if (!pending) pending = win.setTimeout(function () { pending = null; quick(); }, 50);
    }

    function run() {
      timer = null;
      since = 0;
      var api = win.ApiClient;
      if (!api || !win.HssPageMeta || !api.getCurrentUserId || !api.getCurrentUserId()) return;
      var live = liveContainers();
      if (!live.length) return;
      var lang = langNow();
      observer.disconnect();
      try {
        for (var u = 0; u < live.length; u++) {
          upcomingSeries(live[u]);
          upcomingDates(live[u], lang);
        }
      } finally {
        watch();
      }
      var st = state(api.getCurrentUserId(), lang);
      if (usable(st, Date.now())) paint(api, st);
      refresh(api, st, lang);
      // Turns the remaining skeletons into hints once the slot wait is over.
      if (live.some(function (c) { return c.querySelector("[data-hss-loading]"); }) && !late) late = win.setTimeout(function () { late = null; run(); }, SLOT_WAIT_MS);
    }

    function watch() {
      observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
    }

    var observer = new win.MutationObserver(function () {
      quick();
      var now = Date.now();
      if (!since) since = now;
      if (timer) win.clearTimeout(timer);
      timer = win.setTimeout(run, Math.max(0, Math.min(SETTLE_MS, since + MAX_WAIT_MS - now)));
    });
    watch();
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      LOCKED_SELECTOR: LOCKED_SELECTOR, lockHomeSettings: lockHomeSettings, homeStyles: homeStyles, render: render, upcomingDates: upcomingDates, upcomingSeries: upcomingSeries, upcomingNow: upcomingNow,
      hintFor: hintFor, namesOf: namesOf, modesOf: modesOf, enabledOf: enabledOf, start: start, pickMovies: pickMovies, seriesCandidates: seriesCandidates, pickSeries: pickSeries,
      completeCollection: completeCollection, loadRecent: loadRecent, withRecent: withRecent, recentName: recentName, syncRecent: syncRecent, RECENT_DAYS: RECENT_DAYS, RECENT_LIMIT: RECENT_LIMIT,
      withDiscover: withDiscover, discoverName: discoverName, countBy: countBy, pickBackdrops: pickBackdrops, genreColors: genreColors, loadDiscover: loadDiscover, syncDiscover: syncDiscover, DISCOVER_LIMIT: DISCOVER_LIMIT,
      readCache: readCache, writeCache: writeCache, staleParts: staleParts, netsKey: netsKey, assignCards: assignCards, usable: usable, RECENT_TTL_MS: RECENT_TTL_MS, DISCOVER_TTL_MS: DISCOVER_TTL_MS
    };
  } else if (window.MutationObserver && document.body) {
    start(window);
  } else if (window.MutationObserver) {
    document.addEventListener("DOMContentLoaded", function () { start(window); });
  }
})();
