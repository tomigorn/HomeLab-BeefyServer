// "Download now" button on Seerr's title page (README "Download now"): admins / manage-requests on any title, everyone else on a title with their own request. The backend (/_download-now/, download-now container) decides that against Seerr and holds every API key; a 403 or anything unexpected = no button.
(function () {
  "use strict";
  var API = "/_download-now/api";
  var BOX = "hg-download-now";
  var GERMAN = /^(Verarbeiten|Verfügbar|Teilweise verfügbar|Angefragt|Ausstehend|Gelöscht)$/;
  var TEXT = {
    en: {
      button: "⚡ Download now — at daytime power price",
      started: "Waiting for the night 🌙. Starts at once instead, at the higher daytime power price.",
      searching: "Nothing grabbed yet. Searches now and downloads at once, at the daytime power price.",
      busy: "Starting …",
      done: { started: "✅ Started — downloading now at daytime power price", searching: "✅ Searching now — what it grabs downloads at once", downloading: "Already downloading — nothing to do", available: "Already downloaded — nothing to do", "not-found": "Not in Radarr/Sonarr yet: request it first" },
      error: "❌ Failed — try again, or ask an admin",
      running: "🔍 Searching… (started {t})",
      queued: "🔍 Waiting for other searches ({n} ahead)",
      none: "🧭 No release found yet (searched {t}) — searched again in tonight's scheduled search (04:17); downloads at night when found",
      again: "⚡ Search again now — at daytime power price",
      grabbed: "✅ Downloading",
      locale: "en-GB",
    },
    de: {
      button: "⚡ Jetzt herunterladen — zum Tagstrompreis",
      started: "Wartet auf die Nacht 🌙. Startet stattdessen sofort, zum teureren Tagstrompreis.",
      searching: "Noch nichts gefunden. Sucht jetzt und lädt sofort, zum Tagstrompreis.",
      busy: "Startet …",
      done: { started: "✅ Gestartet — lädt jetzt zum Tagstrompreis", searching: "✅ Suche läuft — was sie findet, lädt sofort", downloading: "Lädt bereits — nichts zu tun", available: "Bereits heruntergeladen — nichts zu tun", "not-found": "Noch nicht in Radarr/Sonarr: zuerst anfragen" },
      error: "❌ Fehlgeschlagen — nochmals versuchen oder einen Admin fragen",
      running: "🔍 Suche läuft … (gestartet {t})",
      queued: "🔍 Wartet auf andere Suchen ({n} davor)",
      none: "🧭 Noch kein Release gefunden (gesucht {t}) — die geplante Suche heute Nacht (04:17) sucht weiter; lädt nachts, sobald gefunden",
      again: "⚡ Nochmals suchen — zum Tagstrompreis",
      grabbed: "✅ Lädt herunter",
      locale: "de-CH",
    },
  };
  var CHIP = "inline-flex items-center rounded-md border px-4 py-2 text-sm font-semibold text-white mr-2";
  var SECONDARY = "border-color:#f59e0b;background:transparent;color:#fcd34d;white-space:normal;text-align:left;";
  // Seerr's Button (src/components/Common/Button), amber so it never reads as the request button
  var LOOK = "inline-flex items-center justify-center border font-medium rounded-md focus:outline-none transition ease-in-out duration-150 cursor-pointer disabled:opacity-50 text-white px-4 py-2 text-sm button-md mr-2";
  var COLOURS = "border-color:#f59e0b;background:#b45309;font-weight:700;white-space:normal;text-align:left;";
  var me = null;
  var current = null;

  function request(method, url, body) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      x.__hgSkip = true;
      x.open(method, url);
      x.withCredentials = true;
      if (body !== undefined) x.setRequestHeader("Content-Type", "application/json");
      x.onload = function () {
        try { x.status === 200 ? resolve(JSON.parse(x.responseText)) : reject(new Error(String(x.status))); } catch (e) { reject(e); }
      };
      x.onerror = function () { reject(new Error("network")); };
      x.send(body === undefined ? null : JSON.stringify(body));
    });
  }

  function titleOf(loc) {
    var m = /^\/(movie|tv)\/(\d+)\/?$/.exec(loc.pathname);
    return m ? { type: m[1], tmdbId: +m[2], key: m[1] + "/" + m[2] } : null;
  }

  function langOf(doc, user) {
    var locale = user && user.settings && user.settings.locale;
    if (typeof locale === "string" && /^de/.test(locale)) return "de";
    var nl = typeof window !== "undefined" && window.hgNightLabel;
    if (nl && nl.lang) { try { return nl.lang(); } catch (e) { /* own detection */ } }
    var badge = doc.querySelector(".media-status span:not([data-hg-night-own]):not([data-hg-flag-own])");
    return badge && GERMAN.test(badge.textContent.trim()) ? "de" : "en";
  }

  function clock(iso, t) {
    var d = new Date(iso || "");
    if (isNaN(d.getTime())) return "?";
    try { return d.toLocaleTimeString(t.locale, { hour: "2-digit", minute: "2-digit" }); } catch (e) { return iso; }
  }

  function chip(doc, text, id) {
    var c = doc.createElement("span");
    if (id) c.id = BOX;
    c.className = CHIP;
    c.style.cssText = "border-color:#4b5563;white-space:normal;";
    c.setAttribute("role", "status");
    c.textContent = text;
    return c;
  }

  function running(st) {
    return !!(st && st.state === "searching" && st.search && (st.search.status === "queued" || st.search.status === "started"));
  }

  // what the title's button area shows for a backend answer; null = nothing
  function view(doc, t, title, st) {
    var s = st && st.search;
    if (st && st.state === "downloading" && st.now) return chip(doc, t.grabbed, true);
    if (running(st)) return chip(doc, s.status === "started" ? t.running.replace("{t}", clock(s.started, t)) : t.queued.replace("{n}", String(s.ahead || 0)), true);
    if (st && st.state === "searching" && s && s.last) {
      var box = doc.createElement("div");
      box.id = BOX;
      box.className = "mr-2";
      box.style.cssText = "display:inline-flex;flex-wrap:wrap;gap:.5rem;align-items:center;max-width:100%;";
      var c = chip(doc, t.none.replace("{t}", clock(s.last, t)), false);
      c.className = c.className.replace(/\s?mr-2\b/, "");
      box.appendChild(c);
      var again = button(doc, t, title, "searching", null, t.again);
      again.removeAttribute("id");
      again.className = LOOK.replace(/\s?mr-2\b/, "");
      again.style.cssText = SECONDARY;
      box.appendChild(again);
      return box;
    }
    if (st && ["started", "searching"].indexOf(st.state) >= 0) return button(doc, t, title, st.state);
    return null;
  }

  var POLL = 15000;
  function poll(doc, t, title, mine) {
    var ms = (typeof window !== "undefined" && window.hgDownloadNowPoll) || POLL;
    setTimeout(function () {
      if (current !== mine) return;
      request("GET", API + "?type=" + title.type + "&tmdb=" + title.tmdbId).then(function (st) {
        if (current !== mine) return;
        show(doc, t, title, mine, st);
      }, function () { if (current === mine) poll(doc, t, title, mine); });
    }, ms);
  }

  function show(doc, t, title, mine, st) {
    var el = view(doc, t, title, st);
    if (mine.el && mine.el.parentNode) {
      if (el) mine.el.parentNode.replaceChild(el, mine.el);
      else mine.el.parentNode.removeChild(mine.el);
    } else if (el) {
      var row = doc.querySelector(".media-header .media-actions");
      if (!row) return;
      row.insertBefore(el, row.firstChild);
    }
    mine.el = el;
    if (running(st)) poll(doc, t, title, mine);
  }

  function button(doc, t, title, state, note, label) {
    var b = doc.createElement("button");
    b.type = "button";
    b.id = BOX;
    b.className = LOOK;
    b.style.cssText = COLOURS;
    b.textContent = label || (note ? t.button + " · " + note : t.button);
    b.title = note || t[state];
    b.setAttribute("aria-label", t.button + ". " + (note || t[state]));
    b.addEventListener("click", function () { press(doc, b, t, title, state); });
    return b;
  }

  function swap(old, el) {
    if (current && current.el === old) current.el = el;
    if (old.parentNode) old.parentNode.replaceChild(el, old);
  }

  function press(doc, b, t, title, state) {
    if (b.disabled) return;
    b.disabled = true;
    b.textContent = t.busy;
    request("POST", API, { type: title.type, tmdbId: title.tmdbId }).then(function (r) {
      var nl0 = typeof window !== "undefined" && window.hgNightLabel;
      if (current && (running(r) || (r.state === "downloading" && r.now))) {
        var host = b.id === BOX ? b : b.parentNode;
        var el = view(doc, t, title, r);
        if (host && host.parentNode) host.parentNode.replaceChild(el, host);
        current.el = el;
        if (running(r)) poll(doc, t, title, current);
        if (nl0 && nl0.release && r.released && r.released.length) nl0.release(r.released);
        return;
      }
      var s = doc.createElement("span");
      s.id = BOX;
      s.className = "inline-flex items-center rounded-md border px-4 py-2 text-sm font-semibold text-white mr-2";
      s.style.cssText = "border-color:#4b5563;white-space:normal;";
      s.setAttribute("role", "status");
      s.textContent = t.done[r.state] || t.error;
      swap(b.id === BOX ? b : b.parentNode, s);
      var nl = typeof window !== "undefined" && window.hgNightLabel;
      if (nl && nl.release && r.released && r.released.length) nl.release(r.released);
    }, function () {
      swap(b.id === BOX ? b : b.parentNode, button(doc, t, title, state, t.error));
    });
  }

  function attach(doc, loc) {
    // Signed out (Seerr's login page): the next title asks who is signed in again.
    if (/^\/login(\/|$)/.test((loc && loc.pathname) || "")) me = null;
    var title = titleOf(loc);
    if (current && (!title || current.key !== title.key)) {
      if (current.el && current.el.parentNode) current.el.parentNode.removeChild(current.el);
      current = null;
    }
    if (!title) return;
    var row = doc.querySelector(".media-header .media-actions");
    if (!row) return;
    if (current) {
      if (current.el && current.el.parentNode !== row) row.insertBefore(current.el, row.firstChild);
      return;
    }
    var mine = current = { key: title.key, el: null };
    if (!me) me = request("GET", "/api/v1/auth/me").catch(function () { me = null; return null; });
    me.then(function (user) {
      if (!user || typeof user.id !== "number") return null;
      return request("GET", API + "?type=" + title.type + "&tmdb=" + title.tmdbId).then(function (st) { return { st: st, user: user }; });
    }).then(function (r) {
      if (!r || current !== mine || doc.getElementById(BOX)) return;
      show(doc, TEXT[langOf(doc, r.user)], title, mine, r.st);
    }).catch(function () { /* no button */ });
  }

  var api = { titleOf: titleOf, attach: attach };
  if (typeof window === "undefined") return;
  window.hgDownloadNow = api;
  try {
    if (!window.MutationObserver || !document.body) return;
    attach(document, window.location);
    new MutationObserver(function () {
      try { attach(document, window.location); } catch (e) { /* never break Seerr */ }
    }).observe(document.body, { childList: true, subtree: true });
  } catch (e) { /* never break Seerr */ }
})();
