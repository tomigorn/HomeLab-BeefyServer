(function () {
  // Details overlay for titles not in the library (Popular pages, Top 10 rows, Coming soon), Jellyfin README "Details overlay".
  // Also "Play on TV" for owned titles (window.HssPlayTv, README "Play on TV"): the user's own remote-controllable sessions, PlayNow with the user's token.
  // Data: GET hss-popular/details?id=<opaque id> (precomputed nightly, see scripts/tmdb_details.py); without it a minimal overlay from the caller's data.
  var API = "hss-popular/details";
  var TMDB_IMG = "https://image.tmdb.org/t/p/";
  var YT_EMBED = "https://www.youtube-nocookie.com";
  var IMG_RE = /^\/[A-Za-z0-9_.-]+$/;
  var KEY_RE = /^[A-Za-z0-9_-]{11}$/;
  var IMDB_RE = /^tt\d+$/;
  var FALLBACK_POSTER = /^https:\/\/image\.tmdb\.org\/t\/p\/w\d+\/[A-Za-z0-9_.-]+$|^[^"'()\\\s]*\/HomeScreen\/CachedImage\/[0-9A-Za-z]+(\?[A-Za-z0-9=&_.-]*)?$/;
  var TYPE_RANK = { Trailer: 0, Teaser: 1 };
  var PLAYER_ERRORS = { 100: 1, 101: 1, 150: 1 };
  var MAX_TRAILERS = 5;
  var CAST = 10;
  var TEXT = {
    en: {
      close: "Close", cast: "Cast", trailer: "Trailer", play: "Play trailer", youtube: "Open on YouTube", seasons1: "1 season", seasons: "{n} seasons",
      perEpisode: "{n} min per episode", critics: "Rotten Tomatoes", audience: "Audience", hours: "{h} h {m} min", minutes: "{m} min", loading: "Loading details…"
    },
    de: {
      close: "Schliessen", cast: "Besetzung", trailer: "Trailer", play: "Trailer abspielen", youtube: "Auf YouTube öffnen", seasons1: "1 Staffel", seasons: "{n} Staffeln",
      perEpisode: "{n} Min. pro Folge", critics: "Rotten Tomatoes", audience: "Publikum", hours: "{h} Std. {m} Min.", minutes: "{m} Min.", loading: "Details werden geladen…"
    }
  };

  function langOf(lang) {
    return String(lang || "").toLowerCase().indexOf("de") === 0 ? "de" : "en";
  }

  var PLAY_TEXT = {
    en: {
      play: "\u25B6 Play on TV", playLabel: "Play on TV", pick: "Play on", none: "No TV online. Open Moonfin on the TV first.", looking: "Looking for TVs\u2026", starting: "Starting\u2026",
      started: "Playing on {d}", failed: "Could not start playback on {d}", nothing: "Nothing to play", busy: "now playing: {t}", unreachable: "Could not list the TVs", cont: "Continue on TV"
    },
    de: {
      play: "\u25B6 Auf TV abspielen", playLabel: "Auf TV abspielen", pick: "Abspielen auf", none: "Kein TV online. Zuerst Moonfin auf dem TV öffnen.", looking: "TVs werden gesucht\u2026", starting: "Wird gestartet\u2026",
      started: "Läuft auf {d}", failed: "Wiedergabe auf {d} fehlgeschlagen", nothing: "Nichts zum Abspielen", busy: "spielt gerade: {t}", unreachable: "TVs konnten nicht geladen werden", cont: "Auf TV weiterschauen"
    }
  };

  function tr(lang, key, vars, table) {
    table = table || TEXT;
    var s = table[langOf(lang)][key] || table.en[key] || key;
    for (var k in vars || {}) s = s.split("{" + k + "}").join(String(vars[k]));
    return s;
  }

  function pick(obj, lang) {
    if (!obj) return "";
    return (langOf(lang) === "de" && obj.de) || obj.en || obj.de || "";
  }

  function img(size, path) {
    return typeof path === "string" && IMG_RE.test(path) ? TMDB_IMG + size + path : null;
  }

  function el(doc, tag, cls, text) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function bg(e, url) {
    if (url) e.style.backgroundImage = 'url("' + url + '")';
  }

  function link(doc, cls, href, text) {
    var a = el(doc, "a", cls, text);
    a.setAttribute("href", href);
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
    return a;
  }

  // Trailer type first, then the UI language, then English, larger first.
  function trailers(list, lang) {
    var ui = langOf(lang);
    var rank = function (l) { return l === ui ? 0 : l === "en" ? 1 : 2; };
    return (list || []).filter(function (x) { return x && typeof x.key === "string" && KEY_RE.test(x.key) && x.type in TYPE_RANK; })
      .map(function (x, i) { return { x: x, i: i }; })
      .sort(function (a, b) {
        return TYPE_RANK[a.x.type] - TYPE_RANK[b.x.type] || rank(a.x.lang) - rank(b.x.lang) || (b.x.size || 0) - (a.x.size || 0) || a.i - b.i;
      })
      .slice(0, MAX_TRAILERS).map(function (e) { return e.x; });
  }

  function embedUrl(key, origin) {
    if (typeof key !== "string" || !KEY_RE.test(key)) return null;
    return YT_EMBED + "/embed/" + key + "?enablejsapi=1&origin=" + encodeURIComponent(origin) + "&autoplay=1&rel=0&playsinline=1";
  }

  function runtime(info, lang) {
    var n = info.rt;
    if (typeof n !== "number" || n <= 0) return null;
    if (info.k === "tv") return tr(lang, "perEpisode", { n: n });
    return n >= 60 ? tr(lang, "hours", { h: Math.floor(n / 60), m: n % 60 }) : tr(lang, "minutes", { m: n });
  }

  function meta(info, lang, fallback) {
    var out = [];
    var year = info ? info.y : fallback && fallback.year;
    if (year) out.push(String(year));
    if (!info) return out;
    if (info.k === "tv" && typeof info.ns === "number" && info.ns > 0) out.push(info.ns === 1 ? tr(lang, "seasons1") : tr(lang, "seasons", { n: info.ns }));
    var rt = runtime(info, lang);
    if (rt) out.push(rt);
    if (info.cert) out.push(String(info.cert));
    var genres = (info.g && ((langOf(lang) === "de" && info.g.de && info.g.de.length && info.g.de) || info.g.en)) || [];
    if (genres.length) out.push(genres.join(", "));
    return out;
  }

  function scores(info, lang) {
    var s = (info && info.sc) || {};
    var out = [];
    var num = function (x) { return typeof x === "number" && isFinite(x); };
    if (info && info.k === "movie" && num(s.imdb)) out.push(["imdb", "IMDb " + s.imdb.toFixed(1)]);
    if (num(s.rtc)) out.push(["rtc", tr(lang, "critics") + " " + Math.round(s.rtc) + " %"]);
    if (num(s.rta)) out.push(["rta", tr(lang, "audience") + " " + Math.round(s.rta) + " %"]);
    if (num(s.tmdb)) out.push(["tmdb", "TMDB " + s.tmdb.toFixed(1)]);
    return out;
  }

  // info: the backend's details or null (minimal overlay); opts: {lang, line, fallback: {title, year, poster (full URL)}, actions(doc) -> element}.
  function build(doc, info, opts) {
    var lang = opts.lang;
    var fb = opts.fallback || {};
    var root = el(doc, "div", "hssDetails focuscontainer");
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    var title = (info && pick(info.t, lang)) || fb.title || "";
    root.setAttribute("aria-label", title);
    var backdrop = el(doc, "div", "hssDetailsBackdrop");
    bg(backdrop, info && img("w1280", info.b));
    root.appendChild(backdrop);
    var close = el(doc, "button", "hssDetailsClose paper-icon-button-light");
    close.setAttribute("type", "button");
    close.setAttribute("aria-label", tr(lang, "close"));
    close.appendChild(el(doc, "span", "material-icons close")).setAttribute("aria-hidden", "true");
    root.appendChild(close);
    var main = el(doc, "div", "hssDetailsMain");
    var poster = el(doc, "div", "hssDetailsPoster");
    var purl = info ? img("w342", info.p && ((langOf(lang) === "de" && info.p.de) || info.p.en)) : null;
    if (!purl && typeof fb.poster === "string" && FALLBACK_POSTER.test(fb.poster)) purl = fb.poster;
    bg(poster, purl);
    main.appendChild(poster);
    var body = el(doc, "div", "hssDetailsBody");
    body.appendChild(el(doc, "h1", "hssDetailsTitle", title));
    var m = el(doc, "div", "hssDetailsMeta");
    meta(info, lang, fb).forEach(function (t) { m.appendChild(el(doc, "span", null, t)); });
    body.appendChild(m);
    if (opts.line) body.appendChild(el(doc, "div", "hssDetailsLine", opts.line));
    var sc = scores(info, lang);
    if (sc.length) {
      var chips = el(doc, "div", "hssDetailsScores");
      sc.forEach(function (x) { chips.appendChild(el(doc, "span", "hssScore hssScore-" + x[0], x[1])); });
      body.appendChild(chips);
    }
    var actions = el(doc, "div", "hssDetailsActions");
    if (opts.actions) {
      var a = opts.actions(doc);
      if (a) actions.appendChild(a);
    }
    if (opts.play && !doc.documentElement.classList.contains("layout-tv")) {
      var pb = playButton(doc, opts.play, lang);
      if (pb) actions.appendChild(pb);
    }
    body.appendChild(actions);
    if (info) {
      var text = pick(info.o, lang);
      if (text) body.appendChild(el(doc, "p", "hssDetailsOverview", text));
      var links = el(doc, "div", "hssDetailsLinks");
      if (typeof info.imdb === "string" && IMDB_RE.test(info.imdb)) links.appendChild(link(doc, "hssDetailsLink", "https://www.imdb.com/title/" + info.imdb + "/", "IMDb"));
      if (typeof info.id === "number" && (info.k === "movie" || info.k === "tv")) links.appendChild(link(doc, "hssDetailsLink", "https://www.themoviedb.org/" + info.k + "/" + info.id, "TMDB"));
      if (links.firstChild) body.appendChild(links);
    } else if (opts.loading) {
      body.appendChild(el(doc, "p", "hssDetailsLoading", tr(lang, "loading")));
    }
    main.appendChild(body);
    root.appendChild(main);
    var list = info ? trailers(info.v, lang) : [];
    if (list.length) {
      var sec = el(doc, "div", "hssDetailsTrailer");
      sec.appendChild(el(doc, "h2", "sectionTitle", tr(lang, "trailer")));
      var box = el(doc, "div", "hssTrailerBox");
      sec.appendChild(box);
      box.hssList = list;
      box.hssThumb = img("w780", info.b) || purl;
      thumb(doc, box, lang, false);
      root.appendChild(sec);
    }
    var cast = info ? (info.cast || []).slice(0, CAST) : [];
    if (cast.length) {
      var cs = el(doc, "div", "hssDetailsCastRow");
      cs.appendChild(el(doc, "h2", "sectionTitle", tr(lang, "cast")));
      var row = el(doc, "div", "hssCastRow focuscontainer-x");
      cast.forEach(function (c) {
        var b = el(doc, "button", "hssCast");
        b.setAttribute("type", "button");
        b.setAttribute("aria-label", (c.n || "") + (c.c ? ", " + c.c : ""));
        var ph = el(doc, "div", "hssCastPhoto");
        bg(ph, img("w185", c.p));
        b.appendChild(ph);
        b.appendChild(el(doc, "div", "hssCastName", c.n || ""));
        b.appendChild(el(doc, "div", "hssCastRole", c.c || ""));
        row.appendChild(b);
      });
      cs.appendChild(row);
      root.appendChild(cs);
    }
    return root;
  }

  // Click to play: only the thumbnail until pressed (no YouTube request, no focus-trapping iframe on a TV before that).
  function thumb(doc, box, lang, failed) {
    while (box.firstChild) box.removeChild(box.firstChild);
    var t = el(doc, "div", "hssTrailerThumb");
    bg(t, box.hssThumb);
    box.appendChild(t);
    if (failed) {
      box.appendChild(link(doc, "hssTrailerYoutube", "https://www.youtube.com/watch?v=" + box.hssList[0].key, tr(lang, "youtube")));
      return;
    }
    var play = el(doc, "button", "hssTrailerPlay");
    play.setAttribute("type", "button");
    play.setAttribute("aria-label", tr(lang, "play") + ": " + (box.hssList[0].name || ""));
    play.appendChild(el(doc, "span", "material-icons play_arrow")).setAttribute("aria-hidden", "true");
    box.appendChild(play);
  }

  function player(win, box, lang, at) {
    var doc = win.document;
    var item = box.hssList[at];
    if (!item) {
      box.hssAt = -1;
      thumb(doc, box, lang, true);
      return;
    }
    var src = embedUrl(item.key, win.location.origin);
    if (!src) return player(win, box, lang, at + 1);
    box.hssAt = at;
    while (box.firstChild) box.removeChild(box.firstChild);
    var f = el(doc, "iframe", "hssTrailerFrame");
    f.setAttribute("src", src);
    f.setAttribute("title", item.name || tr(lang, "trailer"));
    f.setAttribute("allow", "autoplay; encrypted-media; picture-in-picture; fullscreen");
    f.setAttribute("allowfullscreen", "");
    f.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    box.appendChild(f);
    // The IFrame API answers only after "listening"; it is sent until the player speaks.
    var tries = 0;
    var hello = function () {
      if (!f.isConnected || f.hssHeard || tries++ > 20 || !f.contentWindow) return;
      try {
        f.contentWindow.postMessage(JSON.stringify({ event: "listening", id: at, channel: "widget" }), YT_EMBED);
        f.contentWindow.postMessage(JSON.stringify({ event: "command", func: "addEventListener", args: ["onError"], id: at, channel: "widget" }), YT_EMBED);
      } catch (e) {}
      win.setTimeout(hello, 500);
    };
    f.addEventListener("load", hello);
  }

  function onMessage(win, e) {
    if (e.origin !== YT_EMBED) return;
    var doc = win.document;
    var frames = doc.querySelectorAll(".hssDetails iframe.hssTrailerFrame");
    for (var i = 0; i < frames.length; i++) {
      var f = frames[i];
      if (f.contentWindow !== e.source) continue;
      f.hssHeard = true;
      var data;
      try {
        data = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
      } catch (err) {
        return;
      }
      if (data && data.event === "onError" && PLAYER_ERRORS[data.info]) {
        var box = f.closest(".hssTrailerBox");
        player(win, box, box.hssLang, box.hssAt + 1);
      }
      return;
    }
  }

  // target: {id, type: "Movie" | "Series" | "Episode", name}.
  function playButton(doc, target, lang) {
    if (!target || !target.id) return null;
    styles(doc);
    var b = el(doc, "button", "hssPlayTv", tr(lang, "play", null, PLAY_TEXT));
    b.setAttribute("type", "button");
    b.setAttribute("data-hss-play", String(target.id));
    b.setAttribute("data-hss-play-type", target.type || "Movie");
    b.setAttribute("aria-label", (target.name ? target.name + ": " : "") + tr(lang, "playLabel", null, PLAY_TEXT));
    return b;
  }

  function sessionLabel(s) {
    return [s.DeviceName, s.Client].filter(Boolean).join(" \u00b7 ");
  }

  // The user's own other active sessions that take remote commands (ControllableByUserId also lists other users' TVs); the browser's own session is not a target.
  function remoteTargets(list, ownDevice, me) {
    return (list || []).filter(function (s) { return s && s.Id && s.UserId === me && s.SupportsRemoteControl && s.IsActive !== false && (!ownDevice || s.DeviceId !== ownDevice); });
  }

  // Jellyfin here has EnableLegacyAuthorization off: X-Emby-Token is refused, only the Authorization header counts.
  function auth(a) {
    return { Authorization: 'MediaBrowser Token="' + a.accessToken() + '"' };
  }

  function getJson(win, a, path, query) {
    return win.fetch(a.getUrl(path, query), { headers: auth(a) }).then(function (r) {
      if (r.status !== 200) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function firstItem(r) {
    return (r && r.Items && r.Items[0]) || null;
  }

  function ticksOf(it) {
    var t = it && it.UserData && it.UserData.PlaybackPositionTicks;
    return typeof t === "number" && t > 0 ? t : 0;
  }

  // Film, episode: from its resume point. Series: the episode in progress, else Next Up, else the first regular episode (not a special).
  // Season: its episode in progress, else its first unwatched episode, else its first episode.
  function resolvePlay(win, a, target) {
    var u = a.getCurrentUserId();
    if (target.type === "current") return resolveCurrent(win, a, target);
    if (target.type === "Season") return resolveSeason(win, a, target.id);
    if (target.type !== "Series") return getJson(win, a, "Users/" + u + "/Items/" + target.id).then(function (it) { return { id: target.id, ticks: ticksOf(it) }; });
    return getJson(win, a, "Users/" + u + "/Items/Resume", { ParentId: target.id, Limit: 1, IncludeItemTypes: "Episode", Recursive: true }).then(firstItem).then(function (it) {
      return it || getJson(win, a, "Shows/NextUp", { SeriesId: target.id, UserId: u, Limit: 1 }).then(firstItem);
    }).then(function (it) {
      return it || getJson(win, a, "Shows/" + target.id + "/Episodes", { UserId: u, Limit: 50, IsMissing: false }).then(function (r) {
        var eps = (r && r.Items) || [];
        return eps.filter(function (e) { return e.ParentIndexNumber > 0; })[0] || eps[0] || null;
      });
    }).then(function (it) { return it && it.Id ? { id: it.Id, ticks: ticksOf(it) } : null; });
  }

  function resolveSeason(win, a, id) {
    var u = a.getCurrentUserId();
    return getJson(win, a, "Users/" + u + "/Items/Resume", { ParentId: id, Limit: 1, IncludeItemTypes: "Episode", Recursive: true }).then(firstItem).then(function (it) {
      if (it) return it;
      return getJson(win, a, "Users/" + u + "/Items/" + id).then(function (season) {
        if (!season || !season.SeriesId) return null;
        return getJson(win, a, "Shows/" + season.SeriesId + "/Episodes", { SeasonId: id, UserId: u, IsMissing: false }).then(function (r) {
          var eps = (r && r.Items) || [];
          return eps.filter(function (e) { return !(e.UserData && e.UserData.Played); })[0] || eps[0] || null;
        });
      });
    }).then(function (it) { return it && it.Id ? { id: it.Id, ticks: ticksOf(it) } : null; });
  }

  // Jellyfin's own rule (playbackManager transcodingOffsetTicks): a progressive transcode without copyTimestamps starts its clock at StartTimeTicks; HLS and direct play run on the item's timeline.
  function streamOffset(src) {
    if (!src || /^blob:/i.test(src)) return 0;
    var path = src.split(/[?#]/)[0];
    var query = src.indexOf("?") < 0 ? "" : src.slice(src.indexOf("?") + 1).split("#")[0];
    if (/\.m3u8$/i.test(path) || /(^|&)copytimestamps=true(&|$)/i.test(query) || /(^|&)static=true(&|$)/i.test(query)) return 0;
    var m = /(?:^|&)starttimeticks=(\d+)(?:&|$)/i.exec(query);
    return m ? Number(m[1]) : 0;
  }

  function localVideo(doc) {
    var all = doc.querySelectorAll("video");
    for (var i = 0; i < all.length; i++) if (all[i].classList.contains("htmlvideoplayer")) return all[i];
    return all[0] || null;
  }

  // The item playing here: the browser's own session, else the id in a direct stream URL. Position read when the TV is picked.
  function resolveCurrent(win, a, target) {
    var u = a.getCurrentUserId();
    var own = typeof a.deviceId === "function" ? a.deviceId() : null;
    var at = target.at || 0;
    var src = target.src || "";
    return getJson(win, a, "Sessions", { DeviceId: own }).then(null, function () { return []; }).then(function (list) {
      var s = (list || []).filter(function (x) { return x && x.UserId === u && x.DeviceId === own && x.NowPlayingItem && x.NowPlayingItem.Id; })[0];
      var m = /\/videos\/([0-9a-f-]{8,})\//i.exec(src);
      var id = s ? s.NowPlayingItem.Id : m ? m[1] : null;
      if (!at && s && s.PlayState && s.PlayState.PositionTicks > 0) at = s.PlayState.PositionTicks;
      return id ? { id: id, ticks: at, current: true } : null;
    });
  }

  function currentPosition(doc) {
    var v = localVideo(doc);
    if (!v) return { at: 0, src: "" };
    var t = Number(v.currentTime) || 0;
    var src = v.currentSrc || v.src || "";
    return { at: Math.max(0, Math.floor(t * 1e7)) + streamOffset(src), src: src };
  }


  // Native Jellyfin pages (README "Play on TV"): a TV icon on title details, cards, episode rows and in the player.
  var PLAYABLE = { Movie: 1, Series: 1, Season: 1, Episode: 1, Video: 1 };

  var phCache = { from: null, ids: {} };
  function placeholders(win) {
    var h = win.HssTrendingHide || null;
    if (phCache.from === h && h) return phCache.ids;
    var ids = {};
    if (h) (h.items || []).forEach(function (i) { ids[i] = true; });
    phCache = { from: h, ids: ids };
    return ids;
  }

  function iconButton(doc, cls, icon, target, lang, name, key) {
    var b = doc.createElement("button");
    b.setAttribute("type", "button");
    b.className = cls;
    var label = tr(lang, key || "playLabel", null, PLAY_TEXT);
    b.setAttribute("title", label);
    b.setAttribute("aria-label", (name ? name + ": " : "") + label);
    b.setAttribute("data-hss-play", target.id);
    b.setAttribute("data-hss-play-type", target.type);
    var i = el(doc, "span", icon + " tv");
    i.setAttribute("aria-hidden", "true");
    b.appendChild(i);
    return b;
  }

  function textOf(node, sel) {
    var t = node.querySelector(sel);
    return t ? t.textContent.trim() : "";
  }

  function wanted(node, ph) {
    var id = node.getAttribute("data-id");
    return !!(id && PLAYABLE[node.getAttribute("data-type")] && !ph[id] && !node.querySelector("[data-hss-play]") && !node.classList.contains("hssComingCard") && !node.closest(".hssDetails"));
  }

  // data-hss-tv: "-<id>" = no icon for this item. React grids re-render cards and reuse them for other items, so a present icon is checked against the id.
  function settled(node, id, cls) {
    if (node.getAttribute("data-hss-tv") === "-" + id) return true;
    var mine = node.querySelector(cls);
    if (mine && mine.getAttribute("data-hss-play") === id && mine.getAttribute("data-hss-play-type") === node.getAttribute("data-type")) return true;
    if (mine) mine.remove();
    return false;
  }

  function skip(node, id) {
    node.setAttribute("data-hss-tv", "-" + id);
  }

  function hover(win) {
    return !(win && win.matchMedia) || win.matchMedia("(hover: hover)").matches;
  }

  // Desktop cards carry Jellyfin's hover overlay; phone cards have none, so the icon is its own tap target in the image.
  function decorateCard(doc, card, lang, ph) {
    var id = card.getAttribute("data-id");
    if (settled(card, id, ".hssCardTv")) return;
    if (!wanted(card, ph)) return skip(card, id);
    var box = card.querySelector(".cardScalable");
    if (!box) return;
    card.setAttribute("data-hss-tv", id);
    var target = { id: id, type: card.getAttribute("data-type") };
    var named = card.querySelector("a[aria-label]");
    var name = textOf(card, ".cardText bdi") || (named && named.getAttribute("aria-label")) || card.getAttribute("aria-label") || "";
    var over = hover(doc.defaultView) && box.querySelector(".cardOverlayContainer");
    if (over) {
      var br = over.querySelector(".cardOverlayButton-br");
      if (!br) br = over.appendChild(el(doc, "div", "cardOverlayButton-br flex"));
      br.insertBefore(iconButton(doc, "cardOverlayButton cardOverlayButton-hover paper-icon-button-light hssCardTv", "material-icons cardOverlayButtonIcon cardOverlayButtonIcon-hover", target, lang, name), br.firstChild);
    } else {
      box.appendChild(iconButton(doc, "cardOverlayButton paper-icon-button-light hssCardTv hssCardTvTouch", "material-icons cardOverlayButtonIcon", target, lang, name));
    }
  }

  function decorateRow(doc, row, lang, ph) {
    var id = row.getAttribute("data-id");
    if (settled(row, id, ".hssListTv")) return;
    if (!wanted(row, ph)) return skip(row, id);
    var box = row.querySelector(".listViewUserDataButtons");
    if (!box) return;
    row.setAttribute("data-hss-tv", id);
    var target = { id: id, type: row.getAttribute("data-type") };
    box.insertBefore(iconButton(doc, "listItemButton paper-icon-button-light hssListTv", "material-icons", target, lang, textOf(row, ".listItemBodyText")), box.firstChild);
  }

  // Injected cards (Top 10, Popular): the native card icon inside the poster; target: {id, type, name}.
  function cardIcon(doc, target, lang) {
    if (!target || !target.id) return null;
    styles(doc);
    var t = { id: String(target.id), type: target.type || "Movie" };
    if (!hover(doc.defaultView)) return iconButton(doc, "cardOverlayButton paper-icon-button-light hssCardTv hssCardTvTouch", "material-icons cardOverlayButtonIcon", t, lang, target.name);
    var over = el(doc, "div", "hssCardTvOverlay");
    over.appendChild(el(doc, "div", "cardOverlayButton-br flex")).appendChild(iconButton(doc, "cardOverlayButton cardOverlayButton-hover paper-icon-button-light hssCardTv", "material-icons cardOverlayButtonIcon cardOverlayButtonIcon-hover", t, lang, target.name));
    return over;
  }

  function detailsId(win) {
    var m = /[?&]id=([0-9A-Za-z-]+)/.exec(win.location.hash || "");
    return m ? m[1] : null;
  }

  function detailButton(doc, target, lang) {
    var b = iconButton(doc, "button-flat detailButton emby-button hssDetailTv", "material-icons detailButton-icon", target, lang);
    var c = el(doc, "div", "detailButton-content");
    c.appendChild(b.firstChild);
    c.appendChild(el(doc, "span", "detailButton-text hssDetailTvLabel", tr(lang, "playLabel", null, PLAY_TEXT)));
    b.appendChild(c);
    return b;
  }

  // One page per route; Jellyfin keeps earlier pages hidden (.hide) and may reuse one for the next id.
  function decorateDetails(win, lang, ph, items) {
    var doc = win.document;
    var id = detailsId(win);
    var pages = doc.querySelectorAll("#itemDetailPage:not(.hide)");
    for (var i = 0; i < pages.length; i++) {
      var row = pages[i].querySelector(".mainDetailButtons");
      if (!row) continue;
      var b = row.querySelector(".hssDetailTv");
      if (!id) { if (b) b.remove(); continue; }
      if (b && b.getAttribute("data-hss-play") === id) continue;
      if (b) b.remove();
      lookup(win, items, id).then(placeDetail(win, row, id, lang, ph));
    }
  }

  function placeDetail(win, row, id, lang, ph) {
    return function (it) {
      if (!it || !row.isConnected || detailsId(win) !== id || row.querySelector(".hssDetailTv")) return;
      if (!PLAYABLE[it.Type] || ph[id] || (it.SeriesId && ph[it.SeriesId])) return;
      var b = detailButton(win.document, { id: id, type: it.Type }, lang);
      var after = row.querySelector(".btnReplay") || row.querySelector(".btnPlay");
      if (after) row.insertBefore(b, after.nextSibling);
      else row.insertBefore(b, row.firstChild);
    };
  }

  // Cards on the page of a Trending placeholder (its seasons) stay plain; until that item is known they wait for the next pass.
  function pageState(win, items, ph, later) {
    var id = detailsId(win);
    var page = id && win.document.querySelector("#itemDetailPage:not(.hide)");
    if (!page) return { ok: function () { return true; } };
    var it = items[id] && items[id].value;
    if (it === undefined) lookup(win, items, id).then(later);
    var blocked = it === undefined || !!(ph[id] || (it && it.SeriesId && ph[it.SeriesId]));
    return {
      ok: function (node) {
        if (!blocked || !page.contains(node)) return true;
        if (it !== undefined) {
          var mine = node.querySelector("[data-hss-play]:not(.hssPlayTv)");
          if (mine) mine.remove();
          skip(node, node.getAttribute("data-id"));
        }
        return false;
      }
    };
  }

  function lookup(win, items, id) {
    if (!items[id]) {
      var a = win.ApiClient;
      var p = items[id] = getJson(win, a, "Users/" + a.getCurrentUserId() + "/Items/" + id).then(function (it) { p.value = it || null; return it; }, function () {
        p.value = null;
        win.setTimeout(function () { if (items[id] === p) delete items[id]; }, 30000);
        return null;
      });
    }
    return items[id];
  }

  function decorateOsd(doc, lang) {
    var bars = doc.querySelectorAll("#videoOsdPage .osdControls .buttons");
    for (var i = 0; i < bars.length; i++) {
      if (bars[i].querySelector(".hssOsdTv")) continue;
      var b = iconButton(doc, "autoSize paper-icon-button-light hssOsdTv", "xlargePaperIconButton material-icons", { id: "current", type: "current" }, lang, "", "cont");
      bars[i].insertBefore(b, bars[i].querySelector(".btnVideoOsdSettings"));
    }
  }

  function toast(win, text) {
    var doc = win.document;
    var old = doc.querySelector(".hssToast");
    if (old) old.remove();
    var t = el(doc, "div", "hssToast", text);
    t.setAttribute("role", "status");
    doc.body.appendChild(t);
    win.setTimeout(function () { t.remove(); }, 4000);
  }

  // Leaving the player route is Jellyfin's own stop: it reports PlaybackStopped with the position, which saves the resume point.
  function stopLocal(win) {
    var back = win.document.querySelector(".headerBackButton:not(.hide)");
    if (back) back.click();
    else win.history.back();
  }

  function native(win, lang, tv, stats) {
    var doc = win.document;
    var items = {};
    var pending = null;
    function scan() {
      pending = null;
      stats.scans++;
      if (tv() || !win.ApiClient || !doc.body) return;
      var l = lang();
      var ph = placeholders(win);
      var page = pageState(win, items, ph, later);
      var cards = doc.querySelectorAll(".card[data-id]");
      for (var i = 0; i < cards.length; i++) if (page.ok(cards[i])) decorateCard(doc, cards[i], l, ph);
      var rows = doc.querySelectorAll(".listItem[data-id]");
      for (var j = 0; j < rows.length; j++) if (page.ok(rows[j])) decorateRow(doc, rows[j], l, ph);
      decorateDetails(win, l, ph, items);
      decorateOsd(doc, l);
      if (obs) obs.takeRecords();
    }
    function later() {
      if (!pending) pending = win.setTimeout(scan, 120);
    }
    var obs = win.MutationObserver ? new win.MutationObserver(function (list) {
      for (var i = 0; i < list.length; i++) {
        var t = list[i].target;
        if (t.closest && t.closest(".hssPlayMenu, .hssToast")) continue;
        var added = list[i].addedNodes;
        for (var k = 0; k < added.length; k++) if (added[k].nodeType === 1) return later();
      }
    }) : null;
    if (obs) obs.observe(doc.documentElement, { childList: true, subtree: true });
    doc.addEventListener("viewshow", later, true);
    win.addEventListener("hashchange", later);
    win.addEventListener("popstate", later);
    later();
    return scan;
  }

  function playUrl(a, sessionId, play) {
    var q = { playCommand: "PlayNow", itemIds: play.id };
    if (play.ticks) q.startPositionTicks = play.ticks;
    return a.getUrl("Sessions/" + encodeURIComponent(sessionId) + "/Playing", q);
  }

  function playMenu(doc, lang) {
    var m = el(doc, "div", "hssPlayMenu focuscontainer");
    m.setAttribute("role", "dialog");
    m.setAttribute("aria-label", tr(lang, "pick", null, PLAY_TEXT));
    m.appendChild(el(doc, "div", "hssPlayMenuTitle", tr(lang, "pick", null, PLAY_TEXT)));
    m.appendChild(el(doc, "div", "hssPlayMenuList"));
    var st = el(doc, "div", "hssPlayMenuStatus", tr(lang, "looking", null, PLAY_TEXT));
    st.setAttribute("role", "status");
    m.appendChild(st);
    return m;
  }

  function fillMenu(doc, m, lang, sessions) {
    var list = m.querySelector(".hssPlayMenuList");
    while (list.firstChild) list.removeChild(list.firstChild);
    sessions.forEach(function (s) {
      var b = el(doc, "button", "hssPlayTarget");
      b.setAttribute("type", "button");
      b.appendChild(el(doc, "span", "hssPlayTargetName", sessionLabel(s)));
      if (s.NowPlayingItem && s.NowPlayingItem.Name) b.appendChild(el(doc, "span", "hssPlayTargetSub", tr(lang, "busy", { t: s.NowPlayingItem.Name }, PLAY_TEXT)));
      b.hssSession = s;
      list.appendChild(b);
    });
    m.querySelector(".hssPlayMenuStatus").textContent = sessions.length ? "" : tr(lang, "none", null, PLAY_TEXT);
  }

  function placeMenu(win, m, btn) {
    m.style.top = m.style.bottom = "";
    var r = btn.getBoundingClientRect();
    var w = win.innerWidth || 0;
    var h = win.innerHeight || 0;
    m.style.left = Math.max(8, Math.min(r.left, w - (m.offsetWidth || 280) - 8)) + "px";
    if (h && r.bottom + 220 > h && r.top > 220) m.style.bottom = Math.max(8, h - r.top + 4) + "px";
    else m.style.top = Math.max(8, r.bottom + 4) + "px";
  }

  var CSS = [
    ".hssDetails { position: fixed; top: 0; right: 0; bottom: 0; left: 0; z-index: 1300; overflow-y: auto; overflow-x: hidden; background: #101010; color: #fff; padding-bottom: 3em; }",
    ".hssDetailsBackdrop { position: absolute; top: 0; left: 0; right: 0; height: 62vh; background: #181818 center top / cover no-repeat; }",
    ".hssDetailsBackdrop::after { content: ''; position: absolute; inset: 0; background: linear-gradient(to bottom, rgba(16, 16, 16, 0.25), rgba(16, 16, 16, 0.75) 60%, #101010); }",
    ".hssDetailsMain, .hssDetailsTrailer, .hssDetailsCastRow { padding-left: max(3.3%, 1em); padding-right: max(3.3%, 1em); }",
    ".hssDetailsClose { position: fixed; top: 0.8em; right: 1em; z-index: 2; width: 2.2em; height: 2.2em; padding: 0; border: 0; font-size: 1.4em; color: #fff; background: rgba(0, 0, 0, 0.55); border-radius: 50%; cursor: pointer; }",
    ".hssDetailsClose:focus { background: #00a4dc; }",
    ".hssDetailsMain { position: relative; display: grid; grid-template-columns: minmax(10em, 16em) 1fr; gap: 2em; align-items: start; padding-top: 28vh; }",
    ".hssDetailsPoster { width: 100%; aspect-ratio: 2 / 3; border-radius: 0.4em; background: #22252c center / cover no-repeat; box-shadow: 0 0.4em 1.5em rgba(0, 0, 0, 0.6); }",
    ".hssDetailsBody { min-width: 0; padding-top: 2em; }",
    ".hssDetailsTitle { margin: 0 0 0.3em; font-size: 2.6em; line-height: 1.1; }",
    ".hssDetailsMeta { display: flex; flex-wrap: wrap; gap: 0.4em 1.2em; opacity: 0.9; }",
    ".hssDetailsLine { margin-top: 0.6em; font-weight: 600; color: #00a4dc; }",
    ".hssDetailsScores { display: flex; flex-wrap: wrap; gap: 0.5em; margin-top: 0.9em; }",
    ".hssScore { padding: 0.2em 0.65em; border-radius: 1em; background: rgba(255, 255, 255, 0.12); font-size: 0.9em; font-weight: 600; }",
    ".hssScore-imdb { background: #f5c518; color: #000; } .hssScore-rtc, .hssScore-rta { background: rgba(250, 50, 10, 0.75); } .hssScore-tmdb { background: rgba(1, 180, 228, 0.6); }",
    ".hssDetailsActions .hssTopActions { width: auto; max-width: 24em; margin-top: 1.1em; }",
    ".hssDetailsActions .hssTopBtn { font-size: 1em; padding: 0.5em 0.9em; }",
    ".hssDetailsOverview { max-width: 50em; line-height: 1.5; margin: 1.2em 0 0; }",
    ".hssDetailsLoading { opacity: 0.6; }",
    ".hssDetailsLinks { display: flex; gap: 1em; margin-top: 1em; }",
    ".hssDetailsLink { color: #00a4dc; text-decoration: none; font-weight: 600; } .hssDetailsLink:hover, .hssDetailsLink:focus { text-decoration: underline; }",
    ".hssDetailsTrailer, .hssDetailsCastRow { position: relative; margin-top: 2em; }",
    ".hssTrailerBox { position: relative; width: 100%; max-width: 56em; aspect-ratio: 16 / 9; border-radius: 0.4em; overflow: hidden; background: #000; }",
    ".hssTrailerThumb { position: absolute; inset: 0; background: #22252c center / cover no-repeat; filter: brightness(0.7); }",
    ".hssTrailerPlay { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 4.5em; height: 4.5em; border-radius: 50%; border: 0; background: rgba(0, 0, 0, 0.65); color: #fff; font-size: 1.2em; cursor: pointer; }",
    ".hssTrailerPlay .material-icons { font-size: 2.6em; }",
    ".hssTrailerPlay:hover, .hssTrailerPlay:focus { background: #00a4dc; outline: none; }",
    ".hssTrailerFrame { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }",
    ".hssTrailerYoutube { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); padding: 0.6em 1.2em; border-radius: 0.3em; background: rgba(0, 0, 0, 0.75); color: #fff; text-decoration: none; font-weight: 600; }",
    ".hssCastRow { display: flex; gap: 1em; overflow-x: auto; padding: 0.4em 0 1em; scrollbar-width: thin; }",
    ".hssCast { flex: 0 0 8.5em; display: block; padding: 0; border: 0; background: none; color: inherit; font: inherit; text-align: center; cursor: default; }",
    ".hssCast:focus { outline: none; } .hssCast:focus .hssCastPhoto { box-shadow: 0 0 0 0.2em #00a4dc; }",
    ".hssCastPhoto { width: 8.5em; aspect-ratio: 2 / 3; border-radius: 0.4em; background: #22252c center / cover no-repeat; }",
    ".hssCastName { margin-top: 0.4em; font-weight: 600; font-size: 0.9em; }",
    ".hssCastRole { font-size: 0.8em; opacity: 0.7; }",
    ".layout-tv .hssDetailsMain { padding-top: 22vh; }",
    ".hssPlayTv { display: inline-flex; align-items: center; margin-top: 0.45em; padding: 0.45em 0.8em; border: 0; border-radius: 0.3em; background: #00a4dc; color: #fff; font: inherit; font-size: 0.9em; font-weight: 600; cursor: pointer; white-space: nowrap; }",
    ".hssPlayTv:hover, .hssPlayTv:focus { background: #0cb5f0; outline: none; box-shadow: 0 0 0 0.15em #fff; }",
    ".hssDetailsActions .hssPlayTv { font-size: 1em; padding: 0.55em 1em; margin-top: 1.1em; }",
    ".layout-tv .hssPlayTv { display: none !important; }",
    ".layout-tv .hssDetailTv, .layout-tv .hssCardTv, .layout-tv .hssListTv, .layout-tv .hssOsdTv { display: none !important; }",
    ".hssDetailTvLabel { display: none; white-space: nowrap; } @media (min-width: 50em) { .hssDetailTvLabel { display: block; } }",
    ".hssCardTvTouch { position: absolute; top: 0; left: 0; z-index: 2; }",
    ".hssCardTvOverlay { position: absolute; inset: 0; z-index: 2; opacity: 0; transition: opacity 0.2s; pointer-events: none; background: rgba(0, 0, 0, 0.5); }",
    ".hssCardTvOverlay .cardOverlayButton-br { position: absolute; right: 0; bottom: 0; } .hssCardTvOverlay .hssCardTv { pointer-events: auto; }",
    ".hssCardTvOverlay .cardOverlayButton-hover { opacity: 1; }",
    ":hover > .hssCardTvOverlay, :focus-within > .hssCardTvOverlay, a:focus .hssCardTvOverlay, a:hover .hssCardTvOverlay { opacity: 1; }",
    ".hssToast { position: fixed; z-index: 1500; left: 50%; bottom: 5em; transform: translateX(-50%); max-width: 90vw; padding: 0.7em 1.2em; border-radius: 0.4em; background: #202020; color: #fff; font-weight: 600; box-shadow: 0 0.4em 1.5em rgba(0, 0, 0, 0.7); }",
    ".hssPlayMenu { position: fixed; z-index: 1400; min-width: 14em; max-width: min(22em, 92vw); padding: 0.6em; border-radius: 0.4em; background: #202020; color: #fff; box-shadow: 0 0.4em 1.5em rgba(0, 0, 0, 0.7); }",
    ".hssPlayMenuTitle { font-weight: 700; margin-bottom: 0.4em; }",
    ".hssPlayTarget { display: block; width: 100%; margin: 0.15em 0; padding: 0.5em 0.6em; border: 0; border-radius: 0.3em; background: rgba(255, 255, 255, 0.08); color: inherit; font: inherit; text-align: left; cursor: pointer; }",
    ".hssPlayTarget:hover, .hssPlayTarget:focus { background: #00a4dc; outline: none; } .hssPlayTarget[disabled] { opacity: 0.5; cursor: default; }",
    ".hssPlayTargetName { display: block; font-weight: 600; } .hssPlayTargetSub { display: block; font-size: 0.8em; opacity: 0.75; }",
    ".hssPlayMenuStatus { margin-top: 0.3em; font-size: 0.9em; opacity: 0.85; } .hssPlayMenuStatus:empty { display: none; }",
    "@media (max-width: 700px) { .hssDetailsMain { grid-template-columns: 1fr; gap: 1em; padding-top: 18vh; } .hssDetailsPoster { width: 45vw; margin: 0 auto; } .hssDetailsBody { padding-top: 0; } .hssDetailsTitle { font-size: 1.8em; } .hssDetailsBackdrop { height: 40vh; } }"
  ].join("\n");

  function styles(doc) {
    if (doc.getElementById("hss-details-style")) return;
    var s = doc.createElement("style");
    s.id = "hss-details-style";
    s.textContent = CSS;
    (doc.head || doc.documentElement).appendChild(s);
  }

  var BACK_KEYS = { Escape: 1, Esc: 1, Backspace: 1, GoBack: 1, BrowserBack: 1 };
  var BACK_CODES = { 27: 1, 8: 1, 461: 1, 10009: 1 };

  // No history entry for the overlay (README "Details overlay"): back keys are caught here, route changes close it.
  function start(win) {
    var doc = win.document;
    var cur = null;
    var seq = 0;
    var watch = null;
    var menu = null;

    function lang() { return doc.documentElement.lang || win.navigator.language || "en"; }
    function tv() { return doc.documentElement.classList.contains("layout-tv"); }

    function focusIn(node) {
      var order = [".hssDetailsActions button:not([disabled])", ".hssTrailerPlay", ".hssDetailsClose"];
      for (var i = 0; i < order.length; i++) {
        var f = node.querySelector(order[i]);
        if (f) return f.focus();
      }
    }

    function show(info, loading) {
      var node = build(doc, info, { lang: cur.lang, line: cur.opts.line, fallback: cur.opts.fallback, actions: cur.opts.actions, play: cur.opts.play, loading: loading });
      var box = node.querySelector(".hssTrailerBox");
      if (box) box.hssLang = cur.lang;
      var had = cur.node && cur.node.contains(doc.activeElement);
      if (cur.node) cur.node.replaceWith(node);
      else doc.body.appendChild(node);
      cur.node = node;
      if (tv() || had || loading) focusIn(node);
    }

    function close(refocus) {
      seq++;
      if (watch) { win.clearInterval(watch); watch = null; }
      if (!cur) return false;
      var was = cur;
      cur = null;
      if (was.node) was.node.remove();
      if (refocus) {
        var back = was.opts.from && was.opts.from.isConnected ? was.opts.from : null;
        if (!back && was.opts.key) {
          var all = doc.querySelectorAll("[data-hss-key]");
          for (var i = 0; i < all.length && !back; i++) if (all[i].getAttribute("data-hss-key") === was.opts.key && !all[i].closest(".hide")) back = all[i];
        }
        if (back) back.focus();
      }
      return true;
    }

    function open(opts) {
      close(false);
      styles(doc);
      var mine = ++seq;
      cur = { opts: opts || {}, lang: lang(), href: win.location.href, node: null };
      show(null, !!cur.opts.id);
      watch = win.setInterval(function () { if (cur && win.location.href !== cur.href) close(false); }, 250);
      var a = win.ApiClient;
      if (!cur.opts.id || !a || !a.accessToken) return;
      win.fetch(a.getUrl(API, { id: cur.opts.id }), { headers: { "X-Emby-Token": a.accessToken() } })
        .then(function (r) { return r.status === 200 ? r.json() : null; })
        .then(function (b) { if (mine === seq && cur) show(b && b.details && typeof b.details === "object" ? b.details : null, false); },
          function () { if (mine === seq && cur) show(null, false); });
    }

    function refresh() {
      if (!cur || !cur.node || !cur.opts.actions) return;
      var box = cur.node.querySelector(".hssDetailsActions");
      var had = box.contains(doc.activeElement);
      while (box.firstChild) box.removeChild(box.firstChild);
      var a = cur.opts.actions(doc);
      if (a) box.appendChild(a);
      if (had || tv()) focusIn(cur.node);
    }

    function closeMenu(refocus) {
      if (!menu) return false;
      var m = menu;
      menu = null;
      if (m.timer) win.clearTimeout(m.timer);
      if (m.watch) win.clearInterval(m.watch);
      m.node.remove();
      if (refocus && m.btn.isConnected) m.btn.focus();
      return true;
    }

    function openMenu(btn) {
      if (menu && menu.btn === btn) return closeMenu(true);
      closeMenu(false);
      var a = win.ApiClient;
      if (!a || !a.accessToken) return;
      styles(doc);
      var m = { btn: btn, lang: lang(), href: win.location.href, target: { id: btn.getAttribute("data-hss-play"), type: btn.getAttribute("data-hss-play-type") } };
      m.node = playMenu(doc, m.lang);
      menu = m;
      doc.body.appendChild(m.node);
      placeMenu(win, m.node, btn);
      // Jellyfin's router changes the route without hashchange: close on any URL change.
      m.watch = win.setInterval(function () { if (menu === m && win.location.href !== m.href) closeMenu(false); }, 250);
      var me = a.getCurrentUserId();
      var own = typeof a.deviceId === "function" ? a.deviceId() : null;
      getJson(win, a, "Sessions", { ControllableByUserId: me }).then(function (list) {
        if (menu !== m) return;
        fillMenu(doc, m.node, m.lang, remoteTargets(list, own, me));
        if (m.btn.isConnected) placeMenu(win, m.node, m.btn);
        var f = m.node.querySelector(".hssPlayTarget");
        if (f) f.focus();
      }, function () {
        if (menu === m) m.node.querySelector(".hssPlayMenuStatus").textContent = tr(m.lang, "unreachable", null, PLAY_TEXT);
      });
    }

    function choose(tb) {
      var m = menu;
      var a = win.ApiClient;
      var s = tb.hssSession;
      if (!m || !a || !s) return;
      var all = m.node.querySelectorAll(".hssPlayTarget");
      var set = function (off) { for (var i = 0; i < all.length; i++) all[i].disabled = off; };
      var status = m.node.querySelector(".hssPlayMenuStatus");
      var name = sessionLabel(s);
      var target = m.target.type === "current" ? Object.assign(currentPosition(doc), m.target) : m.target;
      set(true);
      status.textContent = tr(m.lang, "starting", null, PLAY_TEXT);
      resolvePlay(win, a, target).then(function (play) {
        if (!play) {
          status.textContent = tr(m.lang, "nothing", null, PLAY_TEXT);
          set(false);
          return;
        }
        return win.fetch(playUrl(a, s.Id, play), { method: "POST", headers: auth(a) }).then(function (r) {
          if (r.status < 200 || r.status > 299) throw new Error("HTTP " + r.status);
          if (play.current) {
            toast(win, tr(m.lang, "started", { d: name }, PLAY_TEXT));
            stopLocal(win);
          }
          if (menu !== m) return;
          status.textContent = tr(m.lang, "started", { d: name }, PLAY_TEXT);
          m.timer = win.setTimeout(function () { if (menu === m) closeMenu(true); }, 4000);
        });
      }).then(null, function () {
        if (menu !== m) return;
        status.textContent = tr(m.lang, "failed", { d: name }, PLAY_TEXT);
        set(false);
      });
    }

    doc.addEventListener("click", function (e) {
      var t = e.target && e.target.closest ? e.target : null;
      if (!t) return;
      var pb = t.closest("button[data-hss-play]");
      var tb = !pb && menu && t.closest(".hssPlayMenu .hssPlayTarget");
      if (pb || tb) {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (pb) openMenu(pb);
        else choose(tb);
        return;
      }
      if (menu && !t.closest(".hssPlayMenu")) closeMenu(false);
    }, true);
    win.addEventListener("keydown", function (e) {
      if (!menu) return;
      if (!BACK_KEYS[e.key] && !BACK_CODES[e.keyCode]) return;
      if (e.target && /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      closeMenu(true);
    }, true);

    doc.addEventListener("click", function (e) {
      var t = e.target && e.target.closest ? e.target : null;
      if (!t || !cur || !t.closest(".hssDetails")) return;
      if (t.closest(".hssDetailsClose")) {
        e.preventDefault();
        close(true);
        return;
      }
      var play = t.closest(".hssTrailerPlay");
      if (play) {
        e.preventDefault();
        var box = play.closest(".hssTrailerBox");
        player(win, box, box.hssLang || lang(), 0);
      }
    }, true);
    win.addEventListener("keydown", function (e) {
      if (!cur) return;
      if (!BACK_KEYS[e.key] && !BACK_CODES[e.keyCode]) return;
      if (e.target && /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      close(true);
    }, true);
    win.addEventListener("hashchange", function () { closeMenu(false); close(false); });
    win.addEventListener("popstate", function () { closeMenu(false); });
    // Fixed position: a scrolled page or row would leave the menu behind.
    doc.addEventListener("scroll", function (e) { if (menu && !(e.target && e.target.closest && e.target.closest(".hssPlayMenu"))) closeMenu(false); }, true);
    win.addEventListener("message", function (e) { if (cur) onMessage(win, e); });

    var stats = { scans: 0 };
    var scan = native(win, lang, tv, stats);
    var api = { open: open, close: close, refresh: refresh, isOpen: function () { return !!cur; } };
    win.HssDetails = api;
    win.HssPlayTv = Object.assign({}, PLAY, { scan: scan, stats: stats });
    return api;
  }

  var PLAY = { button: playButton, cardIcon: cardIcon, targets: remoteTargets, label: sessionLabel, offset: streamOffset };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { CSS: CSS, trailers: trailers, embedUrl: embedUrl, build: build, start: start, playTv: PLAY };
  } else if (!window.HssDetails) {
    start(window);
  }
})();
