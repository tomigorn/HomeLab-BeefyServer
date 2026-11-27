// My library page /my-library inside Seerr's layout (README "My library"): Seerr shows its 404 there, this script puts the page in its place. Every downloaded title with its state and the Keep forever / Delete early actions; what is allowed comes from the backend (/_download-now/library/list, rules in retention_rules.py), never decided here. All text goes in as textContent.
(function () {
  "use strict";
  var ROUTE = "/my-library", LIST = "/_download-now/library/list", KEEP = "/_download-now/retention/keep", DAPI = "/_download-now/delete-early/";
  var MONTHS = { en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], de: ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."] };
  var TEXT = {
    en: {
      title: "My library", intro: "Everything on the server. ♾️ Keep forever stays for good; a ⭐ fan favourite stays as long as someone keeps their mark; the rest leaves 3 months after the download or the last play.",
      quota: "{d} of {dm} deletions left this week · {m} of {mm} fan-favourite marks left", noGroup: "Only members of the requesters group can mark or delete.",
      filters: { all: "All", mine: "Mine", temporary: "Temporary", keep: "Keep forever", soon: "Leaving soon", bin: "In recycle bin" }, search: "Search titles",
      film: "Film", series: "Series", temporary: "🗑 Temporary · leaves {d}", temporaryNoDate: "🗑 Temporary", keep: "♾️ Keep forever", fan: "⭐ Fan favourite", soon: "Leaving soon · {d}", bin: "In the recycle bin until {d}",
      why: { old: "released 12 or more years ago", niche: "niche", goated: "top-rated on IMDb", protected: "the permanent test film" }, fanOf: "fan favourite of {names}", you: "you",
      mark: "⭐ Fan favourite", unmark: "Remove my mark", del: "🗑 Delete", restore: "↩ Restore", select: "Select {t}",
      waiting: "⏳ Waiting for {names} to OK it until {d}.", queued: "⏳ Working on it…", inBin: "🗑 In the recycle bin until {d} (deleted by {who}).",
      asked: "Waiting for your OK", wants: "{who} wants to delete {t}: {reason}. Answer until {d}.", approve: "✅ Approve", deny: "✖ Deny",
      reason: { fan: "you marked it ⭐ fan favourite", watching: "you have started watching it", keep: "it is marked ♾️ Keep forever" },
      askReason: { fan: "fan favourite", watching: "watching it" },
      bulk: "🗑 Delete selected ({n})", left: "{n} more deletions this week.",
      confirm: "Delete {n} {titles}?", one: "title", many: "titles", now: "To the recycle bin now:", ask: "Becomes a request first:", askItem: "{t} — {names} ({reason})",
      note: "The files stay 7 days in the recycle bin and can be restored; the titles stay requestable. Who is watching is checked again when it runs.",
      go: "🗑 Delete", cancel: "Cancel", busy: "…", loading: "Loading…", empty: "No titles match.", loadError: "Could not load the library. Try again in a minute.",
      sent: "Sent. It shows here in a moment.", failed: "Not saved: {e}",
      errors: { quota: "at most 10 deletions a week", "mark-quota": "at most 5 fan-favourite marks a week", already: "already being deleted", protected: "protected", kept: "kept forever", library: "not on the server",
        group: "only requesters", forbidden: "not allowed", "not your mark": "only who set the mark removes it", closed: "already decided", "too many": "too fast, try again in a moment", failed: "something went wrong" }
    },
    de: {
      title: "Meine Bibliothek", intro: "Alles auf dem Server. ♾️ Für immer behalten bleibt; ein ⭐ Fan-Liebling bleibt, solange jemand seine Markierung behält; der Rest geht 3 Monate nach dem Download oder dem letzten Abspielen.",
      quota: "Noch {d} von {dm} Löschungen diese Woche · {m} von {mm} Fan-Liebling-Markierungen übrig", noGroup: "Markieren und löschen können nur Mitglieder der Anfragenden-Gruppe.",
      filters: { all: "Alle", mine: "Meine", temporary: "Vorübergehend", keep: "Für immer", soon: "Geht bald", bin: "Im Papierkorb" }, search: "Titel suchen",
      film: "Film", series: "Serie", temporary: "🗑 Vorübergehend · geht am {d}", temporaryNoDate: "🗑 Vorübergehend", keep: "♾️ Für immer behalten", fan: "⭐ Fan-Liebling", soon: "Geht bald · {d}", bin: "Im Papierkorb bis {d}",
      why: { old: "vor 12 oder mehr Jahren erschienen", niche: "Nische", goated: "top bewertet auf IMDb", protected: "der feste Testfilm" }, fanOf: "Fan-Liebling von {names}", you: "dir",
      mark: "⭐ Fan-Liebling", unmark: "Meine Markierung entfernen", del: "🗑 Löschen", restore: "↩ Wiederherstellen", select: "{t} auswählen",
      waiting: "⏳ Wartet bis {d} auf das OK von {names}.", queued: "⏳ Wird erledigt…", inBin: "🗑 Im Papierkorb bis {d} (gelöscht von {who}).",
      asked: "Wartet auf dein OK", wants: "{who} möchte {t} löschen: {reason}. Antwort bis {d}.", approve: "✅ Zustimmen", deny: "✖ Ablehnen",
      reason: { fan: "du hast es als ⭐ Fan-Liebling markiert", watching: "du hast angefangen, es zu schauen", keep: "als ♾️ Für immer behalten markiert" },
      askReason: { fan: "Fan-Liebling", watching: "schaut es" },
      bulk: "🗑 Auswahl löschen ({n})", left: "Noch {n} Löschungen diese Woche.",
      confirm: "{n} {titles} löschen?", one: "Titel", many: "Titel", now: "Sofort in den Papierkorb:", ask: "Wird zuerst eine Anfrage:", askItem: "{t} — {names} ({reason})",
      note: "Die Dateien bleiben 7 Tage im Papierkorb und lassen sich wiederherstellen; die Titel bleiben anfragbar. Wer schaut, wird beim Ausführen nochmals geprüft.",
      go: "🗑 Löschen", cancel: "Abbrechen", busy: "…", loading: "Lädt…", empty: "Keine passenden Titel.", loadError: "Die Bibliothek liess sich nicht laden. In einer Minute nochmals versuchen.",
      sent: "Gesendet. Es erscheint hier gleich.", failed: "Nicht gespeichert: {e}",
      errors: { quota: "höchstens 10 Löschungen pro Woche", "mark-quota": "höchstens 5 Fan-Liebling-Markierungen pro Woche", already: "wird schon gelöscht", protected: "geschützt", kept: "für immer behalten", library: "nicht auf dem Server",
        group: "nur Anfragende", forbidden: "nicht erlaubt", "not your mark": "nur wer markiert hat, kann es entfernen", closed: "schon entschieden", "too many": "zu schnell, gleich nochmals versuchen", failed: "etwas ist schiefgegangen" }
    }
  };
  var CSS = "#hg-library{max-width:64rem;margin:0 auto;padding:1rem 1rem 7rem;color:#e5e7eb;font-size:15px;line-height:1.5}main[data-hg-library]>:not(#hg-library){display:none!important}" +
    "#hg-library *{box-sizing:border-box}#hg-library h1{margin:.5rem 0 .25rem;font-size:1.75rem;font-weight:700;color:#fff}#hg-library h2{margin:0 0 .5rem;font-size:1.1rem;color:#fff}#hg-library a{color:#fff;text-decoration:none;font-weight:600;overflow-wrap:anywhere}#hg-library a:hover{text-decoration:underline}" +
    ".hg-l-intro,.hg-l-note{margin:0 0 .75rem;color:#9ca3af}.hg-l-quota{margin:0 0 1rem;color:#d1d5db;font-size:.9rem}" +
    ".hg-l-bar{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;margin:0 0 .75rem}.hg-l-chip{background:#1f2937;color:#e5e7eb;border:1px solid #4b5563;border-radius:9999px;padding:.15rem .7rem;font:inherit;font-size:.85rem;cursor:pointer}" +
    ".hg-l-chip[aria-pressed=true]{background:#4f46e5;border-color:#6366f1;color:#fff}.hg-l-search{flex:1 1 12rem;min-width:0;background:#111827;color:#fff;border:1px solid #4b5563;border-radius:.375rem;padding:.35rem .6rem;font:inherit}" +
    ".hg-l-card{background:#1f2937;border:1px solid #374151;border-radius:.75rem;padding:1rem;margin:0 0 1rem}.hg-l-asked{border-color:rgba(251,146,60,.7)}" +
    ".hg-l-list{list-style:none;margin:0;padding:0}.hg-l-row{display:flex;gap:.6rem;align-items:flex-start;padding:.6rem .25rem;border-top:1px solid #374151;min-width:0}.hg-l-row input{margin-top:.3rem;width:1.1rem;height:1.1rem;flex:none;accent-color:#ef4444}" +
    ".hg-l-main{flex:1 1 0;min-width:0}.hg-l-meta{color:#9ca3af;font-size:.8rem}.hg-l-state{display:flex;flex-wrap:wrap;gap:.25rem .5rem;align-items:center;margin-top:.2rem;font-size:.85rem}" +
    ".hg-l-pill{display:inline-flex;padding:0 .5rem;border-radius:9999px;font-size:.75rem;line-height:1.25rem;border:1px solid;white-space:nowrap}.hg-l-keep{background:rgba(79,70,229,.25);color:#c7d2fe;border-color:rgba(129,140,248,.5)}" +
    ".hg-l-fan{background:rgba(202,138,4,.25);color:#fde68a;border-color:rgba(250,204,21,.5)}.hg-l-temporary{background:rgba(75,85,99,.45);color:#e5e7eb;border-color:rgba(156,163,175,.5)}.hg-l-soon{background:rgba(234,88,12,.3);color:#fed7aa;border-color:rgba(251,146,60,.7)}.hg-l-bin{background:rgba(127,29,29,.4);color:#fecaca;border-color:rgba(248,113,113,.6)}" +
    ".hg-l-sub{color:#9ca3af;font-size:.8rem;overflow-wrap:anywhere}.hg-l-line{margin-top:.25rem;font-size:.85rem;display:flex;flex-wrap:wrap;gap:.4rem;align-items:center}" +
    ".hg-l-actions{display:flex;flex-wrap:wrap;gap:.35rem;justify-content:flex-end;flex:0 1 auto}.hg-l-btn{padding:0 .6rem;border-radius:.375rem;font:inherit;font-size:.78rem;line-height:1.6rem;background:#374151;color:#e5e7eb;border:1px solid #6b7280;cursor:pointer;white-space:nowrap}.hg-l-btn:hover:not(:disabled){background:#4b5563}.hg-l-btn:disabled{opacity:.5;cursor:default}" +
    ".hg-l-danger{background:#7f1d1d;border-color:#dc2626}.hg-l-sticky{position:sticky;bottom:4.5rem;z-index:5;display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;justify-content:space-between;background:#111827;border:1px solid #374151;border-radius:.5rem;padding:.5rem .75rem;margin:.75rem 0}" +
    ".hg-l-confirm{border:1px solid rgba(248,113,113,.6);border-radius:.5rem;background:rgba(127,29,29,.35);padding:.75rem;margin:.75rem 0}.hg-l-confirm ul{margin:.25rem 0 .5rem 1.1rem;padding:0}.hg-l-confirm li{overflow-wrap:anywhere}" +
    ".hg-l-msg{margin:.5rem 0;color:#a5b4fc}.hg-l-bad{color:#fca5a5}@media (max-width:639px){.hg-l-row{flex-wrap:wrap}.hg-l-actions{flex-basis:100%;justify-content:flex-start;padding-left:1.7rem}}";

  function fill(t, map) { return String(t).replace(/\{(\w+)\}/g, function (_, k) { return map[k] === undefined ? "" : String(map[k]); }); }

  function lang(doc) {
    var nl = typeof window !== "undefined" && window.hgNightLabel;
    if (nl && nl.lang) { try { return nl.lang() === "de" ? "de" : "en"; } catch (e) { /* own detection */ } }
    var tv = doc.querySelector('a[href="/discover/tv"]');
    return tv && /\bSerien\b/.test(tv.textContent) ? "de" : "en";
  }

  function day(iso, l) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    if (!m) return "";
    var year = Number(m[1]) !== new Date().getFullYear() ? " " + m[1] : "";
    return Number(m[3]) + (l === "de" ? ". " : " ") + MONTHS[l][Number(m[2]) - 1] + year;
  }

  function human(n) {
    if (!(n > 0)) return "";
    return n >= 1099511627776 ? (n / 1099511627776).toFixed(1) + " TB" : (n / 1073741824).toFixed(1) + " GB";
  }

  function request(method, url, body) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      x.__hgSkip = true;
      x.open(method, url);
      x.withCredentials = true;
      if (body !== undefined) x.setRequestHeader("Content-Type", "application/json");
      x.onload = function () {
        var data = null;
        try { data = JSON.parse(x.responseText); } catch (e) { data = null; }
        if (x.status >= 200 && x.status < 300) resolve(data || {}); else reject(data || { error: "failed" });
      };
      x.onerror = function () { reject({ error: "failed" }); };
      x.send(body === undefined ? undefined : JSON.stringify(body));
    });
  }

  function App(win, root) {
    this.win = win;
    this.doc = win.document;
    this.root = root;
    this.l = lang(this.doc);
    this.t = TEXT[this.l];
    this.data = null;
    this.failed = false;
    this.filter = "all";
    this.q = "";
    this.selected = [];
    this.confirm = null;
    this.msg = null;
    this.timers = [];
  }

  App.prototype.el = function (tag, attrs, text) {
    var e = this.doc.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };

  App.prototype.btn = function (attr, text, fn, cls) {
    var b = this.el("button", { type: "button", "class": "hg-l-btn" + (cls ? " " + cls : "") }, text);
    b.setAttribute(attr, "");
    b.addEventListener("click", function (e) { e.preventDefault(); fn(b); });
    return b;
  };

  App.prototype.load = function () {
    var self = this;
    return request("GET", LIST).then(function (d) {
      self.data = d;
      self.failed = false;
      var keys = (d.titles || []).filter(function (t) { return t.del && t.del.allowed; }).map(function (t) { return t.key; });
      self.selected = self.selected.filter(function (k) { return keys.indexOf(k) >= 0; });
    }, function () { if (!self.data) self.failed = true; }).then(function () { self.render(); });
  };

  App.prototype.later = function () {
    var self = this;
    [2500, 8000, 20000].forEach(function (ms) { self.timers.push(setTimeout(function () { self.load(); }, ms)); });
  };

  App.prototype.act = function (url, body, after) {
    var self = this;
    self.msg = null;
    return request("POST", url, body).then(function (r) {
      self.msg = { text: self.t.sent };
      if (after) after(r);
    }, function (err) {
      var t = self.t, e = (err && err.error) || "failed";
      if (e === "refused" && err.titles) {
        var names = {};
        (self.data.titles || []).forEach(function (x) { names[x.key] = x.t; });
        self.msg = { bad: true, text: Object.keys(err.titles).map(function (k) { return (names[k] || k) + ": " + (t.errors[err.titles[k]] || err.titles[k]); }).join(" · ") };
      } else {
        self.msg = { bad: true, text: fill(t.failed, { e: t.errors[e] || t.errors.failed }) };
      }
    }).then(function () { self.confirm = null; self.load(); self.later(); });
  };

  App.prototype.shown = function () {
    var f = this.filter, q = this.q.trim().toLowerCase();
    return (this.data.titles || []).filter(function (x) {
      if (q && String(x.t || "").toLowerCase().indexOf(q) < 0) return false;
      if (f === "all") return true;
      if (f === "mine") return !!x.mine;
      return x.state === f;
    });
  };

  App.prototype.left = function () {
    var q = (this.data.quota || {}).delete || {};
    return Math.max(0, (q.max || 0) - (q.used || 0));
  };

  App.prototype.whyText = function (x) {
    var t = this.t;
    if (x.source === "protected") return t.why.protected;
    if (x.source === "default" && x.state === "keep") return (x.reasons || []).map(function (r) { return t.why[r] || r; }).join(", ");
    if ((x.marks || []).length) return fill(t.fanOf, { names: x.marks.map(function (m) { return m.mine ? t.you : m.name || "?"; }).join(", ") });
    return "";
  };

  App.prototype.pill = function (x) {
    var t = this.t, l = this.l, kind = x.state, text;
    if (kind === "keep") { kind = x.source === "fan" ? "fan" : "keep"; text = kind === "fan" ? t.fan : t.keep; }
    else if (kind === "soon") text = fill(t.soon, { d: day(x.deleteOn, l) });
    else if (kind === "bin") text = fill(t.bin, { d: day(((x.items || [])[0] || {}).binUntil, l) });
    else text = x.deleteOn ? fill(t.temporary, { d: day(x.deleteOn, l) }) : t.temporaryNoDate;
    var p = this.el("span", { "class": "hg-l-pill hg-l-" + kind, "data-hg-l-state": kind }, text);
    return p;
  };

  App.prototype.itemLine = function (x, it) {
    var t = this.t, l = this.l, self = this, line = this.el("div", { "class": "hg-l-line" });
    var open = (it.asks || []).filter(function (a) { return a.answer === null; });
    var names = (open.length ? open : it.asks || []).reduce(function (a, b) { return a.concat(b.names || []); }, []).join(", ");
    if (it.state === "pending") line.appendChild(this.el("span", {}, fill(t.waiting, { names: names, d: day(it.expires, l) })));
    else if (it.state === "approved") line.appendChild(this.el("span", {}, t.queued));
    else if (it.state === "deleted" && x.state !== "bin") line.appendChild(this.el("span", {}, fill(t.inBin, { d: day(it.binUntil, l), who: it.byName || "" })));
    if (it.state === "deleted" && it.canRestore) line.appendChild(this.btn("data-hg-l-restore", t.restore, function (b) { b.disabled = true; self.act(DAPI + "restore", { id: it.id }); }));
    return line.childNodes.length ? line : null;
  };

  App.prototype.rowEl = function (x) {
    var t = this.t, self = this, sel = this.selected;
    var li = this.el("li", { "class": "hg-l-row", "data-hg-l-row": x.key });
    if (x.del && x.del.allowed) {
      var c = this.el("input", { type: "checkbox", "aria-label": fill(t.select, { t: x.t || "" }) });
      c.checked = sel.indexOf(x.key) >= 0;
      c.disabled = !c.checked && sel.length >= this.left();
      c.addEventListener("change", function () {
        var i = self.selected.indexOf(x.key);
        if (c.checked && i < 0 && self.selected.length < self.left()) self.selected.push(x.key);
        if (!c.checked && i >= 0) self.selected.splice(i, 1);
        self.confirm = null;
        self.render();
      });
      li.appendChild(c);
    }
    var main = this.el("div", { "class": "hg-l-main" });
    main.appendChild(this.el("a", { href: "/" + (x.kind === "tv" ? "tv" : "movie") + "/" + (Number(x.tmdb) || 0) }, (x.t || "") + (x.y ? " (" + x.y + ")" : "")));
    main.appendChild(this.el("div", { "class": "hg-l-meta" }, [x.kind === "tv" ? t.series : t.film, human(x.size), x.tier ? String(x.tier).toUpperCase().replace("SPLIT", "SSD + HDD") : ""].filter(Boolean).join(" · ")));
    var st = this.el("div", { "class": "hg-l-state" });
    st.appendChild(this.pill(x));
    var why = this.whyText(x);
    if (why) st.appendChild(this.el("span", { "class": "hg-l-sub" }, why));
    main.appendChild(st);
    (x.items || []).forEach(function (it) { var line = self.itemLine(x, it); if (line) main.appendChild(line); });
    li.appendChild(main);
    var acts = this.el("div", { "class": "hg-l-actions" });
    var kt = { type: x.kind, tmdbId: Number(x.tmdb) };
    if (x.canMark) acts.appendChild(this.btn("data-hg-l-mark", t.mark, function (b) { b.disabled = true; self.act(KEEP, { type: kt.type, tmdbId: kt.tmdbId, keep: true }); }));
    if (x.canUnmark) acts.appendChild(this.btn("data-hg-l-unmark", t.unmark, function (b) { b.disabled = true; self.act(KEEP, { type: kt.type, tmdbId: kt.tmdbId, keep: false }); }));
    if (x.del && x.del.allowed && this.left() > 0) acts.appendChild(this.btn("data-hg-l-delete", t.del, function () { self.confirm = [x.key]; self.render(); }, "hg-l-danger"));
    if (acts.childNodes.length) li.appendChild(acts);
    return li;
  };

  App.prototype.confirmEl = function () {
    var t = this.t, self = this, byKey = {};
    (this.data.titles || []).forEach(function (x) { byKey[x.key] = x; });
    var picked = this.confirm.map(function (k) { return byKey[k]; }).filter(Boolean);
    var box = this.el("div", { "class": "hg-l-confirm", role: "alertdialog", "data-hg-l-confirm": "" });
    box.appendChild(this.el("strong", {}, fill(t.confirm, { n: picked.length, titles: picked.length === 1 ? t.one : t.many })));
    var now = picked.filter(function (x) { return !(x.del.plan || []).length; }), ask = picked.filter(function (x) { return (x.del.plan || []).length; });
    var name = function (x) { return (x.t || "") + (x.y ? " (" + x.y + ")" : ""); };
    if (now.length) {
      box.appendChild(this.el("div", {}, t.now));
      var ul = this.el("ul", { "data-hg-l-now": "" });
      now.forEach(function (x) { ul.appendChild(self.el("li", {}, name(x))); });
      box.appendChild(ul);
    }
    if (ask.length) {
      box.appendChild(this.el("div", {}, t.ask));
      var ul2 = this.el("ul", { "data-hg-l-ask": "" });
      ask.forEach(function (x) {
        x.del.plan.forEach(function (a) { ul2.appendChild(self.el("li", {}, fill(t.askItem, { t: name(x), names: (a.names || []).map(function (n) { return n || "?"; }).join(", "), reason: t.askReason[a.reason] || a.reason }))); });
      });
      box.appendChild(ul2);
    }
    box.appendChild(this.el("p", { "class": "hg-l-note" }, t.note));
    var bar = this.el("div", { "class": "hg-l-actions" });
    bar.appendChild(this.btn("data-hg-l-go", t.go, function (b) {
      b.disabled = true;
      b.textContent = t.busy;
      var items = picked.map(function (x) { return { type: x.kind, tmdbId: Number(x.tmdb) }; });
      self.act(DAPI + "bulk", { items: items }, function () { self.selected = []; });
    }, "hg-l-danger"));
    bar.appendChild(this.btn("data-hg-l-cancel", t.cancel, function () { self.confirm = null; self.render(); }));
    box.appendChild(bar);
    return box;
  };

  App.prototype.askedEl = function () {
    var t = this.t, l = this.l, self = this;
    var box = this.el("section", { "class": "hg-l-card hg-l-asked", "data-hg-l-asked": "" });
    box.appendChild(this.el("h2", {}, t.asked));
    this.data.asked.forEach(function (a) {
      var it = a.item || {}, open = (it.asks || []).filter(function (x) { return x.answer === null; });
      var line = self.el("div", { "class": "hg-l-line" });
      line.appendChild(self.el("span", {}, fill(t.wants, { who: it.byName || "", t: (a.t || "") + (a.y ? " (" + a.y + ")" : ""), reason: t.reason[(open[0] || {}).reason] || "", d: day(it.expires, l) })));
      line.appendChild(self.btn("data-hg-l-approve", t.approve, function (b) { b.disabled = true; self.act(DAPI + "answer", { id: it.id, approve: true }); }));
      line.appendChild(self.btn("data-hg-l-deny", t.deny, function (b) { b.disabled = true; self.act(DAPI + "answer", { id: it.id, approve: false }); }));
      box.appendChild(line);
    });
    return box;
  };

  App.prototype.render = function () {
    var t = this.t, self = this, root = this.root;
    var search = root.querySelector("[data-hg-l-search]"), focused = search && this.doc.activeElement === search, pos = focused ? search.selectionStart : null;
    while (root.firstChild) root.removeChild(root.firstChild);
    root.appendChild(this.el("h1", {}, t.title));
    root.appendChild(this.el("p", { "class": "hg-l-intro" }, t.intro));
    if (!this.data) {
      root.appendChild(this.el("p", { "class": "hg-l-note" + (this.failed ? " hg-l-bad" : "") }, this.failed ? t.loadError : t.loading));
      return;
    }
    var q = this.data.quota || {}, dq = q.delete || {}, mq = q.marks || {};
    root.appendChild(this.el("p", { "class": "hg-l-quota" }, fill(t.quota, { d: this.left(), dm: dq.max || 0, m: Math.max(0, (mq.max || 0) - (mq.used || 0)), mm: mq.max || 0 })));
    if (!this.data.group) root.appendChild(this.el("p", { "class": "hg-l-note" }, t.noGroup));
    if (this.msg) root.appendChild(this.el("p", { "class": "hg-l-msg" + (this.msg.bad ? " hg-l-bad" : "") }, this.msg.text));
    if ((this.data.asked || []).length) root.appendChild(this.askedEl());
    var bar = this.el("div", { "class": "hg-l-bar" });
    ["all", "mine", "temporary", "keep", "soon", "bin"].forEach(function (f) {
      if (f === "mine" && !self.data.mineKnown) return;
      var b = self.el("button", { type: "button", "class": "hg-l-chip", "data-hg-l-filter": f, "aria-pressed": String(self.filter === f) }, t.filters[f]);
      b.addEventListener("click", function () { self.filter = f; self.render(); });
      bar.appendChild(b);
    });
    var input = this.el("input", { type: "search", "class": "hg-l-search", "data-hg-l-search": "", placeholder: t.search, "aria-label": t.search });
    input.value = this.q;
    input.addEventListener("input", function () { self.q = input.value; self.render(); });
    bar.appendChild(input);
    root.appendChild(bar);
    if (this.confirm) root.appendChild(this.confirmEl());
    var shown = this.shown();
    if (!shown.length) root.appendChild(this.el("p", { "class": "hg-l-note" }, t.empty));
    var ul = this.el("ul", { "class": "hg-l-list" });
    shown.forEach(function (x) { ul.appendChild(self.rowEl(x)); });
    root.appendChild(ul);
    if (this.selected.length && !this.confirm) {
      var sticky = this.el("div", { "class": "hg-l-sticky" });
      sticky.appendChild(this.el("span", { "class": "hg-l-sub" }, fill(t.left, { n: this.left() - this.selected.length })));
      sticky.appendChild(this.btn("data-hg-l-bulk", fill(t.bulk, { n: this.selected.length }), function () { self.confirm = self.selected.slice(); self.render(); self.win.scrollTo && self.win.scrollTo(0, 0); }, "hg-l-danger"));
      root.appendChild(sticky);
    }
    if (focused) {
      var again = root.querySelector("[data-hg-l-search]");
      again.focus();
      try { again.setSelectionRange(pos, pos); } catch (e) { /* type=search */ }
    }
  };

  App.prototype.start = function () { this.render(); this.load(); };

  App.prototype.stop = function () { this.timers.forEach(function (x) { clearTimeout(x); }); };

  // On /my-library: our page as the first child of Seerr's <main> (its 404 hidden by CSS), put back when React re-renders; gone on any other route (as collections.js)
  var app = null;
  function sync(win) {
    var doc = win.document, main = doc.querySelector("main");
    var here = win.location.pathname.replace(/\/+$/, "") === ROUTE;
    if (!here) {
      if (doc.documentElement.getAttribute("data-hg-early") === "my-library") doc.documentElement.removeAttribute("data-hg-early");
      if (app) { app.stop(); if (app.root.parentNode) app.root.parentNode.removeChild(app.root); app = null; }
      var m = doc.querySelector("main[data-hg-library]");
      if (m) m.removeAttribute("data-hg-library");
      return;
    }
    if (!main) return;
    if (!doc.getElementById("hg-library-style")) {
      var st = doc.createElement("style");
      st.id = "hg-library-style";
      st.textContent = CSS;
      (doc.head || doc.documentElement).appendChild(st);
    }
    if (!main.hasAttribute("data-hg-library")) main.setAttribute("data-hg-library", "");
    if (!app) {
      var root = doc.createElement("div");
      root.id = "hg-library";
      app = new App(win, root);
      app.start();
    }
    if (app.root.parentNode !== main || main.firstChild !== app.root) main.insertBefore(app.root, main.firstChild);
    if (doc.documentElement.getAttribute("data-hg-early") === "my-library") doc.documentElement.removeAttribute("data-hg-early");
    var title = app.t.title + " - Seerr";
    if (doc.title !== title) doc.title = title;
  }

  if (typeof window === "undefined") return;
  window.hgLibrary = { TEXT: TEXT };
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
      sync(window);
      new MutationObserver(function () {
        try { sync(window); } catch (e) { /* never break Seerr */ }
      }).observe(document.documentElement, { childList: true, subtree: true });
    };
    if (hydrated(document)) go();
    else {
      var frame = window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function (f) { setTimeout(f, 16); };
      var until = Date.now() + 10000;
      var wait = function () {
        try { if (hydrated(document) || Date.now() > until) go(); else frame(wait); } catch (e) { go(); }
      };
      frame(wait);
    }
  } catch (e) { /* never break Seerr */ }
})();
