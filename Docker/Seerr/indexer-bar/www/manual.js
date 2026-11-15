// Manual interaction page /_manual/ (README "Manual interaction"): every download problem that needs a hand, for every Seerr user; data and actions from download-now's /_download-now/manual/. All text goes in as textContent, never as markup.
(function () {
  "use strict";
  var API = "/_download-now/manual/";
  var TEXT = {
    en: {
      locale: "en-GB", title: "Manual interaction", intro: "Downloads that need a hand. Everyone can help, also with other people's requests.", back: "← Back to Seerr",
      open: "Open", done: "Done", empty: "Nothing needs a hand right now.", emptyDone: "Nothing done yet.", signin: "Sign in to Seerr first", loadError: "Could not load the list. Try again later.",
      types: { removed: "Removed from Radarr/Sonarr", unmatched: "Not matched — Seerr could not hand it over", "import": "Downloaded, but not importable", "wrong-film": "Wrong film downloaded", failed: "Download failed", "no-release": "Nothing found for days" },
      hints: {
        removed: "The request is still open in Seerr, but the title is gone from Radarr/Sonarr, so nothing searches for it.",
        unmatched: "Pick the right title; it is added to Radarr/Sonarr with the usual quality rules.",
        "import": "Check which file is which episode or film, then import.",
        "wrong-film": "The download is a different film (other year). Block it and search again.",
        failed: "Block this release and search for another one.",
        "no-release": "Pick a release by hand, or allow older versions (DVD, SD) for old or rare titles."
      },
      requestedBy: "Requested by", nobody: "no Seerr request", since: "Since", lastSearch: "Last search", never: "never", profile: "Profile", release: "Release",
      years: function (a, b) { return "Release year " + a + ", film " + b; }, episodes: function (n) { return n + " episodes"; },
      readd: "Add again", del: "Delete request", delAgain: "Tap again to delete",
      delWarn: "Deletes the request and everything of this title in Seerr; files still in the library are deleted too (Clear Data). Only when nobody wants the title any more.",
      block: "Block this release and search again", blockAgain: "Tap again to block it", profile2: function (p) { return "Allow older versions (" + p + ")"; },
      suggest: "Suggested: an old title, often only out as DVD or SD.", releases: "Show releases", searching: "Searching the indexers… (up to 2 min)", season: "Season",
      grab: "Download", noReleases: "No releases found.", pickOne: "Pick one first.", rejected: "not allowed: ", seeders: "seeders", score: "score",
      lookup: "Search", link: "Use this one", probable: "Most likely", inLibrary: "already in the library", noResults: "Nothing found.",
      files: "Show files", "import": "Import", detected: "as detected", noEpisode: "—", needEpisode: "Pick an episode for every checked file.",
      history: "What happened", gone: "Solved itself", fixed: "Fixed: ",
      actions: { readd: "added again", "delete": "deleted", block: "blocked, searched again", profile: "older versions allowed", grab: "release picked", link: "matched", "import": "imported", reopened: "back again" },
      errors: { 429: "Too many tries — wait a moment and try again.", 403: "Not allowed.", 400: "That does not fit any more — reload the page.", 404: "Already solved — reload the page.", other: "Radarr/Sonarr not reachable — try again later." },
      ok: "Done: ", working: "Working…"
    },
    de: {
      locale: "de-CH", title: "Manuell beheben", intro: "Downloads, die Hilfe brauchen. Alle können helfen, auch bei Anfragen von anderen.", back: "← Zurück zu Seerr",
      open: "Offen", done: "Erledigt", empty: "Gerade braucht nichts Hilfe.", emptyDone: "Noch nichts erledigt.", signin: "Zuerst bei Seerr anmelden", loadError: "Liste nicht ladbar. Später nochmals versuchen.",
      types: { removed: "Aus Radarr/Sonarr entfernt", unmatched: "Nicht zugeordnet — Seerr konnte es nicht übergeben", "import": "Heruntergeladen, aber nicht importierbar", "wrong-film": "Falscher Film heruntergeladen", failed: "Download fehlgeschlagen", "no-release": "Seit Tagen nichts gefunden" },
      hints: {
        removed: "Die Anfrage ist in Seerr noch offen, der Titel fehlt aber in Radarr/Sonarr, darum sucht nichts danach.",
        unmatched: "Den richtigen Titel wählen; er kommt mit den üblichen Qualitätsregeln in Radarr/Sonarr.",
        "import": "Prüfen, welche Datei welche Folge oder welcher Film ist, dann importieren.",
        "wrong-film": "Der Download ist ein anderer Film (anderes Jahr). Sperren und neu suchen.",
        failed: "Dieses Release sperren und ein anderes suchen.",
        "no-release": "Von Hand ein Release wählen, oder bei alten oder seltenen Titeln ältere Versionen (DVD, SD) erlauben."
      },
      requestedBy: "Angefragt von", nobody: "keine Seerr-Anfrage", since: "Seit", lastSearch: "Letzte Suche", never: "nie", profile: "Profil", release: "Release",
      years: function (a, b) { return "Release-Jahr " + a + ", Film " + b; }, episodes: function (n) { return n + " Folgen"; },
      readd: "Wieder hinzufügen", del: "Anfrage löschen", delAgain: "Nochmals tippen zum Löschen",
      delWarn: "Löscht die Anfrage und alles zu diesem Titel in Seerr; noch vorhandene Dateien in der Bibliothek werden auch gelöscht (Clear Data). Nur wenn niemand den Titel mehr will.",
      block: "Release sperren und neu suchen", blockAgain: "Nochmals tippen zum Sperren", profile2: function (p) { return "Ältere Versionen erlauben (" + p + ")"; },
      suggest: "Empfohlen: ein alter Titel, oft nur als DVD oder SD erhältlich.", releases: "Releases anzeigen", searching: "Sucht in den Indexern… (bis 2 Min.)", season: "Staffel",
      grab: "Herunterladen", noReleases: "Keine Releases gefunden.", pickOne: "Zuerst eines wählen.", rejected: "nicht erlaubt: ", seeders: "Seeder", score: "Punkte",
      lookup: "Suchen", link: "Diesen verwenden", probable: "Am wahrscheinlichsten", inLibrary: "schon in der Bibliothek", noResults: "Nichts gefunden.",
      files: "Dateien anzeigen", "import": "Importieren", detected: "wie erkannt", noEpisode: "—", needEpisode: "Für jede gewählte Datei eine Folge wählen.",
      history: "Verlauf", gone: "Hat sich erledigt", fixed: "Behoben: ",
      actions: { readd: "wieder hinzugefügt", "delete": "gelöscht", block: "gesperrt, neu gesucht", profile: "ältere Versionen erlaubt", grab: "Release gewählt", link: "zugeordnet", "import": "importiert", reopened: "wieder da" },
      errors: { 429: "Zu viele Versuche — kurz warten und nochmals versuchen.", 403: "Nicht erlaubt.", 400: "Das passt nicht mehr — Seite neu laden.", 404: "Schon gelöst — Seite neu laden.", other: "Radarr/Sonarr nicht erreichbar — später nochmals versuchen." },
      ok: "Erledigt: ", working: "Läuft…"
    }
  };

  function el(doc, tag, attrs) {
    var e = doc.createElement(tag);
    for (var k in attrs || {}) if (Object.prototype.hasOwnProperty.call(attrs, k) && attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) e.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    for (var i = 3; i < arguments.length; i++) {
      var c = arguments[i];
      if (c === null || c === undefined) continue;
      e.appendChild(typeof c === "string" || typeof c === "number" ? doc.createTextNode(String(c)) : c);
    }
    return e;
  }

  function when(t, iso) {
    if (!iso) return t.never;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    try { return d.toLocaleString(t.locale, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); } catch (e) { return String(iso); }
  }

  function gb(n) { return (Number(n || 0) / 1e9).toFixed(1) + " GB"; }

  function langOf(win, me) {
    var q = /[?&]lang=([a-z]+)/.exec(win.location.search || "");
    if (q) return q[1] === "de" ? "de" : "en";
    var locale = me && me.settings && me.settings.locale;
    return typeof locale === "string" && /^de/.test(locale) ? "de" : "en";
  }

  function call(win, path, body) {
    var opts = { credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body) { opts.method = "POST"; opts.headers = { "Content-Type": "application/json", Accept: "application/json" }; opts.body = JSON.stringify(body); }
    return win.fetch(path, opts).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; }, function () { return { status: r.status, body: {} }; });
    }, function () { return { status: 0, body: {} }; });
  }

  function errorText(t, status) { return t.errors[status] || t.errors.other; }

  function App(win, root) {
    this.win = win; this.doc = win.document; this.root = root; this.t = TEXT.en; this.tab = "open"; this.data = null;
  }

  App.prototype.start = function () {
    var self = this;
    return Promise.all([call(this.win, "/api/v1/auth/me"), call(this.win, API + "problems")]).then(function (r) {
      self.t = TEXT[langOf(self.win, r[0].status === 200 ? r[0].body : null)];
      self.doc.documentElement.setAttribute("lang", self.t === TEXT.de ? "de" : "en");
      self.doc.title = self.t.title + " · Seerr";
      self.show(r[1]);
    });
  };

  App.prototype.reload = function (flash) {
    var self = this;
    return call(this.win, API + "problems").then(function (r) { self.show(r, flash); });
  };

  App.prototype.show = function (r, flash) {
    var t = this.t, d = this.doc, self = this;
    while (this.root.firstChild) this.root.removeChild(this.root.firstChild);
    this.root.appendChild(el(d, "a", { href: "/", "class": "hg-m-back" }, t.back));
    this.root.appendChild(el(d, "h1", null, t.title));
    this.root.appendChild(el(d, "p", { "class": "hg-m-intro" }, t.intro));
    if (r.status === 401 || r.status === 403) {
      this.root.appendChild(el(d, "p", null, el(d, "a", { href: "/login" }, t.signin)));
      return;
    }
    if (r.status !== 200 || !r.body || !Array.isArray(r.body.open)) {
      this.root.appendChild(el(d, "p", { "class": "hg-m-error" }, t.loadError));
      return;
    }
    this.data = r.body;
    if (flash) this.root.appendChild(el(d, "p", { "class": "hg-m-flash", "data-flash": true, role: "status" }, flash));
    var tabs = el(d, "div", { "class": "hg-m-tabs", role: "tablist" });
    ["open", "done"].forEach(function (name) {
      var b = el(d, "button", { type: "button", role: "tab", "data-tab": name, "aria-selected": String(self.tab === name), "class": self.tab === name ? "hg-m-tab hg-m-on" : "hg-m-tab" }, t[name] + " (" + (r.body[name] || []).length + ")");
      b.addEventListener("click", function () { self.tab = name; self.show(r); });
      tabs.appendChild(b);
    });
    this.root.appendChild(tabs);
    var list = r.body[this.tab] || [];
    if (!list.length) {
      this.root.appendChild(el(d, "p", { "class": "hg-m-empty", "data-empty": true }, this.tab === "open" ? t.empty : t.emptyDone));
      return;
    }
    list.forEach(function (p) { self.root.appendChild(self.card(p, self.tab === "open")); });
  };

  App.prototype.card = function (p, open) {
    var t = this.t, d = this.doc, self = this;
    var c = el(d, "section", { "class": "hg-m-card", "data-problem": p.id });
    var title = String(p.title || p.key || "") + (p.year ? " (" + p.year + ")" : "");
    var link = /^(movie|tv)\/\d+$/.test(p.key || "") ? el(d, "a", { href: "/" + p.key }, title) : title;
    c.appendChild(el(d, "h2", { "data-title": true }, link));
    c.appendChild(el(d, "p", { "class": "hg-m-type", "data-type": true }, t.types[p.type] || String(p.type)));
    var who = (p.requesters || []).length ? p.requesters.map(String).join(", ") : t.nobody;
    c.appendChild(el(d, "p", { "class": "hg-m-meta", "data-requesters": true }, t.requestedBy + ": " + who + " · " + t.since + " " + when(t, p.firstSeen)));
    c.appendChild(this.details(p));
    if (open) {
      c.appendChild(el(d, "p", { "class": "hg-m-hint" }, t.hints[p.type] || ""));
      var acts = el(d, "div", { "class": "hg-m-actions" });
      this.actions(p, acts);
      c.appendChild(acts);
      c.appendChild(el(d, "p", { "class": "hg-m-msg", "data-msg": true, role: "status" }));
    } else {
      var res = p.resolved || {};
      c.appendChild(el(d, "p", { "class": "hg-m-resolved" }, (res.how === "gone" ? t.gone : t.fixed + (t.actions[res.how] || res.how)) + " · " + when(t, res.at)));
    }
    if ((p.actions || []).length) {
      var h = el(d, "details", { "class": "hg-m-history", open: !open }, el(d, "summary", null, t.history));
      var ul = el(d, "ul");
      p.actions.forEach(function (a) {
        ul.appendChild(el(d, "li", null, when(t, a.at) + " · " + (a.user ? String(a.user) + ": " : "") + (t.actions[a.action] || String(a.action)) + (a.result ? " — " + String(a.result) : "")));
      });
      h.appendChild(ul);
      c.appendChild(h);
    }
    return c;
  };

  App.prototype.details = function (p) {
    var t = this.t, d = this.doc, x = p.details || {}, parts = [];
    if (x.release) parts.push(t.release + ": " + String(x.release));
    if (x.releaseYear && x.year) parts.push(t.years(x.releaseYear, x.year));
    if (x.episodes) parts.push(t.episodes(x.episodes));
    if (x.message) parts.push(String(x.message));
    if (p.type === "no-release") {
      parts.push(t.lastSearch + ": " + when(t, x.lastSearch));
      if (x.profile) parts.push(t.profile + ": " + String(x.profile));
    }
    return el(d, "p", { "class": "hg-m-details", "data-details": true }, parts.join(" · "));
  };

  App.prototype.button = function (name, label, cls) {
    return el(this.doc, "button", { type: "button", "data-act": name, "class": "hg-m-btn" + (cls ? " " + cls : "") }, label);
  };

  App.prototype.say = function (card, text, bad) {
    var m = card.querySelector("[data-msg]");
    if (!m) return;
    m.textContent = text;
    m.className = "hg-m-msg" + (bad ? " hg-m-bad" : "");
  };

  App.prototype.act = function (card, p, body) {
    var self = this, t = this.t;
    body = Object.assign({ id: p.id }, body);
    this.say(card, t.working);
    card.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    return call(this.win, API + "action", body).then(function (r) {
      if (r.status === 200) return self.reload(t.ok + String((r.body && r.body.result) || ""));
      card.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
      self.say(card, errorText(t, r.status), true);
    });
  };

  // a button that needs a second tap: the first one arms it (and shows the warning)
  App.prototype.twice = function (btn, again, warning, box, go) {
    var d = this.doc, armed = false;
    btn.addEventListener("click", function () {
      if (armed) return go();
      armed = true;
      btn.textContent = again;
      btn.classList.add("hg-m-armed");
      if (warning) box.appendChild(el(d, "p", { "class": "hg-m-warning", "data-warning": true, role: "alert" }, warning));
    });
  };

  App.prototype.actions = function (p, box) {
    var self = this, t = this.t, d = this.doc, x = p.details || {};
    var card = function () { return box.parentNode; };
    if (p.type === "removed") {
      var re = this.button("readd", t.readd, "hg-m-primary");
      re.addEventListener("click", function () { self.act(card(), p, { action: "readd" }); });
      box.appendChild(re);
      if (this.data && this.data.admin) {
        var del = this.button("delete", t.del, "hg-m-danger");
        this.twice(del, t.delAgain, t.delWarn, box, function () { self.act(card(), p, { action: "delete", confirm: "delete" }); });
        box.appendChild(del);
      }
    } else if (p.type === "wrong-film" || p.type === "failed") {
      var bl = this.button("block", t.block, "hg-m-primary");
      this.twice(bl, t.blockAgain, null, box, function () { self.act(card(), p, { action: "block" }); });
      box.appendChild(bl);
    } else if (p.type === "no-release") {
      if (x.older) {
        if (x.old) box.appendChild(el(d, "p", { "class": "hg-m-suggest", "data-suggest": true }, t.suggest));
        var pr = this.button("profile", t.profile2(String(x.older)), x.old ? "hg-m-primary" : "");
        pr.addEventListener("click", function () { self.act(card(), p, { action: "profile" }); });
        box.appendChild(pr);
      }
      var seasons = Array.isArray(x.seasons) ? x.seasons : [];
      var pick = null;
      if (seasons.length > 1) {
        pick = el(d, "select", { "data-season": true, "aria-label": t.season });
        seasons.forEach(function (s) { pick.appendChild(el(d, "option", { value: String(s) }, t.season + " " + s)); });
        box.appendChild(pick);
      }
      var rl = this.button("releases", t.releases);
      rl.addEventListener("click", function () { self.releases(card(), p, pick ? pick.value : seasons.length ? String(seasons[0]) : null); });
      box.appendChild(rl);
      box.appendChild(el(d, "div", { "data-list": true }));
    } else if (p.type === "unmatched") {
      var term = el(d, "input", { type: "search", "data-term": true, "class": "hg-m-input", "aria-label": t.lookup, maxlength: "100" });
      term.value = String(p.title || "");
      var lk = this.button("lookup", t.lookup);
      lk.addEventListener("click", function () { self.lookup(card(), p, term.value); });
      box.appendChild(term);
      box.appendChild(lk);
      box.appendChild(el(d, "div", { "data-list": true }));
    } else if (p.type === "import") {
      var fl = this.button("files", t.files);
      fl.addEventListener("click", function () { self.files(card(), p); });
      box.appendChild(fl);
      box.appendChild(el(d, "div", { "data-list": true }));
    }
  };

  App.prototype.list = function (card) {
    var l = card.querySelector("[data-list]");
    while (l.firstChild) l.removeChild(l.firstChild);
    return l;
  };

  App.prototype.releases = function (card, p, season) {
    var self = this, t = this.t, d = this.doc;
    var l = this.list(card);
    this.say(card, t.searching);
    return call(this.win, API + "releases?id=" + encodeURIComponent(p.id) + (season ? "&season=" + encodeURIComponent(season) : "")).then(function (r) {
      if (r.status !== 200) return self.say(card, errorText(t, r.status), true);
      self.say(card, "");
      var rel = (r.body && r.body.releases) || [];
      if (!rel.length) return l.appendChild(el(d, "p", null, t.noReleases));
      var ul = el(d, "ul", { "class": "hg-m-rows" });
      rel.forEach(function (x) {
        var radio = el(d, "input", { type: "radio", name: "release-" + p.id, value: String(x.n), disabled: !!x.rejected });
        var facts = [x.quality, gb(x.size), (x.languages || []).join(", "), x.protocol === "torrent" && x.seeders != null ? x.seeders + " " + t.seeders : null, x.score != null ? t.score + " " + x.score : null, x.indexer].filter(Boolean).map(String).join(" · ");
        var why = x.rejected && (x.rejections || []).length ? el(d, "span", { "class": "hg-m-why" }, t.rejected + x.rejections.map(String).join("; ")) : null;
        ul.appendChild(el(d, "li", { "data-release": true, "class": x.rejected ? "hg-m-rejected" : null }, el(d, "label", null, radio, el(d, "span", { "class": "hg-m-name" }, String(x.title || "")), el(d, "span", { "class": "hg-m-facts" }, facts), why)));
      });
      l.appendChild(ul);
      var go = self.button("grab", t.grab, "hg-m-primary");
      go.addEventListener("click", function () {
        var c = l.querySelector('input[name="release-' + p.id + '"]:checked');
        if (!c) return self.say(card, t.pickOne, true);
        self.act(card, p, { action: "grab", release: Number(c.value) });
      });
      l.appendChild(go);
    });
  };

  App.prototype.lookup = function (card, p, term) {
    var self = this, t = this.t, d = this.doc;
    var l = this.list(card);
    this.say(card, t.working);
    return call(this.win, API + "lookup?id=" + encodeURIComponent(p.id) + "&term=" + encodeURIComponent(String(term || "").trim())).then(function (r) {
      if (r.status !== 200) return self.say(card, errorText(t, r.status), true);
      self.say(card, "");
      var res = (r.body && r.body.results) || [];
      if (!res.length) return l.appendChild(el(d, "p", null, t.noResults));
      var ul = el(d, "ul", { "class": "hg-m-rows" });
      res.forEach(function (x) {
        var radio = el(d, "input", { type: "radio", name: "pick-" + p.id, value: String(x.n) });
        radio.checked = !!x.probable;
        var id = x.tvdbId ? "TVDB " + x.tvdbId : x.tmdbId ? "TMDB " + x.tmdbId : null;
        var facts = [x.year, id, x.inLibrary ? t.inLibrary : null].filter(Boolean).map(String).join(" · ");
        ul.appendChild(el(d, "li", { "data-result": true }, el(d, "label", null, radio, el(d, "span", { "class": "hg-m-name" }, String(x.title || "")),
          x.probable ? el(d, "span", { "class": "hg-m-probable" }, t.probable) : null, el(d, "span", { "class": "hg-m-facts" }, facts),
          x.overview ? el(d, "span", { "class": "hg-m-why" }, String(x.overview)) : null)));
      });
      l.appendChild(ul);
      var go = self.button("link", t.link, "hg-m-primary");
      go.addEventListener("click", function () {
        var c = l.querySelector('input[name="pick-' + p.id + '"]:checked');
        if (!c) return self.say(card, t.pickOne, true);
        self.act(card, p, { action: "link", pick: Number(c.value) });
      });
      l.appendChild(go);
    });
  };

  App.prototype.files = function (card, p) {
    var self = this, t = this.t, d = this.doc;
    var l = this.list(card);
    this.say(card, t.working);
    return call(this.win, API + "files?id=" + encodeURIComponent(p.id)).then(function (r) {
      if (r.status !== 200) return self.say(card, errorText(t, r.status), true);
      self.say(card, "");
      var tv = r.body.kind === "tv", eps = r.body.episodes || [], label = {};
      eps.forEach(function (e) { label[e.id] = e.label; });
      var ul = el(d, "ul", { "class": "hg-m-rows" });
      (r.body.files || []).forEach(function (f) {
        var box = el(d, "input", { type: "checkbox", "data-file": String(f.n) });
        box.checked = !!f.probable;
        var row = el(d, "li", null, el(d, "label", null, box, el(d, "span", { "class": "hg-m-name" }, String(f.name || "")), el(d, "span", { "class": "hg-m-facts" }, [gb(f.size), f.quality].filter(Boolean).map(String).join(" · "))));
        if (tv) {
          var sel = el(d, "select", { "data-episode": String(f.n), "aria-label": String(f.name || "") });
          sel.appendChild(el(d, "option", { value: "" }, t.noEpisode));
          var guess = (f.episodeIds || []).join(",");
          if ((f.episodeIds || []).length > 1) sel.appendChild(el(d, "option", { value: guess }, f.episodeIds.map(function (i) { return label[i] || i; }).join("+") + " (" + t.detected + ")"));
          eps.forEach(function (e) { sel.appendChild(el(d, "option", { value: String(e.id) }, String(e.label) + (e.title ? " · " + String(e.title) : ""))); });
          sel.value = guess;
          row.appendChild(sel);
        }
        if ((f.rejections || []).length) row.appendChild(el(d, "span", { "class": "hg-m-why" }, f.rejections.map(String).join("; ")));
        ul.appendChild(row);
      });
      l.appendChild(ul);
      var go = self.button("import", t["import"], "hg-m-primary");
      go.addEventListener("click", function () {
        var out = [], missing = false;
        l.querySelectorAll("input[data-file]").forEach(function (b) {
          if (!b.checked) return;
          var n = Number(b.getAttribute("data-file"));
          if (!tv) return out.push({ n: n });
          var s = l.querySelector('select[data-episode="' + n + '"]');
          if (!s || !s.value) { missing = true; return; }
          out.push({ n: n, episodeIds: s.value.split(",").map(Number) });
        });
        if (missing) return self.say(card, t.needEpisode, true);
        if (!out.length) return self.say(card, t.pickOne, true);
        self.act(card, p, { action: "import", files: out });
      });
      l.appendChild(go);
    });
  };

  if (typeof window === "undefined") return;
  window.hgManual = { TEXT: TEXT, langOf: langOf };
  try {
    var root = document.getElementById("hg-manual");
    if (root && window.fetch) new App(window, root).start();
  } catch (e) { /* page stays as it is */ }
})();
