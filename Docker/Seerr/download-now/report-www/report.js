(function () {
  "use strict";

  var REASONS = ["start", "video", "audio", "quality", "sound", "language", "subtitles", "wrong", "other"];
  var TEXT = {
    en: {
      title: "Report a problem", intro: "Something wrong with a film or episode? Report it here and the server replaces it, no admin needed.",
      left: function (n) { return n === 1 ? "You can send 1 more report today." : "You can send " + n + " more reports today."; },
      noneLeft: "You have used your 2 reports for today. Try again tomorrow.", admin: "Admin: no daily limit, you see every report.",
      find: "1. Which film or series?", search: "Search by title", searchBtn: "Search", noHits: "Nothing in the library matches.",
      change: "Change", season: "Season", episode: "2. Which episode?", pickEpisode: "Pick the episode",
      what: "What is wrong?", note: "Note (optional)", noteOther: "Please describe the problem", send: "Send report", sending: "Sending…",
      reasons: {
        start: "Doesn't start / stops", video: "Video glitches (blocks, freezes, artefacts)", audio: "Audio glitches (dropouts, crackle, out of sync)",
        quality: "Bad video quality (blurry, low resolution, wrong aspect)", sound: "No sound / wrong audio track", language: "Wrong language",
        subtitles: "Subtitles missing / wrong / out of sync", wrong: "Wrong title / wrong episode", other: "Other"
      },
      status: {
        queued: "Waiting", checking: "Checking", searching: "Searching for another version", downloading: "Downloading", importing: "Importing",
        imported: "Replaced — ready to watch", "nothing-found": "Nothing found yet — an admin will look at it", "nothing-better": "No better version found",
        "language-ok": "Language check passed — an admin will look at it", subtitles: "Subtitles are searched again", review: "An admin will look at it",
        failed: "Replacement failed — the old file stays, an admin will look at it", closed: "Handled by an admin", "dry-run": "Test mode: nothing changed"
      },
      mine: "Your reports", all: "All reports", none: "No reports yet.", back: "← New report", reportedBy: "by",
      errors: { limit: "You have used your 2 reports for today.", "already reported": "This was already reported — see its status below.", "bad request": "Please check your choice.",
        forbidden: "Not signed in or no access.", origin: "Please reload the page.", "not in the library": "This title is not in the library.", generic: "Something went wrong. Try again later." },
      fileCheck: "File check", ep: function (s, e) { return "S" + pad(s) + "E" + pad(e); }
    },
    de: {
      title: "Problem melden", intro: "Stimmt etwas mit einem Film oder einer Folge nicht? Hier melden, der Server ersetzt die Datei, ohne Admin.",
      left: function (n) { return n === 1 ? "Du kannst heute noch 1 Meldung senden." : "Du kannst heute noch " + n + " Meldungen senden."; },
      noneLeft: "Du hast deine 2 Meldungen für heute gebraucht. Morgen wieder.", admin: "Admin: kein Tageslimit, du siehst alle Meldungen.",
      find: "1. Welcher Film, welche Serie?", search: "Nach Titel suchen", searchBtn: "Suchen", noHits: "Nichts in der Bibliothek passt.",
      change: "Ändern", season: "Staffel", episode: "2. Welche Folge?", pickEpisode: "Folge wählen",
      what: "Was ist falsch?", note: "Bemerkung (freiwillig)", noteOther: "Bitte beschreibe das Problem", send: "Meldung senden", sending: "Wird gesendet…",
      reasons: {
        start: "Startet nicht / bricht ab", video: "Bildfehler (Blöcke, Standbild, Artefakte)", audio: "Tonfehler (Aussetzer, Knacken, nicht synchron)",
        quality: "Schlechte Bildqualität (unscharf, tiefe Auflösung, falsches Format)", sound: "Kein Ton / falsche Tonspur", language: "Falsche Sprache",
        subtitles: "Untertitel fehlen / falsch / nicht synchron", wrong: "Falscher Titel / falsche Folge", other: "Anderes"
      },
      status: {
        queued: "Wartet", checking: "Wird geprüft", searching: "Suche nach einer anderen Version", downloading: "Wird heruntergeladen", importing: "Wird importiert",
        imported: "Ersetzt — bereit zum Schauen", "nothing-found": "Noch nichts gefunden — ein Admin schaut es an", "nothing-better": "Keine bessere Version gefunden",
        "language-ok": "Sprachprüfung bestanden — ein Admin schaut es an", subtitles: "Untertitel werden neu gesucht", review: "Ein Admin schaut es an",
        failed: "Ersetzen fehlgeschlagen — die alte Datei bleibt, ein Admin schaut es an", closed: "Von einem Admin erledigt", "dry-run": "Testmodus: nichts geändert"
      },
      mine: "Deine Meldungen", all: "Alle Meldungen", none: "Noch keine Meldungen.", back: "← Neue Meldung", reportedBy: "von",
      errors: { limit: "Du hast deine 2 Meldungen für heute gebraucht.", "already reported": "Das wurde schon gemeldet — Status siehe unten.", "bad request": "Bitte prüfe deine Auswahl.",
        forbidden: "Nicht angemeldet oder kein Zugriff.", origin: "Bitte lade die Seite neu.", "not in the library": "Dieser Titel ist nicht in der Bibliothek.", generic: "Etwas ging schief. Später nochmals versuchen." },
      fileCheck: "Dateiprüfung", ep: function (s, e) { return "S" + pad(s) + "E" + pad(e); }
    }
  };
  var GOOD = { imported: 1, subtitles: 1, closed: 1, "nothing-better": 1 };
  var BAD = { failed: 1, "nothing-found": 1 };
  var DONE = { imported: 1, "nothing-better": 1, subtitles: 1, failed: 1, closed: 1, "dry-run": 1, review: 1, "language-ok": 1, "nothing-found": 1 };

  function pad(n) { return (Number(n) < 10 ? "0" : "") + Number(n); }

  function pickLang(langs) {
    var list = langs || [];
    for (var i = 0; i < list.length; i++) {
      var l = String(list[i] || "").toLowerCase();
      if (l.indexOf("de") === 0) return "de";
      if (l.indexOf("en") === 0) return "en";
    }
    return "en";
  }

  function statusInfo(t, rep) {
    var s = rep && rep.status;
    var text = t.status[s] || String(s || "");
    if (s === "downloading" && typeof rep.progress === "number") text += " " + rep.progress + " %";
    return { text: text, cls: GOOD[s] ? "ok" : BAD[s] ? "bad" : "warn", done: !!DONE[s] };
  }

  // ?type=movie&tmdb=1 or ?type=tv&tvdb=2&tmdb=3&season=1&episode=8 from the Jellyfin link: the query for /api/resolve, else null
  function linkQuery(search) {
    var p = new URLSearchParams(search || "");
    var type = p.get("type");
    if (type !== "movie" && type !== "tv") return null;
    var out = new URLSearchParams({ type: type });
    ["tmdb", "tvdb", "season", "episode"].forEach(function (k) { var v = p.get(k); if (v && /^[0-9]{1,10}$/.test(v)) out.set(k, v); });
    return out.has("tmdb") || out.has("tvdb") ? out.toString() : null;
  }

  function itemLabel(t, r) {
    var s = String(r.title || "") + (r.year ? " (" + r.year + ")" : "");
    if (r.season != null && r.episode != null) s += " · " + t.ep(r.season, r.episode) + (r.episodeTitle ? " " + r.episodeTitle : "");
    return s;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { pickLang: pickLang, statusInfo: statusInfo, linkQuery: linkQuery, itemLabel: itemLabel, TEXT: TEXT, REASONS: REASONS };
    return;
  }

  var lang = pickLang(navigator.languages || [navigator.language]);
  var t = TEXT[lang];
  document.documentElement.lang = lang;
  document.title = t.title;
  var app = document.getElementById("app");
  var state = { me: null, item: null, seasons: null, reason: null, poll: null };

  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "on") Object.keys(attrs.on).forEach(function (ev) { e.addEventListener(ev, attrs.on[ev]); });
      else if (k === "text") e.textContent = attrs.text;
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) e.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }

  function api(path, body) {
    var opt = { credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body) { opt.method = "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) { var e = new Error(j.error || "generic"); e.data = j; throw e; } return j; });
    });
  }

  function errText(e) { return t.errors[e && e.message] || t.errors.generic; }

  function header() {
    var me = state.me;
    var info = !me ? "" : me.admin ? t.admin : me.left > 0 ? t.left(me.left) : t.noneLeft;
    return el("header", {}, [el("h1", { text: "⚠ " + t.title }), el("p", { class: "muted", text: t.intro }), el("p", { class: "muted", "data-left": true, text: info })]);
  }

  function render() {
    clearTimeout(state.poll);
    app.textContent = "";
    var m = /^#r=([0-9a-f]{12})$/.exec(location.hash);
    if (m) return renderStatus(m[1]);
    app.appendChild(header());
    app.appendChild(findCard());
    if (state.item && state.item.kind === "tv") app.appendChild(episodeCard());
    if (state.item && (state.item.kind === "movie" || state.item.episodeId)) app.appendChild(reasonCard());
    app.appendChild(listCard());
  }

  function findCard() {
    var c = el("section", { class: "card", "data-find": true }, [el("h2", { text: t.find })]);
    if (state.item) {
      c.appendChild(el("div", { class: "chosen" }, [el("strong", { class: "break", text: state.item.title + (state.item.year ? " (" + state.item.year + ")" : "") }),
        el("button", { class: "plain", type: "button", on: { click: function () { state.item = null; state.seasons = null; render(); } }, text: t.change })]));
      return c;
    }
    var input = el("input", { type: "search", placeholder: t.search, "aria-label": t.search, autocomplete: "off", enterkeyhint: "search" });
    var list = el("ul", { class: "pick" });
    var msg = el("p", { class: "muted" });
    function go(ev) {
      if (ev) ev.preventDefault();
      var q = input.value.trim();
      if (!q) return;
      msg.textContent = "…";
      api("/api/search?q=" + encodeURIComponent(q)).then(function (j) {
        list.textContent = "";
        msg.textContent = j.results.length ? "" : t.noHits;
        j.results.forEach(function (r) {
          list.appendChild(el("li", {}, [el("button", { type: "button", on: { click: function () { choose(r, null); } }, text: (r.kind === "tv" ? "📺 " : "🎬 ") + r.title + (r.year ? " (" + r.year + ")" : "") })]));
        });
      }).catch(function (e) { msg.textContent = errText(e); });
    }
    c.appendChild(el("form", { role: "search", on: { submit: go } }, [input, el("div", { class: "row" }, [el("button", { type: "submit", text: t.searchBtn })])]));
    c.appendChild(msg);
    c.appendChild(list);
    setTimeout(function () { input.focus(); }, 0);
    return c;
  }

  function choose(r, episodeId) {
    state.item = { ref: r.ref, kind: r.kind, title: r.title, year: r.year, episodeId: episodeId || null };
    state.seasons = null;
    if (r.kind === "tv") {
      api("/api/episodes?ref=" + encodeURIComponent(r.ref)).then(function (j) { state.seasons = j.seasons; render(); }).catch(function () { state.seasons = []; render(); });
    }
    render();
  }

  function episodeCard() {
    var c = el("section", { class: "card", "data-episodes": true }, [el("h2", { text: t.episode })]);
    if (!state.seasons) { c.appendChild(el("p", { class: "muted", text: "…" })); return c; }
    var chosen = state.item.episodeId;
    var current = null;
    state.seasons.forEach(function (s) { s.episodes.forEach(function (e) { if (e.id === chosen) current = s.season; }); });
    if (current === null && state.seasons.length) current = state.seasons[0].season;
    var sel = el("select", { "aria-label": t.season, on: { change: function () { state.item.episodeId = null; state.item.season = Number(sel.value); render(); } } });
    if (state.item.season != null && !chosen) current = state.item.season;
    state.seasons.forEach(function (s) { sel.appendChild(el("option", { value: String(s.season), selected: s.season === current, text: t.season + " " + s.season })); });
    c.appendChild(sel);
    var list = el("ul", { class: "pick" });
    (state.seasons.filter(function (s) { return s.season === current; })[0] || { episodes: [] }).episodes.forEach(function (e) {
      list.appendChild(el("li", {}, [el("button", { type: "button", "aria-pressed": e.id === chosen ? "true" : "false",
        on: { click: function () { state.item.episodeId = e.id; state.item.season = current; render(); } }, text: t.ep(current, e.episode) + (e.title ? " · " + e.title : "") })]));
    });
    c.appendChild(list);
    return c;
  }

  function reasonCard() {
    var c = el("section", { class: "card", "data-reasons": true }, [el("h2", { text: (state.item.kind === "tv" ? "3. " : "2. ") + t.what })]);
    var form = el("form", {});
    REASONS.forEach(function (r) {
      var input = el("input", { type: "radio", name: "reason", value: r, checked: state.reason === r, on: { change: function () { state.reason = r; update(); } } });
      form.appendChild(el("label", { class: "reason" }, [input, el("span", { text: t.reasons[r] })]));
    });
    var noteLabel = el("p", { class: "muted" });
    var note = el("textarea", { maxlength: "300", rows: "3", on: { input: update } });
    var send = el("button", { type: "submit", text: t.send });
    var msg = el("p", { class: "err", role: "alert" });
    function update() {
      noteLabel.textContent = state.reason === "other" ? t.noteOther : t.note;
      var over = state.me && !state.me.admin && state.me.left <= 0;
      send.disabled = !state.reason || over || (state.reason === "other" && !note.value.trim());
    }
    form.appendChild(noteLabel);
    form.appendChild(note);
    form.appendChild(el("div", { class: "row" }, [send]));
    form.appendChild(msg);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      send.disabled = true;
      send.textContent = t.sending;
      var body = { ref: state.item.ref, reason: state.reason, note: note.value.trim() };
      if (state.item.kind === "tv") body.episodeId = state.item.episodeId;
      api("/api/report", body).then(function (j) {
        state.item = null; state.reason = null;
        location.hash = "#r=" + j.id;
      }).catch(function (e) {
        msg.textContent = errText(e);
        if (e.data && e.data.id) msg.appendChild(el("a", { href: "#r=" + e.data.id, text: " →" }));
        send.textContent = t.send;
        update();
      });
    });
    c.appendChild(form);
    update();
    return c;
  }

  function reportLine(r, admin) {
    var s = statusInfo(t, r);
    var a = el("a", { href: "#r=" + r.id, class: "break", text: itemLabel(t, r) });
    return el("div", { class: "item" }, [a, el("p", { class: "status " + s.cls, text: s.text }),
      el("p", { class: "muted", text: (t.reasons[r.reason] || r.reason) + (admin ? " · " + t.reportedBy + " " + r.user : "") + " · " + new Date(r.at).toLocaleString(lang) })]);
  }

  function listCard() {
    var c = el("section", { class: "card", "data-list": true }, [el("h2", { text: state.me && state.me.admin ? t.all : t.mine })]);
    var body = el("div", {}, [el("p", { class: "muted", text: "…" })]);
    c.appendChild(body);
    api("/api/reports").then(function (j) {
      body.textContent = "";
      if (!j.reports.length) body.appendChild(el("p", { class: "muted", text: t.none }));
      j.reports.slice(0, 30).forEach(function (r) { body.appendChild(reportLine(r, state.me && state.me.admin)); });
    }).catch(function (e) { body.textContent = errText(e); });
    return c;
  }

  function renderStatus(id) {
    app.appendChild(el("p", {}, [el("a", { href: "#", text: t.back })]));
    var c = el("section", { class: "card", "data-status": true }, [el("p", { class: "muted", text: "…" })]);
    app.appendChild(c);
    api("/api/report?id=" + id).then(function (r) {
      c.textContent = "";
      var s = statusInfo(t, r);
      c.appendChild(el("h2", { class: "break", text: itemLabel(t, r) }));
      c.appendChild(el("p", { class: "muted", text: t.reasons[r.reason] || r.reason }));
      if (r.note) c.appendChild(el("p", { class: "break", text: "“" + r.note + "”" }));
      c.appendChild(el("p", { class: "status " + s.cls, "data-state": r.status, text: s.text }));
      if (r.status === "downloading" && typeof r.progress === "number") {
        var fill = el("span", {});
        fill.style.width = Math.max(0, Math.min(100, r.progress)) + "%";
        c.appendChild(el("div", { class: "bar" }, [fill]));
      }
      if (r.result) c.appendChild(el("p", { class: "muted break", text: r.result }));
      if (r.fileCheck) c.appendChild(el("p", { class: "muted break", text: t.fileCheck + ": " + r.fileCheck.verdict + ((r.fileCheck.reasons || []).length ? " — " + r.fileCheck.reasons.join("; ") : "") }));
      if (!s.done || r.status === "downloading") state.poll = setTimeout(render, 10000);
    }).catch(function (e) { c.textContent = errText(e); });
  }

  window.addEventListener("hashchange", render);
  api("/api/me").then(function (me) {
    state.me = me;
    var q = linkQuery(location.search);
    if (!q) return render();
    history.replaceState(null, "", location.pathname + location.hash);
    return api("/api/resolve?" + q).then(function (r) { choose(r, r.episodeId); }).catch(function () { render(); });
  }).catch(function (e) { app.textContent = errText(e); });
})();
