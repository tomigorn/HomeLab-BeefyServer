// "Download now" button in Seerr's Manage panel (README "Download now"): admins / manage-requests only; the backend (/_download-now/, download-now container) checks that again against Seerr and holds every API key. Anything unexpected = no button.
(function () {
  "use strict";
  var API = "/_download-now/api";
  var BOX = "hg-download-now";
  var MAY = 2 | 16;
  var TITLE = /^(Manage (Movie|Series)|(Film|Serie) verwalten)$/;
  var OPEN = /^(Open in (Radarr|Sonarr)|In (Radarr|Sonarr) öffnen)$/;
  var TEXT = {
    en: {
      button: "⚡ Download now — at daytime power price",
      started: "Waiting for the night 🌙. Starts at once instead, at the higher daytime power price.",
      searching: "Nothing grabbed yet. Searches now and downloads at once, at the daytime power price.",
      busy: "Starting …",
      downloading: "Already downloading — nothing to do",
      done: { started: "✅ Started — downloading now at daytime power price", searching: "✅ Searching now — what it grabs downloads at once", downloading: "Already downloading — nothing to do", available: "Already downloaded — nothing to do", "not-found": "Not in Radarr/Sonarr yet: request it first" },
      error: "❌ Failed — try again, or open Radarr/Sonarr",
    },
    de: {
      button: "⚡ Jetzt herunterladen — zum Tagstrompreis",
      started: "Wartet auf die Nacht 🌙. Startet stattdessen sofort, zum teureren Tagstrompreis.",
      searching: "Noch nichts gefunden. Sucht jetzt und lädt sofort, zum Tagstrompreis.",
      busy: "Startet …",
      downloading: "Lädt bereits — nichts zu tun",
      done: { started: "✅ Gestartet — lädt jetzt zum Tagstrompreis", searching: "✅ Suche läuft — was sie findet, lädt sofort", downloading: "Lädt bereits — nichts zu tun", available: "Bereits heruntergeladen — nichts zu tun", "not-found": "Noch nicht in Radarr/Sonarr: zuerst anfragen" },
      error: "❌ Fehlgeschlagen — nochmals versuchen oder Radarr/Sonarr öffnen",
    },
  };
  var me = null;

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

  function canManage(user) {
    return !!user && typeof user.permissions === "number" && (user.permissions & MAY) !== 0;
  }

  function titleOf(loc) {
    var m = /^\/(movie|tv)\/(\d+)\/?$/.exec(loc.pathname);
    return m ? { type: m[1], tmdbId: +m[2] } : null;
  }

  function panels(doc) {
    var out = [], all = doc.querySelectorAll(".slideover");
    for (var i = 0; i < all.length; i++) {
      var h = all[i].querySelector("header h2");
      if (h && TITLE.test(h.textContent.trim())) out.push({ el: all[i], lang: /verwalten$/.test(h.textContent.trim()) ? "de" : "en" });
    }
    return out;
  }

  function place(panel, box) {
    var links = panel.querySelectorAll('a[target="_blank"]');
    for (var i = 0; i < links.length; i++) {
      if (OPEN.test(links[i].textContent.trim())) { links[i].parentNode.insertBefore(box, links[i].nextSibling); return true; }
    }
    var top = panel.querySelector(".space-y-6");
    if (!top) return false;
    top.insertBefore(box, top.firstChild);
    return true;
  }

  function line(doc, text, css) {
    var d = doc.createElement("div");
    d.textContent = text;
    d.style.cssText = css;
    return d;
  }

  function render(doc, box, t, state, title, note) {
    while (box.firstChild) box.removeChild(box.firstChild);
    var muted = "color:#9ca3af;font-size:.8rem;margin-top:.35rem;";
    if (state === "downloading") { box.appendChild(line(doc, t.downloading, "color:#d1d5db;font-size:.875rem;")); return; }
    var b = doc.createElement("button");
    b.type = "button";
    b.textContent = t.button;
    b.style.cssText = "width:100%;padding:.55rem .75rem;border-radius:.375rem;border:1px solid #f59e0b;background:#b45309;color:#fff;font-weight:700;font-size:.95rem;cursor:pointer;";
    b.addEventListener("click", function () { press(doc, box, t, title, state); });
    box.appendChild(b);
    box.appendChild(line(doc, note || t[state], muted));
  }

  function press(doc, box, t, title, state) {
    var b = box.querySelector("button");
    if (!b || b.disabled) return;
    b.disabled = true;
    b.textContent = t.busy;
    request("POST", API, { type: title.type, tmdbId: title.tmdbId }).then(function (r) {
      while (box.firstChild) box.removeChild(box.firstChild);
      box.appendChild(line(doc, t.done[r.state] || t.error, "color:#e5e7eb;font-weight:600;font-size:.9rem;"));
      var nl = typeof window !== "undefined" && window.hgNightLabel;
      if (nl && nl.release && r.released && r.released.length) nl.release(r.released);
    }, function () {
      render(doc, box, t, state, title, t.error);
    });
  }

  function attach(doc, loc) {
    var title = titleOf(loc), ps = panels(doc);
    if (!title || !ps.length) return;
    for (var i = 0; i < ps.length; i++) {
      var p = ps[i];
      if (p.el.hasAttribute("data-hg-dn")) {
        if (p.el.__hgDnBox && !p.el.__hgDnBox.isConnected) place(p.el, p.el.__hgDnBox);
        continue;
      }
      p.el.setAttribute("data-hg-dn", "");
      (function (p) {
        if (!me) me = request("GET", "/api/v1/auth/me").catch(function () { me = null; return null; });
        me.then(function (user) {
          if (!canManage(user)) return null;
          return request("GET", API + "?type=" + title.type + "&tmdb=" + title.tmdbId);
        }).then(function (st) {
          if (!st || ["started", "searching", "downloading"].indexOf(st.state) < 0 || p.el.querySelector("#" + BOX)) return;
          var box = doc.createElement("div");
          box.id = BOX;
          box.style.cssText = "margin-top:.5rem;";
          render(doc, box, TEXT[p.lang], st.state, title);
          p.el.__hgDnBox = box;
          place(p.el, box);
        }).catch(function () { /* no button */ });
      })(p);
    }
  }

  var api = { canManage: canManage, titleOf: titleOf, attach: attach };
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
