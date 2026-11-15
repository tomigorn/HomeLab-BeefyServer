// Collections page /collections inside Seerr's layout (README "Collections"): Seerr shows its 404 there, this script (injected by seerr-proxy) puts the page in its place. Own Jellyfin collections from a search (producer:…, actor:…), for every Seerr user to see; data from download-now's /_download-now/collections/. All text goes in as textContent, never as markup.
(function () {
  "use strict";
  var API = "/_download-now/collections/", ROUTE = "/collections", IMG = "https://image.tmdb.org/t/p/w185", SHOWN = 8;
  var IMAGE_PATH = /^\/[A-Za-z0-9_.-]+$/;
  var CSS = "#hg-collections { max-width: 64rem; margin: 0 auto; padding: 1rem 1rem 7rem; color: #e5e7eb; font-size: 15px; line-height: 1.5; }" +
    "main[data-hg-collections] > :not(#hg-collections) { display: none !important; }" +
    "#hg-collections * { box-sizing: border-box; }" +
    "#hg-collections h1 { margin: .5rem 0 .25rem; font-size: 1.75rem; font-weight: 700; color: #fff; }" +
    "#hg-collections h2 { margin: 0; font-size: 1.15rem; color: #fff; overflow-wrap: anywhere; }" +
    "#hg-collections a { color: inherit; text-decoration: none; }" +
    ".hg-c-intro, .hg-c-note { margin: 0 0 1rem; color: #9ca3af; }" +
    ".hg-c-card { background: #1f2937; border: 1px solid #374151; border-radius: .75rem; padding: 1rem; margin: 0 0 1rem; }" +
    ".hg-c-form { display: grid; grid-template-columns: 1fr; gap: .6rem; }" +
    ".hg-c-row { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }" +
    ".hg-c-label { display: flex; flex-direction: column; gap: .2rem; font-size: .85rem; color: #9ca3af; flex: 1 1 12rem; }" +
    ".hg-c-label.hg-c-small { flex: 0 1 9rem; }" +
    ".hg-c-input, #hg-collections select { background: #111827; color: #fff; border: 1px solid #4b5563; border-radius: .375rem; padding: .45rem .6rem; font: inherit; width: 100%; min-width: 0; }" +
    ".hg-c-query { display: flex; gap: .4rem; align-items: stretch; }" +
    ".hg-c-query .hg-c-input { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9rem; }" +
    ".hg-c-help { flex: none; width: 2.4rem; background: #374151; color: #fff; border: 1px solid #4b5563; border-radius: .375rem; font: inherit; font-weight: 700; cursor: help; }" +
    ".hg-c-btn { background: #374151; color: #fff; border: 1px solid #4b5563; border-radius: .375rem; padding: .45rem .9rem; font: inherit; cursor: pointer; }" +
    ".hg-c-btn:hover:not(:disabled) { background: #4b5563; }" +
    ".hg-c-btn:disabled { opacity: .5; cursor: default; }" +
    ".hg-c-primary { background: #4f46e5; border-color: #6366f1; }" +
    ".hg-c-primary:hover:not(:disabled) { background: #6366f1; }" +
    ".hg-c-armed { background: #b91c1c; border-color: #dc2626; }" +
    ".hg-c-msg { margin: 0; min-height: 1.4em; color: #a5b4fc; }" +
    ".hg-c-bad { color: #fca5a5; }" +
    ".hg-c-flash { padding: .6rem .9rem; border-radius: .5rem; background: #14532d; color: #bbf7d0; margin: 0 0 1rem; }" +
    ".hg-c-warning { flex-basis: 100%; margin: 0; padding: .5rem .75rem; border-radius: .375rem; background: #450a0a; color: #fecaca; }" +
    ".hg-c-picks h3 { margin: .25rem 0 .5rem; font-size: 1rem; color: #fff; }" +
    ".hg-c-options { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); gap: .5rem; }" +
    ".hg-c-option { display: flex; gap: .6rem; align-items: center; text-align: left; background: #111827; color: #e5e7eb; border: 1px solid #4b5563; border-radius: .5rem; padding: .4rem; font: inherit; cursor: pointer; }" +
    ".hg-c-option:hover { border-color: #818cf8; }" +
    ".hg-c-option img, .hg-c-face { flex: none; width: 3rem; height: 4.5rem; object-fit: cover; border-radius: .25rem; background: #374151; }" +
    ".hg-c-option b { display: block; color: #fff; }" +
    ".hg-c-option small { display: block; color: #9ca3af; font-size: .8rem; }" +
    ".hg-c-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .5rem; align-items: baseline; }" +
    ".hg-c-sub { margin: .15rem 0; color: #9ca3af; font-size: .9rem; overflow-wrap: anywhere; }" +
    ".hg-c-q { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85rem; color: #c7d2fe; overflow-wrap: anywhere; }" +
    ".hg-c-counts { margin: .35rem 0; color: #d1d5db; font-size: .9rem; }" +
    ".hg-c-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(7.5rem, 1fr)); gap: .75rem; margin: .5rem 0 0; padding: 0; list-style: none; }" +
    ".hg-c-grid li { min-width: 0; }" +
    ".hg-c-poster { position: relative; aspect-ratio: 2 / 3; border-radius: .375rem; overflow: hidden; background: #374151; }" +
    ".hg-c-poster img { width: 100%; height: 100%; object-fit: cover; display: block; }" +
    ".hg-c-missing img { filter: saturate(.5) brightness(.75); }" +
    ".hg-c-band { position: absolute; left: 0; right: 0; bottom: 0; padding: .2rem .3rem; font-size: .7rem; font-weight: 600; text-align: center; color: #fff; background: rgba(17,24,39,.85); border-top: 2px solid #f59e0b; }" +
    ".hg-c-owned .hg-c-band { border-top-color: #22c55e; }" +
    ".hg-c-name { margin: .25rem 0 0; font-size: .8rem; color: #e5e7eb; overflow-wrap: anywhere; line-height: 1.25; }" +
    ".hg-c-year { color: #9ca3af; }" +
    ".hg-c-actions { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-top: .75rem; }" +
    ".hg-c-readonly { margin: .5rem 0 0; color: #9ca3af; font-size: .9rem; }" +
    ".hg-c-lists { min-height: 8rem; }";

  var TEXT = {
    en: {
      title: "Collections", intro: "Build your own Jellyfin collections from a search, e.g. everything a producer made. They show up in Jellyfin under Collections & Trending; titles that are not on the server yet can be downloaded there.",
      newList: "New list", editBtn: "Edit", edit: function (n) { return "Edit “" + n + "”"; }, name: "Name", query: "Search", queryPh: "producer:Adam Sandler genre:comedy",
      sort: "Order", sorts: { release: "Newest first", popularity: "Most popular", rating: "Best rated" }, max: "At most (1–100)",
      preview: "Preview", save: "Save", cancel: "Cancel", loading: "Loading…", saving: "Saving…",
      help: "Search terms (combine them, all must fit):\nactor: — films and series a person acted in\ndirector: — directed by\nproducer: — produced or executive produced by\nwriter: — written by\nstudio: — production company (films)\nfranchise: — a film series, e.g. franchise:Harry Potter\ngenre: — e.g. genre:comedy\nyear: — 1999 or 1990-1999\ntype: — movie or series\nkeyword: — a TMDB keyword, e.g. keyword:heist",
      helpLabel: "Search help",
      pickTitle: function (v, f) { return "Which “" + v + "” (" + f + ")?"; }, pickHint: "Several match the name: pick one.",
      counts: function (n, o) { return n + (n === 1 ? " title" : " titles") + " · " + o + " on the server"; },
      totalMore: function (n) { return " (" + n + " found, the first are kept)"; },
      owned: "✓ On the server", missing: "⬇ Not downloaded yet",
      lists: function (n, total) { return "Lists (" + n + " of " + total + ")"; }, mine: function (n, max) { return "You have " + n + " of " + max + " lists."; },
      by: function (u) { return "by " + (u || "someone"); }, readOnly: function (u) { return "Read only — created by " + (u || "someone"); },
      showAll: function (n) { return "Show all " + n; }, showLess: "Show fewer",
      del: "Delete", delAgain: "Tap again to delete", delWarn: "Deletes the list and its Jellyfin collection. The films and series on the server stay.",
      empty: "No lists yet.", noCreate: "Only users who may request titles can create lists.",
      saved: "Saved. Jellyfin shows it under Collections & Trending in a few minutes.", deleted: "Deleted. Jellyfin removes the collection in a few minutes.",
      signin: "Sign in to see the lists.", loadError: "Could not load the lists. Try again in a minute.", needPreview: "Preview first, then save.",
      errors: {
        empty: "Type a search first, e.g. producer:Adam Sandler.", "too-long": "The search is too long (300 characters at most).", "too-many": "At most 6 search terms.",
        syntax: "Start with a search term like producer: or actor: (see ?).", "empty-term": "A search term has no value.", year: "Year: 1999 or 1990-1999.",
        type: "Type: movie or series.", genre: "Unknown genre (e.g. comedy, drama, horror, documentary).", twice: "Each of year, type and genre only once.",
        "anchor-or-filter": "Add a person, studio or franchise, or at least two of genre, year, keyword.", sort: "Unknown order.", max: "At most: a number from 1 to 100.",
        pick: "That pick no longer fits, preview again.", name: "Name: 1 to 60 letters, digits and simple punctuation.", "name-taken": "That name is taken.",
        "limit-user": "You already have 5 lists. Delete one first.", "limit-total": "There are already 20 lists. Delete one first.", "no-titles": "The search finds no titles.",
        forbidden: "Only the list's creator or an admin can change it.", "not-found-list": "That list is gone.", "slow-down": "Too many searches, wait a minute.",
        "not-reachable": "Seerr / TMDB did not answer. Try again in a minute.", store: "The list file can't be read; an admin must look at it.", other: "That did not work."
      },
      notFound: function (q) { return "Nothing found for “" + q + "”."; }
    },
    de: {
      title: "Sammlungen", intro: "Eigene Jellyfin-Sammlungen aus einer Suche, z. B. alles, was ein Produzent gemacht hat. Sie erscheinen in Jellyfin unter Collections & Trending; Titel, die noch nicht auf dem Server sind, lassen sich dort herunterladen.",
      newList: "Neue Liste", editBtn: "Bearbeiten", edit: function (n) { return "«" + n + "» bearbeiten"; }, name: "Name", query: "Suche", queryPh: "producer:Adam Sandler genre:comedy",
      sort: "Reihenfolge", sorts: { release: "Neueste zuerst", popularity: "Beliebteste", rating: "Beste Bewertung" }, max: "Höchstens (1–100)",
      preview: "Vorschau", save: "Speichern", cancel: "Abbrechen", loading: "Lädt…", saving: "Speichert…",
      help: "Suchbegriffe (kombinierbar, alle müssen passen):\nactor: — Filme und Serien mit dieser Person\ndirector: — Regie\nproducer: — Produzent oder ausführender Produzent\nwriter: — Drehbuch\nstudio: — Produktionsfirma (Filme)\nfranchise: — eine Filmreihe, z. B. franchise:Harry Potter\ngenre: — z. B. genre:komödie\nyear: — 1999 oder 1990-1999\ntype: — movie oder series\nkeyword: — ein TMDB-Stichwort, z. B. keyword:heist",
      helpLabel: "Hilfe zur Suche",
      pickTitle: function (v, f) { return "Welche/r «" + v + "» (" + f + ")?"; }, pickHint: "Mehrere passen zum Namen: eine/n wählen.",
      counts: function (n, o) { return n + " Titel · " + o + " auf dem Server"; },
      totalMore: function (n) { return " (" + n + " gefunden, die ersten bleiben)"; },
      owned: "✓ Auf dem Server", missing: "⬇ Noch nicht heruntergeladen",
      lists: function (n, total) { return "Listen (" + n + " von " + total + ")"; }, mine: function (n, max) { return "Du hast " + n + " von " + max + " Listen."; },
      by: function (u) { return "von " + (u || "jemandem"); }, readOnly: function (u) { return "Nur lesen — erstellt von " + (u || "jemandem"); },
      showAll: function (n) { return "Alle " + n + " zeigen"; }, showLess: "Weniger zeigen",
      del: "Löschen", delAgain: "Nochmals tippen zum Löschen", delWarn: "Löscht die Liste und ihre Jellyfin-Sammlung. Die Filme und Serien auf dem Server bleiben.",
      empty: "Noch keine Listen.", noCreate: "Nur wer Titel anfragen darf, kann Listen erstellen.",
      saved: "Gespeichert. Jellyfin zeigt sie in ein paar Minuten unter Collections & Trending.", deleted: "Gelöscht. Jellyfin entfernt die Sammlung in ein paar Minuten.",
      signin: "Anmelden, um die Listen zu sehen.", loadError: "Die Listen konnten nicht geladen werden. In einer Minute nochmals versuchen.", needPreview: "Zuerst Vorschau, dann speichern.",
      errors: {
        empty: "Zuerst eine Suche eingeben, z. B. producer:Adam Sandler.", "too-long": "Die Suche ist zu lang (höchstens 300 Zeichen).", "too-many": "Höchstens 6 Suchbegriffe.",
        syntax: "Mit einem Suchbegriff wie producer: oder actor: beginnen (siehe ?).", "empty-term": "Ein Suchbegriff hat keinen Wert.", year: "Jahr: 1999 oder 1990-1999.",
        type: "Typ: movie oder series.", genre: "Unbekanntes Genre (z. B. comedy, drama, horror, documentary).", twice: "Jahr, Typ und Genre je nur einmal.",
        "anchor-or-filter": "Eine Person, ein Studio oder eine Filmreihe angeben, oder mindestens zwei von genre, year, keyword.", sort: "Unbekannte Reihenfolge.", max: "Höchstens: eine Zahl von 1 bis 100.",
        pick: "Diese Auswahl passt nicht mehr, nochmals Vorschau.", name: "Name: 1 bis 60 Buchstaben, Ziffern und einfache Satzzeichen.", "name-taken": "Dieser Name ist schon vergeben.",
        "limit-user": "Du hast schon 5 Listen. Zuerst eine löschen.", "limit-total": "Es gibt schon 20 Listen. Zuerst eine löschen.", "no-titles": "Die Suche findet keine Titel.",
        forbidden: "Nur wer die Liste erstellt hat oder ein Admin kann sie ändern.", "not-found-list": "Diese Liste gibt es nicht mehr.", "slow-down": "Zu viele Suchen, eine Minute warten.",
        "not-reachable": "Seerr / TMDB antwortet nicht. In einer Minute nochmals versuchen.", store: "Die Listendatei ist nicht lesbar; ein Admin muss nachsehen.", other: "Das hat nicht geklappt."
      },
      notFound: function (q) { return "Nichts gefunden für «" + q + "»."; }
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

  function img(doc, path, alt) {
    if (typeof path !== "string" || !IMAGE_PATH.test(path)) return null;
    return el(doc, "img", { src: IMG + path, alt: alt || "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
  }

  function langOf(win, me) {
    var q = /[?&]lang=([a-z]+)/.exec(win.location.search || "");
    if (q) return q[1] === "de" ? "de" : "en";
    var tv = win.document.querySelector('a[href="/discover/tv"]:not([data-hg-link])');
    if (tv && /\bSerien\b/.test(tv.textContent)) return "de";
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

  function tip(e, text) {
    if (typeof window !== "undefined" && window.hgTip) window.hgTip.set(e, text);
    else e.setAttribute("aria-label", text.split("\n")[0]);
  }

  function App(win, root) {
    this.win = win; this.doc = win.document; this.root = root; this.t = TEXT.en; this.data = null;
    this.editing = null; this.picks = {}; this.previewed = null; this.open = {};
  }

  App.prototype.start = function () {
    var self = this;
    return Promise.all([call(this.win, "/api/v1/auth/me"), call(this.win, API + "lists")]).then(function (r) {
      self.t = TEXT[langOf(self.win, r[0].status === 200 ? r[0].body : null)];
      self.doc.documentElement.setAttribute("lang", self.t === TEXT.de ? "de" : "en");
      self.doc.title = self.t.title + " - Seerr";
      self.build();
      self.showLists(r[1]);
    });
  };

  App.prototype.build = function () {
    var t = this.t, d = this.doc;
    while (this.root.firstChild) this.root.removeChild(this.root.firstChild);
    this.root.appendChild(el(d, "h1", null, t.title));
    this.root.appendChild(el(d, "p", { "class": "hg-c-intro" }, t.intro));
    this.flash = this.root.appendChild(el(d, "div", { "data-flash-box": true }));
    this.formBox = this.root.appendChild(el(d, "div", { "data-form-box": true }));
    this.listBox = this.root.appendChild(el(d, "section", { "class": "hg-c-lists", "data-lists": true }));
  };

  App.prototype.reload = function () {
    var self = this;
    return call(this.win, API + "lists").then(function (r) { self.showLists(r); });
  };

  App.prototype.say = function (text, bad) {
    if (!this.msg) return;
    this.msg.textContent = text || "";
    this.msg.className = bad ? "hg-c-msg hg-c-bad" : "hg-c-msg";
  };

  App.prototype.errorText = function (r, query) {
    var e = this.t.errors, code = r.body && r.body.error;
    if (code === "not-found" && r.body && typeof r.body.term === "number") return this.t.notFound(query || "");
    if (code === "not-found") return e["not-found-list"];
    return (code && e[code]) || (r.status === 429 ? e["slow-down"] : r.status === 401 || r.status === 403 ? e.forbidden : e.other);
  };

  App.prototype.showLists = function (r) {
    var t = this.t, d = this.doc, self = this, box = this.listBox;
    while (box.firstChild) box.removeChild(box.firstChild);
    if (r.status === 401 || r.status === 403) {
      this.formBox.textContent = "";
      box.appendChild(el(d, "p", null, el(d, "a", { href: "/login" }, t.signin)));
      return;
    }
    if (r.status !== 200 || !r.body || !Array.isArray(r.body.lists)) {
      box.appendChild(el(d, "p", { "class": "hg-c-bad" }, t.loadError));
      return;
    }
    this.data = r.body;
    if (!this.form) this.showForm();
    var lim = r.body.limits || {};
    box.appendChild(el(d, "h2", null, t.lists(r.body.lists.length, lim.total || 20)));
    if (r.body.me && r.body.me.create) box.appendChild(el(d, "p", { "class": "hg-c-note", "data-mine": true }, t.mine(lim.mine || 0, lim.perUser || 5)));
    if (!r.body.lists.length) box.appendChild(el(d, "p", { "class": "hg-c-note", "data-empty": true }, t.empty));
    r.body.lists.forEach(function (l) { box.appendChild(self.card(l)); });
  };

  App.prototype.grid = function (items, limit) {
    var t = this.t, d = this.doc, ul = el(d, "ul", { "class": "hg-c-grid" });
    (items || []).slice(0, limit || items.length).forEach(function (i) {
      if (!i || (i.k !== "movie" && i.k !== "tv") || !/^[1-9][0-9]{0,9}$/.test(String(i.id))) return;
      var poster = el(d, "div", { "class": "hg-c-poster" }, img(d, i.p, i.t), el(d, "span", { "class": "hg-c-band", "data-state": i.o ? "owned" : "missing" }, i.o ? t.owned : t.missing));
      var a = el(d, "a", { href: "/" + i.k + "/" + i.id }, poster, el(d, "p", { "class": "hg-c-name" }, String(i.t || ""), i.y ? el(d, "span", { "class": "hg-c-year" }, " (" + i.y + ")") : null));
      ul.appendChild(el(d, "li", { "class": i.o ? "hg-c-owned" : "hg-c-missing", "data-title": i.k + "/" + i.id }, a));
    });
    return ul;
  };

  App.prototype.card = function (l) {
    var t = this.t, d = this.doc, self = this;
    var c = el(d, "article", { "class": "hg-c-card", "data-list": l.id });
    c.appendChild(el(d, "div", { "class": "hg-c-head" }, el(d, "h2", null, String(l.name || "")), el(d, "span", { "class": "hg-c-sub", "data-owner": true }, t.by(l.ownerName))));
    c.appendChild(el(d, "p", { "class": "hg-c-q", "data-query": true }, String(l.query || "")));
    c.appendChild(el(d, "p", { "class": "hg-c-counts", "data-counts": true }, t.counts(l.count || 0, l.owned || 0)));
    var open = !!this.open[l.id], items = l.items || [];
    c.appendChild(this.grid(items, open ? 0 : SHOWN));
    var actions = el(d, "div", { "class": "hg-c-actions" });
    if (items.length > SHOWN) {
      var more = el(d, "button", { type: "button", "class": "hg-c-btn", "data-act": "more" }, open ? t.showLess : t.showAll(items.length));
      more.addEventListener("click", function () { self.open[l.id] = !open; c.parentNode.replaceChild(self.card(l), c); });
      actions.appendChild(more);
    }
    if (l.canEdit) {
      var ed = el(d, "button", { type: "button", "class": "hg-c-btn", "data-act": "edit" }, t.editBtn);
      ed.addEventListener("click", function () { self.showForm(l); });
      actions.appendChild(ed);
      var del = el(d, "button", { type: "button", "class": "hg-c-btn", "data-act": "delete" }, t.del), armed = false;
      del.addEventListener("click", function () {
        if (!armed) {
          armed = true;
          del.textContent = t.delAgain;
          del.classList.add("hg-c-armed");
          actions.appendChild(el(d, "p", { "class": "hg-c-warning", "data-warning": true }, t.delWarn));
          return;
        }
        del.disabled = true;
        call(self.win, API + "delete", { id: l.id, confirm: "delete" }).then(function (r) {
          if (r.status === 200) { self.note(t.deleted); if (self.editing && self.editing.id === l.id) self.showForm(); return self.reload(); }
          del.disabled = false;
          actions.appendChild(el(d, "p", { "class": "hg-c-msg hg-c-bad" }, self.errorText(r)));
        });
      });
      actions.appendChild(del);
    } else {
      c.appendChild(el(d, "p", { "class": "hg-c-readonly", "data-readonly": true }, t.readOnly(l.ownerName)));
    }
    if (actions.firstChild) c.appendChild(actions);
    return c;
  };

  App.prototype.note = function (text) {
    while (this.flash.firstChild) this.flash.removeChild(this.flash.firstChild);
    if (text) this.flash.appendChild(el(this.doc, "p", { "class": "hg-c-flash", "data-flash": true, role: "status" }, text));
  };

  App.prototype.showForm = function (l) {
    var t = this.t, d = this.doc, self = this, box = this.formBox;
    while (box.firstChild) box.removeChild(box.firstChild);
    this.form = null;
    if (!this.data || !this.data.me || !this.data.me.create) {
      box.appendChild(el(d, "p", { "class": "hg-c-note", "data-no-create": true }, t.noCreate));
      return;
    }
    this.editing = l || null; this.picks = {}; this.previewed = null;
    var f = el(d, "form", { "class": "hg-c-card hg-c-form", "data-form": true, novalidate: true });
    f.addEventListener("submit", function (ev) { ev.preventDefault(); });
    f.appendChild(el(d, "h2", { "data-form-title": true }, l ? t.edit(String(l.name || "")) : t.newList));
    var name = el(d, "input", { "class": "hg-c-input", "data-field": "name", maxlength: "60", autocomplete: "off" });
    var query = el(d, "input", { "class": "hg-c-input", "data-field": "query", maxlength: "300", autocomplete: "off", spellcheck: "false", placeholder: t.queryPh });
    var help = el(d, "button", { type: "button", "class": "hg-c-help", "data-help": true, "aria-label": t.helpLabel }, "?");
    tip(help, t.help);
    var sort = el(d, "select", { "data-field": "sort" });
    ["release", "popularity", "rating"].forEach(function (s) { sort.appendChild(el(d, "option", { value: s }, t.sorts[s])); });
    var max = el(d, "input", { "class": "hg-c-input", "data-field": "max", type: "number", min: "1", max: "100", step: "1", inputmode: "numeric" });
    name.value = l ? String(l.name || "") : "";
    query.value = l ? String(l.query || "") : "";
    sort.value = l && l.sort ? l.sort : "release";
    max.value = String(l && l.max ? l.max : 50);
    f.appendChild(el(d, "label", { "class": "hg-c-label" }, t.name, name));
    f.appendChild(el(d, "label", { "class": "hg-c-label" }, t.query, el(d, "span", { "class": "hg-c-query" }, query, help)));
    f.appendChild(el(d, "div", { "class": "hg-c-row" }, el(d, "label", { "class": "hg-c-label" }, t.sort, sort), el(d, "label", { "class": "hg-c-label hg-c-small" }, t.max, max)));
    var pv = el(d, "button", { type: "button", "class": "hg-c-btn", "data-act": "preview" }, t.preview);
    var sv = el(d, "button", { type: "button", "class": "hg-c-btn hg-c-primary", "data-act": "save", disabled: true }, t.save);
    var row = el(d, "div", { "class": "hg-c-row" }, pv, sv);
    if (l) {
      var cancel = el(d, "button", { type: "button", "class": "hg-c-btn", "data-act": "cancel" }, t.cancel);
      cancel.addEventListener("click", function () { self.showForm(); });
      row.appendChild(cancel);
    }
    f.appendChild(row);
    this.msg = f.appendChild(el(d, "p", { "class": "hg-c-msg", "data-msg": true, role: "status" }));
    this.pickBox = f.appendChild(el(d, "div", { "class": "hg-c-picks", "data-picks": true }));
    this.previewBox = f.appendChild(el(d, "div", { "data-preview": true }));
    this.form = { name: name, query: query, sort: sort, max: max, preview: pv, save: sv };
    var changed = function (ev) {
      if (ev && ev.target === query) self.picks = {};
      self.previewed = null;
      sv.disabled = true;
    };
    [name, query, max].forEach(function (x) { x.addEventListener("input", changed); });
    sort.addEventListener("change", changed);
    query.addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); self.preview(); } });
    pv.addEventListener("click", function () { self.preview(); });
    sv.addEventListener("click", function () { self.save(); });
    box.appendChild(f);
    if (l && f.scrollIntoView) try { f.scrollIntoView({ block: "start", behavior: "smooth" }); } catch (e) { /* old browsers */ }
  };

  App.prototype.values = function () {
    var f = this.form;
    return { name: f.name.value.trim(), query: f.query.value.trim(), sort: f.sort.value, max: Number(f.max.value) };
  };

  App.prototype.clearResults = function () {
    while (this.pickBox.firstChild) this.pickBox.removeChild(this.pickBox.firstChild);
    while (this.previewBox.firstChild) this.previewBox.removeChild(this.previewBox.firstChild);
  };

  App.prototype.preview = function () {
    var self = this, t = this.t, v = this.values(), f = this.form;
    var url = API + "preview?q=" + encodeURIComponent(v.query) + "&sort=" + encodeURIComponent(v.sort) + "&max=" + encodeURIComponent(String(v.max));
    Object.keys(this.picks).forEach(function (k) { url += "&pick=" + encodeURIComponent(k + ":" + self.picks[k]); });
    f.preview.disabled = true; f.save.disabled = true;
    f.preview.textContent = t.loading;
    this.say("");
    return call(this.win, url).then(function (r) {
      f.preview.disabled = false;
      f.preview.textContent = t.preview;
      self.clearResults();
      if (r.status !== 200 || !r.body) return self.say(self.errorText(r, v.query), true);
      if (r.body.status === "pick") return self.showPicks(r.body.picks || []);
      self.showPreview(r.body);
      self.previewed = JSON.stringify(self.values());
      f.save.disabled = !self.values().name;
      if (!self.values().name) self.say(t.errors.name, true);
    });
  };

  App.prototype.showPicks = function (picks) {
    var self = this, t = this.t, d = this.doc;
    picks.forEach(function (p) {
      var opts = el(d, "div", { "class": "hg-c-options" });
      (p.options || []).forEach(function (o) {
        if (!o || !/^[1-9][0-9]{0,9}$/.test(String(o.id))) return;
        var known = Array.isArray(o.known) ? o.known.filter(function (k) { return typeof k === "string"; }).join(", ") : "";
        var b = el(d, "button", { type: "button", "class": "hg-c-option", "data-pick": String(o.id) },
          img(d, o.photo, String(o.name || "")) || el(d, "span", { "class": "hg-c-face", "aria-hidden": "true" }),
          el(d, "span", null, el(d, "b", null, String(o.name || "")), known ? el(d, "small", null, known) : null));
        b.addEventListener("click", function () { self.picks[p.term] = o.id; self.preview(); });
        opts.appendChild(b);
      });
      self.pickBox.appendChild(el(d, "h3", null, t.pickTitle(String(p.value || ""), String(p.field || ""))));
      self.pickBox.appendChild(el(d, "p", { "class": "hg-c-note" }, t.pickHint));
      self.pickBox.appendChild(opts);
    });
  };

  App.prototype.showPreview = function (b) {
    var t = this.t, d = this.doc;
    var counts = t.counts(b.count || 0, b.owned || 0) + (b.total > b.count ? t.totalMore(b.total) : "");
    this.previewBox.appendChild(el(d, "p", { "class": "hg-c-q" }, String(b.query || "")));
    this.previewBox.appendChild(el(d, "p", { "class": "hg-c-counts", "data-counts": true }, counts));
    this.previewBox.appendChild(this.grid(b.items || []));
  };

  App.prototype.save = function () {
    var self = this, t = this.t, v = this.values(), f = this.form;
    if (this.previewed !== JSON.stringify(v)) return this.say(t.needPreview, true);
    var body = { name: v.name, query: v.query, sort: v.sort, max: v.max, picks: this.picks };
    if (this.editing) body = Object.assign({ id: this.editing.id }, body);
    f.save.disabled = true; f.preview.disabled = true;
    f.save.textContent = t.saving;
    return call(this.win, API + "save", body).then(function (r) {
      f.save.textContent = t.save;
      f.preview.disabled = false;
      if (r.status === 200) {
        self.note(t.saved);
        self.showForm();
        return self.reload();
      }
      if (r.status === 409 && r.body && r.body.status === "pick") { self.clearResults(); self.showPicks(r.body.picks || []); return; }
      f.save.disabled = false;
      self.say(self.errorText(r, v.query), true);
    });
  };

  // On /collections: our page as the first child of Seerr's <main> (its 404 hidden by CSS), the same node and state put back when React re-renders; gone again on any other route.
  var app = null;
  function sync(win) {
    var doc = win.document, main = doc.querySelector("main");
    var here = win.location.pathname.replace(/\/+$/, "") === ROUTE;
    if (!here) {
      if (doc.documentElement.getAttribute("data-hg-early") === "collections") doc.documentElement.removeAttribute("data-hg-early");
      if (app) { if (app.root.parentNode) app.root.parentNode.removeChild(app.root); app = null; }
      var m = doc.querySelector("main[data-hg-collections]");
      if (m) m.removeAttribute("data-hg-collections");
      return;
    }
    if (!main || !win.fetch) return;
    if (!doc.getElementById("hg-collections-style")) {
      var st = doc.createElement("style");
      st.id = "hg-collections-style";
      st.textContent = CSS;
      (doc.head || doc.documentElement).appendChild(st);
    }
    if (!main.hasAttribute("data-hg-collections")) main.setAttribute("data-hg-collections", "");
    if (!app) {
      var root = doc.createElement("div");
      root.id = "hg-collections";
      app = new App(win, root);
      app.start();
    }
    if (app.root.parentNode !== main || main.firstChild !== app.root) main.insertBefore(app.root, main.firstChild);
    if (doc.documentElement.getAttribute("data-hg-early") === "collections") doc.documentElement.removeAttribute("data-hg-early");
    var title = app.t.title + " - Seerr";
    if (app.data && doc.title !== title) doc.title = title;
  }

  if (typeof window === "undefined") return;
  window.hgCollections = { TEXT: TEXT };
  // React must hydrate Seerr's server-rendered 404 first: our page put in before makes it throw error #418 and redraw everything (as in manual.js)
  function hydrated(doc) {
    var root = doc.getElementById("__next");
    if (!root) return true;
    var first = root.firstElementChild;
    if (!first) return false;
    for (var k in first) if (k.indexOf("__reactFiber$") === 0) return true;
    return false;
  }

  try {
    if (!window.MutationObserver) return;
    var go = function () {
      document.documentElement.removeAttribute("data-hg-collections-wait");
      sync(window);
      new MutationObserver(function () {
        try { sync(window); } catch (e) { /* never break Seerr */ }
      }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    };
    if (hydrated(document)) go();
    else {
      if (window.location.pathname.replace(/\/+$/, "") === ROUTE) {
        var st = document.createElement("style");
        st.id = "hg-collections-wait";
        st.textContent = "html[data-hg-collections-wait] main { visibility: hidden; }";
        (document.head || document.documentElement).appendChild(st);
        document.documentElement.setAttribute("data-hg-collections-wait", "");
      }
      var frame = window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function (f) { setTimeout(f, 16); };
      var until = Date.now() + 10000;
      var wait = function () {
        try { if (hydrated(document) || Date.now() > until) go(); else frame(wait); } catch (e) { go(); }
      };
      frame(wait);
    }
  } catch (e) { /* never break Seerr */ }
})();
