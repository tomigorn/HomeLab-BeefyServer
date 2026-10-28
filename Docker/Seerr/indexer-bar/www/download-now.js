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
    },
    de: {
      button: "⚡ Jetzt herunterladen — zum Tagstrompreis",
      started: "Wartet auf die Nacht 🌙. Startet stattdessen sofort, zum teureren Tagstrompreis.",
      searching: "Noch nichts gefunden. Sucht jetzt und lädt sofort, zum Tagstrompreis.",
      busy: "Startet …",
      done: { started: "✅ Gestartet — lädt jetzt zum Tagstrompreis", searching: "✅ Suche läuft — was sie findet, lädt sofort", downloading: "Lädt bereits — nichts zu tun", available: "Bereits heruntergeladen — nichts zu tun", "not-found": "Noch nicht in Radarr/Sonarr: zuerst anfragen" },
      error: "❌ Fehlgeschlagen — nochmals versuchen oder einen Admin fragen",
    },
  };
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
    var badge = doc.querySelector(".media-status");
    return badge && GERMAN.test(badge.textContent.trim()) ? "de" : "en";
  }

  function button(doc, t, title, state, note) {
    var b = doc.createElement("button");
    b.type = "button";
    b.id = BOX;
    b.className = LOOK;
    b.style.cssText = COLOURS;
    b.textContent = note ? t.button + " · " + note : t.button;
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
      var s = doc.createElement("span");
      s.id = BOX;
      s.className = "inline-flex items-center rounded-md border px-4 py-2 text-sm font-semibold text-white mr-2";
      s.style.cssText = "border-color:#4b5563;white-space:normal;";
      s.setAttribute("role", "status");
      s.textContent = t.done[r.state] || t.error;
      swap(b, s);
      var nl = typeof window !== "undefined" && window.hgNightLabel;
      if (nl && nl.release && r.released && r.released.length) nl.release(r.released);
    }, function () {
      swap(b, button(doc, t, title, state, t.error));
    });
  }

  function attach(doc, loc) {
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
      if (!r || current !== mine || ["started", "searching"].indexOf(r.st.state) < 0) return;
      var row = doc.querySelector(".media-header .media-actions");
      if (!row || doc.getElementById(BOX)) return;
      mine.el = button(doc, TEXT[langOf(doc, r.user)], title, r.st.state);
      row.insertBefore(mine.el, row.firstChild);
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
