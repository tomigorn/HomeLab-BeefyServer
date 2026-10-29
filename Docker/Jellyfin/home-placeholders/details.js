(function () {
  // Details overlay for titles not in the library (Popular pages, Top 10 rows, Coming soon), Jellyfin README "Details overlay".
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

  function tr(lang, key, vars) {
    var s = TEXT[langOf(lang)][key] || TEXT.en[key] || key;
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
      var node = build(doc, info, { lang: cur.lang, line: cur.opts.line, fallback: cur.opts.fallback, actions: cur.opts.actions, loading: loading });
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
    win.addEventListener("hashchange", function () { close(false); });
    win.addEventListener("message", function (e) { if (cur) onMessage(win, e); });

    var api = { open: open, close: close, refresh: refresh, isOpen: function () { return !!cur; } };
    win.HssDetails = api;
    return api;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { CSS: CSS, trailers: trailers, embedUrl: embedUrl, build: build, start: start };
  } else if (!window.HssDetails) {
    start(window);
  }
})();
