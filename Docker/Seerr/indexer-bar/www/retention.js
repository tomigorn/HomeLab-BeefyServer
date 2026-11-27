// Storage retention (README "Storage retention", "My library"): "♾️ Keep forever" box in the request dialog (= my own ⭐ fan-favourite mark, preset from the title's state, stored after the request was created), a fact row with the date pill and the mark button on the title page, a pill in the Status field of request rows and cards, and the would-delete list for managers on the Collections page. Data from /_download-now/retention/; anything unexpected = Seerr unchanged.
(function () {
  "use strict";
  var TITLE = /^(Request (Movie|Series|Collection)( in 4K)?|(Film|Serie|Sammlung)( in 4K)? anfragen)$/;
  var BOX = "hg-keep-forever";
  var API = "/_download-now/retention/";
  var TITLE_PAGE = /^\/(movie|tv)\/(\d+)(\/|$)/;
  var MEDIA = /^\/(movie|tv)\/(\d+)(\/|$|\?)/;
  var SEERR_TITLE = /^\/api\/v1\/(movie|tv)\/(\d+)$/;
  var STATUS = /^Status$/;
  var CARD_TTL = 60000;
  var MONTHS = { en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], de: ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."] };
  var TEXT = {
    en: {
      box: "♾️ Keep forever", loading: "…",
      boxKeep: "Kept on the server for good ({why}).",
      boxMine: "Your ⭐ fan-favourite mark keeps it for good. Untick to remove your mark.",
      boxTemp: "Leaves the server 3 months after the download or the last play (watching resets the clock). Tick to mark it ⭐ fan favourite and keep it for good ({left} of {max} marks left this week).",
      boxTheirs: "Kept for good: {why}. Tick to add your own mark ({left} of {max} marks left this week).",
      boxFull: "You used your {max} fan-favourite marks this week.",
      boxUnknown: "Tick to mark it ⭐ fan favourite and keep it for good. Otherwise it leaves 3 months after the download or the last play, unless it is old, niche or top-rated.",
      fact: "On the server", kept: "♾️ Kept forever", leaves: "🗑 Leaves on {d}", afterPlay: "(3 months after the last play)", afterImport: "(3 months after the download)", played: "Last played:", soon: "Leaving soon · {d}", short: "🗑 {d}", soonShort: "Leaving soon",
      keep: "⭐ Fan favourite", unkeep: "Remove my mark", busy: "…", failed: "❌ Not saved", fanPill: "⭐ Fan favourite",
      why: { old: "released 12 or more years ago", niche: "niche", goated: "top-rated on IMDb", fan: "fan favourite", protected: "the permanent test film" }, fanOf: "fan favourite of {names}", you: "you",
      hKept: "♾️ Kept forever", hTemp: "🗑 Temporary", imported: "Downloaded:", ssd: "SSD until:", hdd: "HDD from:", del: "Deleted on:", because: "Kept because:",
      postponed: "Postponed:", pReason: { "in-progress": "someone is still watching it (at most 30 days)", collection: "it is in a household collection (at most 30 days)" },
      space: "Earlier because the HDD is over 85 % full.", grace: "14 days of grace after cleanup was switched on.",
      observe: "Cleanup is not switched on yet: nothing is deleted until the owner switches it on.",
      listTitle: "Leaving the server", listObserve: "Observe mode: nothing is deleted until cleanup is switched on. These are the dates it would use.",
      listOn: "Cleanup is on. Files are deleted in the night window; the titles stay requestable.",
      counts: "{n} titles · {k} kept forever ({o} old, {ni} niche, {g} top-rated, {ov} fan favourites) · {t} temporary · {s} leaving within 7 days",
      none: "Nothing scheduled to leave.", more: "Show all {n}", promote: "Would move back to the SSD (played from the HDD, observe only):", season: "Season",
      tier: { ssd: "SSD", hdd: "HDD", split: "SSD + HDD" }, whyList: { due: "", space: "HDD full", postponed: "postponed", grace: "grace" }
    },
    de: {
      box: "♾️ Für immer behalten", loading: "…",
      boxKeep: "Bleibt für immer auf dem Server ({why}).",
      boxMine: "Deine ⭐ Fan-Liebling-Markierung behält es für immer. Abwählen, um deine Markierung zu entfernen.",
      boxTemp: "Geht 3 Monate nach dem Download oder dem letzten Abspielen vom Server (Schauen setzt die Frist zurück). Anwählen, um es als ⭐ Fan-Liebling für immer zu behalten (noch {left} von {max} Markierungen diese Woche).",
      boxTheirs: "Bleibt für immer: {why}. Anwählen, um es auch zu markieren (noch {left} von {max} Markierungen diese Woche).",
      boxFull: "Du hast diese Woche alle {max} Fan-Liebling-Markierungen gebraucht.",
      boxUnknown: "Anwählen, um es als ⭐ Fan-Liebling für immer zu behalten. Sonst geht es 3 Monate nach dem Download oder dem letzten Abspielen, ausser es ist alt, eine Nische oder top bewertet.",
      fact: "Auf dem Server", kept: "♾️ Für immer behalten", leaves: "🗑 Geht am {d}", afterPlay: "(3 Monate nach dem letzten Abspielen)", afterImport: "(3 Monate nach dem Download)", played: "Zuletzt geschaut:", soon: "Geht bald · {d}", short: "🗑 {d}", soonShort: "Geht bald",
      keep: "⭐ Fan-Liebling", unkeep: "Meine Markierung entfernen", busy: "…", failed: "❌ Nicht gespeichert", fanPill: "⭐ Fan-Liebling",
      why: { old: "vor 12 oder mehr Jahren erschienen", niche: "Nische", goated: "top bewertet auf IMDb", fan: "Fan-Liebling", protected: "der feste Testfilm" }, fanOf: "Fan-Liebling von {names}", you: "dir",
      hKept: "♾️ Für immer behalten", hTemp: "🗑 Vorübergehend", imported: "Heruntergeladen:", ssd: "SSD bis:", hdd: "HDD ab:", del: "Gelöscht am:", because: "Behalten, weil:",
      postponed: "Verschoben:", pReason: { "in-progress": "jemand schaut es noch (höchstens 30 Tage)", collection: "es ist in einer Sammlung des Haushalts (höchstens 30 Tage)" },
      space: "Früher, weil die HDD über 85 % voll ist.", grace: "14 Tage Schonfrist nach dem Einschalten der Aufräumung.",
      observe: "Die Aufräumung ist noch nicht eingeschaltet: nichts wird gelöscht, bis der Besitzer sie einschaltet.",
      listTitle: "Verlässt den Server", listObserve: "Beobachtungsmodus: nichts wird gelöscht, bis die Aufräumung eingeschaltet ist. Das sind die Daten, die sie nehmen würde.",
      listOn: "Die Aufräumung ist eingeschaltet. Dateien werden im Nachtfenster gelöscht; die Titel bleiben anfragbar.",
      counts: "{n} Titel · {k} für immer ({o} alt, {ni} Nische, {g} top bewertet, {ov} Fan-Lieblinge) · {t} vorübergehend · {s} gehen in 7 Tagen",
      none: "Nichts geht demnächst.", more: "Alle {n} zeigen", promote: "Käme zurück auf die SSD (von der HDD geschaut, nur beobachtet):", season: "Staffel",
      tier: { ssd: "SSD", hdd: "HDD", split: "SSD + HDD" }, whyList: { due: "", space: "HDD voll", postponed: "verschoben", grace: "Schonfrist" }
    }
  };
  var state = { titles: {}, loading: {}, cards: {}, cardAt: 0, cardLoading: false, lastApi: null, list: null, listLoading: false, listAt: 0, listAll: false };

  function fill(t, map) { return t.replace(/\{(\w+)\}/g, function (_, k) { return map[k] === undefined ? "" : String(map[k]); }); }

  function lang(doc) {
    var nl = typeof window !== "undefined" && window.hgNightLabel;
    if (nl && nl.lang) { try { return nl.lang() === "de" ? "de" : "en"; } catch (e) { /* own detection */ } }
    var tv = doc.querySelector('a[href="/discover/tv"]');
    return tv && /\bSerien\b/.test(tv.textContent) ? "de" : "en";
  }

  function day(iso, l) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    if (!m) return "";
    var year = Number(m[1]) !== new Date().getFullYear() ? " " + m[1] : "";
    return Number(m[3]) + (l === "de" ? ". " : " ") + MONTHS[l][Number(m[2]) - 1] + year;
  }

  function el(doc, tag, attrs, text) {
    var e = doc.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined) e.textContent = text;
    return e;
  }

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
      x.send(body === undefined ? undefined : JSON.stringify(body));
    });
  }

  function tip(node, content) {
    var t = typeof window !== "undefined" && window.hgTip;
    if (t && t.set) { try { t.set(node, content); } catch (e) { /* no hint */ } }
  }

  function whyText(st, l) {
    var t = TEXT[l], w = t.why;
    if (st.source === "protected") return w.protected;
    if (st.source === "fan" || (st.source !== "default" && (st.marks || []).length)) {
      return (st.marks || []).length ? fill(t.fanOf, { names: st.marks.map(function (m) { return m.mine ? t.you : m.name || "?"; }).join(", ") }) : w.fan;
    }
    return (st.reasons || []).map(function (r) { return w[r] || r; }).join(", ");
  }

  function mine(st) { return (st.marks || []).some(function (m) { return m.mine; }); }

  // ---- request dialog ----
  function dialogKey() {
    var m = TITLE_PAGE.exec(window.location.pathname);
    if (m) return m[1] + "/" + m[2];
    return state.lastApi && Date.now() - state.lastApi.at < 10000 ? state.lastApi.key : null;
  }

  function makeBox(doc, l) {
    var wrap = el(doc, "div", { id: BOX, "class": "mt-4" });
    wrap.style.cssText = "display:flex;align-items:flex-start;gap:.6rem;padding:.6rem .75rem;border:1px solid #4b5563;border-radius:.375rem;background:rgba(17,24,39,.6);color:#e5e7eb;font-size:.875rem;line-height:1.25rem;";
    var input = el(doc, "input", { type: "checkbox", id: BOX + "-input" });
    input.style.cssText = "margin-top:.15rem;width:1rem;height:1rem;flex:none;accent-color:#6366f1;";
    var label = el(doc, "label", { "for": BOX + "-input" });
    label.style.cssText = "cursor:pointer;";
    var hint = el(doc, "div", { "data-hg-keep-hint": "" }, TEXT[l].loading);
    hint.style.cssText = "color:#9ca3af;font-size:.8rem;min-height:1.25rem;";
    label.appendChild(el(doc, "strong", {}, TEXT[l].box));
    label.appendChild(hint);
    wrap.appendChild(input);
    wrap.appendChild(label);
    return wrap;
  }

  function fillBox(box, key, st, l) {
    var input = box.querySelector("input"), hint = box.querySelector("[data-hg-keep-hint]");
    if (!st) {
      box.__hg = { key: null, initial: false };
      hint.textContent = TEXT[l].boxUnknown;
      return;
    }
    var t = TEXT[l], own = mine(st), q = st.markQuota || {}, left = Math.max(0, (q.max || 0) - (q.used || 0));
    var locked = st.source === "protected" || (st.source === "default" && !!st.keep);
    input.checked = locked || own;
    input.disabled = locked || !(own ? st.canUnmark : st.canMark);
    box.__hg = { key: key, initial: input.checked };
    if (locked) hint.textContent = fill(t.boxKeep, { why: whyText(st, l) });
    else if (own) hint.textContent = t.boxMine;
    else if (!st.canMark && q.max && left === 0) hint.textContent = fill(t.boxFull, { max: q.max });
    else if ((st.marks || []).length) hint.textContent = fill(t.boxTheirs, { why: whyText(st, l), left: left, max: q.max || 5 });
    else hint.textContent = fill(t.boxTemp, { left: left, max: q.max || 5 });
  }

  function attachDialogs(doc) {
    var dialogs = doc.querySelectorAll('[role="dialog"]');
    for (var i = 0; i < dialogs.length; i++) {
      var d = dialogs[i], box = d.querySelector("#" + BOX), can = d.querySelector("#hg-can-wait");
      if (box) {
        if (can && can.nextElementSibling !== box) can.after(box);
        continue;
      }
      var title = d.querySelector('[data-testid="modal-title"]'), ok = d.querySelector('[data-testid="modal-ok-button"]');
      if (!title || !ok || !TITLE.test((title.textContent || "").trim())) continue;
      var row = ok.parentElement;
      if (!row || !row.parentElement) continue;
      var l = lang(doc), key = dialogKey();
      box = makeBox(doc, l);
      if (can) can.after(box); else row.parentElement.insertBefore(box, row);
      if (!key) { fillBox(box, null, null, l); continue; }
      (function (box, key, l) {
        box.__hg = { key: key, initial: null };
        title_state(key, true).then(function (st) { fillBox(box, key, st, l); }, function () { fillBox(box, null, null, l); });
      })(box, key, l);
    }
  }

  // what to store once the request exists: decided when it is sent (the dialog closes right after)
  function pendingFor(body) {
    var box = typeof document !== "undefined" && document.querySelector("#" + BOX);
    if (!box || !body || (body.mediaType !== "movie" && body.mediaType !== "tv")) return null;
    var input = box.querySelector("input"), id = Number(body.mediaId), want = !!input.checked, info = box.__hg || {};
    if (!(id > 0) || input.disabled) return null;
    var key = body.mediaType + "/" + id;
    if (info.key) {
      if (info.key !== key || info.initial === null || want === info.initial) return null;
    } else if (!want) {
      return null;
    }
    return { type: body.mediaType, tmdbId: id, keep: want };
  }

  function hookXHR(XHR) {
    if (!XHR || !XHR.prototype || XHR.prototype.__hgKeep) return;
    var open = XHR.prototype.open, send = XHR.prototype.send;
    XHR.prototype.__hgKeep = true;
    XHR.prototype.open = function (method, url) {
      var path = String(url).replace(/^https?:\/\/[^/]+/, "").split("?")[0], m = SEERR_TITLE.exec(path);
      this.__hgKeepReq = [String(method).toUpperCase(), path];
      if (m && this.__hgKeepReq[0] === "GET") state.lastApi = { key: m[1] + "/" + m[2], at: Date.now() };
      return open.apply(this, arguments);
    };
    XHR.prototype.send = function (body) {
      var r = this.__hgKeepReq || [], self = this;
      if (!this.__hgSkip && r[0] === "POST" && r[1] === "/api/v1/request" && typeof body === "string" && this.addEventListener) {
        var pending = null;
        try { pending = pendingFor(JSON.parse(body)); } catch (e) { pending = null; }
        if (pending) {
          this.addEventListener("load", function () {
            if (self.status >= 200 && self.status < 300) request("POST", API + "keep", pending).then(function (st) { state.titles[pending.type + "/" + pending.tmdbId] = { at: Date.now(), st: st }; run(); }, function () { /* the default stays */ });
          });
        }
      }
      return send.apply(this, arguments);
    };
  }

  // ---- title page ----
  // dflt: the request dialog asks for the category default of a title not in the library yet (a Radarr/Sonarr lookup); the title page does not need it
  function title_state(key, dflt) {
    var c = state.titles[key], slot = key + (dflt ? "|d" : "");
    if (c && c.st && Date.now() - c.at < 30000 && (!dflt || c.st.known || c.d)) return Promise.resolve(c.st);
    if (state.loading[slot]) return state.loading[slot];
    var kt = key.split("/");
    state.loading[slot] = request("GET", API + "title?type=" + kt[0] + "&tmdb=" + kt[1] + (dflt ? "&default=1" : "")).then(function (st) {
      state.titles[key] = { at: Date.now(), st: st, d: !!dflt };
      state.loading[slot] = null;
      run();
      return st;
    }, function (e) {
      state.titles[key] = { at: Date.now(), st: null };
      state.loading[slot] = null;
      throw e;
    });
    return state.loading[slot];
  }

  function hintRows(st, l, observe) {
    var t = TEXT[l], rows = [];
    if (st.imported) rows.push([t.imported, day(st.imported, l)]);
    if (st.lastPlayed) rows.push([t.played, day(st.lastPlayed, l)]);
    if (st.ssdUntil) rows.push([t.ssd, day(st.ssdUntil, l)]);
    if (st.hddFrom) rows.push([t.hdd, day(st.hddFrom, l)]);
    if (st.keep) rows.push([t.because, whyText(st, l)]);
    else if (st.deleteOn) rows.push([t.del, day(st.deleteOn, l)]);
    if (!st.keep && st.postponed) rows.push([t.postponed, t.pReason[st.postponed] || st.postponed]);
    var out = { title: st.keep ? t.hKept : t.hTemp, rows: rows };
    var notes = [];
    if (!st.keep && st.why === "space") notes.push(t.space);
    if (!st.keep && st.why === "grace") notes.push(t.grace);
    if (observe) notes.push(t.observe);
    if (notes.length) out.note = notes.join(" ");
    return out;
  }

  function hintContent(st, l, observe) {
    var h = hintRows(st, l, observe), lines = [];
    h.rows.forEach(function (r) { lines.push(r[0] + " " + r[1]); });
    return { title: h.title, rows: lines, sections: h.note ? [h.note] : [] };
  }

  function pillFor(doc, st, l, attr) {
    var t = TEXT[l], kind = st.state === "keep" ? "keep" : st.soon ? "soon" : "temp", fan = kind === "keep" && st.source === "fan";
    var long = kind === "keep" ? (fan ? t.fanPill : t.kept) : kind === "soon" ? fill(t.soon, { d: day(st.deleteOn, l) }) : fill(t.leaves, { d: day(st.deleteOn, l) });
    var short = kind === "keep" ? (fan ? "⭐" : "♾️") : kind === "soon" ? t.soonShort : fill(t.short, { d: day(st.deleteOn, l) });
    var p = el(doc, "span", { "class": "hg-keep-pill hg-keep-" + kind, tabindex: "0" });
    p.setAttribute(attr, kind);
    if (attr === "data-hg-keep-pill" && kind !== "keep" && st.anchorBy) long += " " + (st.anchorBy === "play" ? t.afterPlay : t.afterImport);
    p.appendChild(el(doc, "span", { "class": "hg-keep-long" }, long));
    if (attr === "data-hg-keep-card") p.appendChild(el(doc, "span", { "class": "hg-keep-short", "aria-hidden": "true" }, short));
    return p;
  }

  function factAnchor(panel) {
    var a = panel.querySelector(":scope > [data-hg-origin-fact]") || panel.querySelector(":scope > [data-hg-size-fact]");
    if (a) return a;
    var facts = panel.querySelectorAll(":scope > .media-fact:not([data-hg-keep-fact])");
    for (var i = 0; i < facts.length; i++) if (facts[i].firstElementChild && STATUS.test(facts[i].firstElementChild.textContent.trim())) return facts[i];
    return null;
  }

  function toggle(doc, key, want, btn) {
    var l = lang(doc), kt = key.split("/");
    btn.disabled = true;
    btn.textContent = TEXT[l].busy;
    request("POST", API + "keep", { type: kt[0], tmdbId: Number(kt[1]), keep: want }).then(function (next) {
      state.titles[key] = { at: Date.now(), st: next };
      var row = doc.querySelector("[data-hg-keep-fact]");
      if (row) row.parentNode.removeChild(row);
      run();
    }, function () {
      btn.disabled = false;
      btn.textContent = TEXT[l].failed;
    });
  }

  function titleFact(doc) {
    var m = TITLE_PAGE.exec(window.location.pathname), panel = m && doc.querySelector(".media-facts");
    var row = doc.querySelector("[data-hg-keep-fact]");
    if (!m) { if (row) row.parentNode.removeChild(row); return; }
    var key = m[1] + "/" + m[2], c = state.titles[key];
    if (!c || Date.now() - c.at > 30000) title_state(key).catch(function () { /* no row */ });
    var st = c && c.st;
    if (!panel || !st || !st.known) { if (row) row.parentNode.removeChild(row); return; }
    var anchor = factAnchor(panel);
    if (!anchor) { if (row) row.parentNode.removeChild(row); return; }
    var sig = [key, st.state, st.deleteOn, st.soon, st.canMark, st.canUnmark, st.source, JSON.stringify(st.marks || [])].join("|");
    if (row && row.getAttribute("data-hg-keep-fact") !== sig) { row.parentNode.removeChild(row); row = null; }
    if (!row) {
      var l = lang(doc);
      row = el(doc, "div", { "class": "media-fact", "data-hg-keep-fact": sig });
      row.appendChild(el(doc, "span", {}, TEXT[l].fact));
      var value = el(doc, "span", { "class": "media-fact-value hg-keep-value" });
      var pill = pillFor(doc, st, l, "data-hg-keep-pill");
      value.appendChild(pill);
      tip(pill, hintContent(st, l, !st.deleteEnabled));
      var own = mine(st), want = own ? false : st.canMark ? true : null;
      if (want !== null && (want || st.canUnmark)) {
        var btn = el(doc, "button", { type: "button", "class": "hg-keep-toggle", "data-hg-keep-toggle": "" }, want ? TEXT[l].keep : TEXT[l].unkeep);
        btn.addEventListener("click", function (e) { e.preventDefault(); toggle(doc, key, want, btn); });
        value.appendChild(btn);
      }
      row.appendChild(value);
    }
    if (anchor.nextElementSibling !== row) anchor.after(row);
  }

  // ---- Delete early (README "Delete early"): a fact row on the title page, state and actions from /_download-now/delete-early/ ----
  var DAPI = "/_download-now/delete-early/";
  var DTEXT = {
    en: {
      fact: "Delete early", start: "🗑 Delete early", whole: "Whole series", season: "Season {n}", film: "this film", series: "the whole series", seasonOf: "season {n}",
      ask: "Delete {what} now?", askNote: "The files stay 7 days in the recycle bin and can be restored; the title stays requestable. {left} of {max} deletions left this week.",
      planNow: "Goes to the recycle bin at once.", planAsk: "Becomes a request first: {list}", planReason: { fan: "fan favourite", watching: "watching it" },
      confirm: "🗑 Delete", cancel: "Cancel", busy: "…", queued: "⏳ Working on it…",
      wants: "{who} wants to delete {what}: {reason}. Answer until {d}.", reason: { keep: "it is marked ♾️ Keep forever", fan: "you marked it ⭐ fan favourite", watching: "you have started watching it" },
      approve: "✅ Approve", deny: "✖ Deny", waiting: "⏳ Waiting for {names} to OK it until {d}.",
      bin: "🗑 In the recycle bin until {d} (deleted by {who}{scope}).", restore: "↩ Restore", restored: "↩ Restored{scope}.",
      denied: "✖ Not deleted{scope}: {names} said no.", expired: "✖ Not deleted{scope}: no answer within 7 days.", refusedRow: "✖ Not deleted{scope}: {why}.",
      scope: ", season {n}",
      errors: { quota: "at most 10 deletions a week", already: "already being deleted", protected: "this title is protected", kept: "kept forever automatically", library: "not on the server", group: "only requesters may delete", forbidden: "not allowed", closed: "already decided", season: "unknown season", failed: "something went wrong", "not in the bin": "no longer in the recycle bin" }
    },
    de: {
      fact: "Früher löschen", start: "🗑 Früher löschen", whole: "Ganze Serie", season: "Staffel {n}", film: "diesen Film", series: "die ganze Serie", seasonOf: "Staffel {n}",
      ask: "{What} jetzt löschen?", askNote: "Die Dateien bleiben 7 Tage im Papierkorb und lassen sich wiederherstellen; der Titel bleibt anfragbar. Noch {left} von {max} Löschungen diese Woche.",
      planNow: "Kommt sofort in den Papierkorb.", planAsk: "Wird zuerst eine Anfrage: {list}", planReason: { fan: "Fan-Liebling", watching: "schaut es" },
      confirm: "🗑 Löschen", cancel: "Abbrechen", busy: "…", queued: "⏳ Wird erledigt…",
      wants: "{who} möchte {what} löschen: {reason}. Antwort bis {d}.", reason: { keep: "als ♾️ Für immer behalten markiert", fan: "du hast es als ⭐ Fan-Liebling markiert", watching: "du hast angefangen, es zu schauen" },
      approve: "✅ Zustimmen", deny: "✖ Ablehnen", waiting: "⏳ Wartet bis {d} auf das OK von {names}.",
      bin: "🗑 Im Papierkorb bis {d} (gelöscht von {who}{scope}).", restore: "↩ Wiederherstellen", restored: "↩ Wiederhergestellt{scope}.",
      denied: "✖ Nicht gelöscht{scope}: {names} hat abgelehnt.", expired: "✖ Nicht gelöscht{scope}: keine Antwort innert 7 Tagen.", refusedRow: "✖ Nicht gelöscht{scope}: {why}.",
      scope: ", Staffel {n}",
      errors: { quota: "höchstens 10 Löschungen pro Woche", already: "wird schon gelöscht", protected: "dieser Titel ist geschützt", kept: "automatisch für immer behalten", library: "nicht auf dem Server", group: "nur Anfragende dürfen löschen", forbidden: "nicht erlaubt", closed: "schon entschieden", season: "unbekannte Staffel", failed: "etwas ist schiefgegangen", "not in the bin": "nicht mehr im Papierkorb" }
    }
  };
  var del = { titles: {}, loading: {}, confirm: null, scope: {}, error: {}, timer: null };

  function dRequest(method, url, body) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      x.__hgSkip = true;
      x.open(method, url);
      x.withCredentials = true;
      if (body !== undefined) x.setRequestHeader("Content-Type", "application/json");
      x.onload = function () {
        var data = null;
        try { data = JSON.parse(x.responseText); } catch (e) { data = null; }
        if (x.status >= 200 && x.status < 300) resolve(data || {}); else reject((data && data.error) || "failed");
      };
      x.onerror = function () { reject("failed"); };
      x.send(body === undefined ? undefined : JSON.stringify(body));
    });
  }

  function dState(key, force) {
    var c = del.titles[key];
    var busy = c && c.st && ((c.st.queued || []).length || (c.st.items || []).some(function (i) { return i.state === "approved"; }));
    var ttl = busy ? 3000 : 30000;
    if (!force && c && Date.now() - c.at < ttl) return;
    if (del.loading[key]) return;
    var kt = key.split("/");
    del.loading[key] = true;
    dRequest("GET", DAPI + "state?type=" + kt[0] + "&tmdb=" + kt[1]).then(function (st) { del.titles[key] = { at: Date.now(), st: st }; }, function () { del.titles[key] = { at: Date.now(), st: null }; })
      .then(function () { del.loading[key] = false; run(); if (busy || (del.titles[key].st && (del.titles[key].st.queued || []).length)) schedule(); });
  }

  function schedule() {
    if (del.timer || typeof setTimeout === "undefined") return;
    del.timer = setTimeout(function () { del.timer = null; run(); }, 3000);
  }

  function dAct(key, op, body) {
    del.error[key] = null;
    dRequest("POST", DAPI + op, body).then(function () { del.confirm = null; }, function (err) { del.confirm = null; del.error[key] = String(err || "failed"); })
      .then(function () { var row = document.querySelector("[data-hg-delete-fact]"); if (row) row.parentNode.removeChild(row); dState(key, true); run(); });
  }

  function what(t, kind, season) {
    if (kind === "movie") return t.film;
    return season === null || season === undefined ? t.series : fill(t.seasonOf, { n: season });
  }

  function dButton(doc, attr, text, fn) {
    var b = el(doc, "button", { type: "button", "class": "hg-keep-toggle" }, text);
    b.setAttribute(attr, "");
    b.addEventListener("click", function (e) { e.preventDefault(); fn(b); });
    return b;
  }

  function itemLine(doc, key, kind, it, l) {
    var t = DTEXT[l], line = el(doc, "div", { "class": "hg-delete-line" });
    var scope = it.season === null || it.season === undefined ? "" : fill(t.scope, { n: it.season });
    var open = (it.asks || []).filter(function (a) { return a.answer === null; });
    var names = (open.length ? open : it.asks || []).reduce(function (a, x) { return a.concat(x.names || []); }, []).join(", ");
    if (it.state === "pending" && it.approver) {
      var reason = (open[0] || {}).reason;
      line.appendChild(el(doc, "span", {}, fill(t.wants, { who: it.byName || "", what: what(t, kind, it.season), reason: t.reason[reason] || "", d: day((it.expires || "").slice(0, 10), l) })));
      line.appendChild(dButton(doc, "data-hg-delete-approve", t.approve, function (b) { b.disabled = true; dAct(key, "answer", { id: it.id, approve: true }); }));
      line.appendChild(dButton(doc, "data-hg-delete-deny", t.deny, function (b) { b.disabled = true; dAct(key, "answer", { id: it.id, approve: false }); }));
    } else if (it.state === "pending") {
      line.appendChild(el(doc, "span", {}, fill(t.waiting, { names: names, d: day((it.expires || "").slice(0, 10), l) })));
    } else if (it.state === "approved") {
      line.appendChild(el(doc, "span", {}, t.queued));
    } else if (it.state === "deleted") {
      line.appendChild(el(doc, "span", {}, fill(t.bin, { d: day((it.binUntil || "").slice(0, 10), l), who: it.byName || "", scope: scope })));
      if (it.canRestore) line.appendChild(dButton(doc, "data-hg-delete-restore", t.restore, function (b) { b.disabled = true; dAct(key, "restore", { id: it.id }); }));
    } else if (it.state === "restored") {
      line.appendChild(el(doc, "span", {}, fill(t.restored, { scope: scope })));
    } else if (it.mine && it.state === "denied") {
      line.appendChild(el(doc, "span", {}, fill(t.denied, { scope: scope, names: names })));
    } else if (it.mine && it.state === "expired") {
      line.appendChild(el(doc, "span", {}, fill(t.expired, { scope: scope })));
    } else if (it.mine && (it.state === "refused" || it.state === "failed")) {
      line.appendChild(el(doc, "span", {}, fill(t.refusedRow, { scope: scope, why: t.errors[it.why] || t.errors.failed })));
    } else {
      return null;
    }
    return line;
  }

  function deleteFact(doc) {
    var m = TITLE_PAGE.exec(window.location.pathname), panel = m && doc.querySelector(".media-facts");
    var row = doc.querySelector("[data-hg-delete-fact]");
    if (!m) { if (row) row.parentNode.removeChild(row); return; }
    var key = m[1] + "/" + m[2], kind = m[1];
    dState(key);
    var c = del.titles[key], st = c && c.st;
    var anchor = panel && (panel.querySelector(":scope > [data-hg-keep-fact]") || factAnchor(panel));
    var l = lang(doc), t = DTEXT[l];
    var lines = [];
    var items = st ? (st.items || []) : [];
    // only the newest closed item per season: an older restore or refusal is not repeated once the title moved on
    items = items.filter(function (it, i) {
      if (it.state === "pending" || it.state === "approved" || it.state === "deleted") return true;
      return !items.some(function (o, j) { return j !== i && o.season === it.season && String(o.at || "") > String(it.at || ""); });
    });
    items.forEach(function (it) { var x = itemLine(doc, key, kind, it, l); if (x) lines.push(x); });
    var active = st && ((st.queued || []).length || (st.items || []).some(function (it) { return it.state === "pending" || it.state === "approved" || it.state === "deleted"; }));
    var wholeBusy = st && (st.items || []).some(function (it) { return (it.state === "pending" || it.state === "approved" || it.state === "deleted") && (it.season === null || it.season === undefined); });
    var canStart = st && st.allowed && !wholeBusy && !(kind === "movie" && active);
    if (!panel || !anchor || !st || (!canStart && !lines.length && !(st.queued || []).length)) { if (row) row.parentNode.removeChild(row); return; }
    var sig = JSON.stringify([key, l, st, del.confirm === key, del.error[key] || null, del.scope[key] || ""]);
    if (row && row.getAttribute("data-hg-delete-fact") !== sig) { row.parentNode.removeChild(row); row = null; }
    if (!row) {
      row = el(doc, "div", { "class": "media-fact", "data-hg-delete-fact": sig });
      row.appendChild(el(doc, "span", {}, t.fact));
      var value = el(doc, "div", { "class": "media-fact-value hg-delete-value" });
      lines.forEach(function (x) { value.appendChild(x); });
      if ((st.queued || []).length) value.appendChild(el(doc, "div", { "class": "hg-delete-line" }, t.queued));
      if (del.error[key]) value.appendChild(el(doc, "div", { "class": "hg-delete-line hg-delete-error" }, "✖ " + (t.errors[del.error[key]] || t.errors.failed)));
      if (canStart && del.confirm === key) {
        var sv = del.scope[key] || "", season = sv === "" ? null : Number(sv);
        var box = el(doc, "div", { "class": "hg-delete-confirm", role: "alertdialog" });
        var w = what(t, kind, season);
        box.appendChild(el(doc, "strong", {}, fill(t.ask, { what: w, What: w.charAt(0).toUpperCase() + w.slice(1) })));
        var q = st.quota || {};
        box.appendChild(el(doc, "div", { "class": "hg-delete-note" }, fill(t.askNote, { left: Math.max(0, (q.max || 10) - (q.used || 0)), max: q.max || 10 })));
        var plan = st.plan || [];
        box.appendChild(el(doc, "div", { "class": "hg-delete-note", "data-hg-delete-plan": "" }, plan.length ? fill(t.planAsk, { list: plan.map(function (a) {
          return (a.names || []).map(function (n) { return n || "?"; }).join(", ") + " (" + (t.planReason[a.reason] || a.reason) + ")";
        }).join(", ") }) : t.planNow));
        var bar = el(doc, "div", { "class": "hg-delete-buttons" });
        bar.appendChild(dButton(doc, "data-hg-delete-confirm", t.confirm, function (b) { b.disabled = true; b.textContent = t.busy; dAct(key, "delete", { type: kind, tmdbId: Number(m[2]), season: season }); }));
        bar.appendChild(dButton(doc, "data-hg-delete-cancel", t.cancel, function () { del.confirm = null; run(); }));
        box.appendChild(bar);
        value.appendChild(box);
      } else if (canStart) {
        var start = el(doc, "div", { "class": "hg-delete-line" });
        if (kind === "tv" && (st.seasons || []).length) {
          var sel = el(doc, "select", { "class": "hg-delete-scope", "data-hg-delete-scope": "", "aria-label": t.fact });
          sel.appendChild(el(doc, "option", { value: "" }, t.whole));
          st.seasons.forEach(function (n) { sel.appendChild(el(doc, "option", { value: String(n) }, fill(t.season, { n: n }))); });
          sel.value = del.scope[key] || "";
          start.appendChild(sel);
        }
        start.appendChild(dButton(doc, "data-hg-delete-start", t.start, function () {
          var s = row.querySelector("select[data-hg-delete-scope]");
          del.scope[key] = s ? s.value : "";
          del.confirm = key;
          del.error[key] = null;
          run();
        }));
        value.appendChild(start);
      }
      row.appendChild(value);
    }
    if (anchor.nextElementSibling !== row) anchor.after(row);
  }

  // ---- request rows and cards ----
  function cardFields(doc) {
    var out = [], names = doc.querySelectorAll(".card-field-name");
    for (var i = 0; i < names.length; i++) {
      if (!STATUS.test(names[i].textContent.trim())) continue;
      var field = names[i].parentElement, box = field, key = null;
      for (var up = 0; box && up < 6 && !key; up++, box = box.parentElement) {
        var links = box.querySelectorAll("a[href]");
        for (var j = 0; j < links.length && !key; j++) {
          var m = MEDIA.exec(links[j].getAttribute("href") || "");
          if (m) key = m[1] + "/" + m[2];
        }
      }
      if (key) out.push({ field: field, key: key });
    }
    return out;
  }

  function cards(doc) {
    var found = cardFields(doc);
    if (!found.length) return;
    var missing = found.map(function (f) { return f.key; }).filter(function (k, i, a) { return a.indexOf(k) === i && !(k in state.cards); });
    if ((missing.length || Date.now() - state.cardAt > CARD_TTL) && !state.cardLoading) {
      var keys = found.map(function (f) { return f.key; }).filter(function (k, i, a) { return a.indexOf(k) === i; }).slice(0, 100);
      state.cardLoading = true;
      request("GET", API + "titles?keys=" + encodeURIComponent(keys.join(","))).then(function (d) {
        keys.forEach(function (k) { state.cards[k] = (d.titles || {})[k] || null; });
        state.cardObserve = !d.deleteEnabled;
      }, function () {
        keys.forEach(function (k) { if (!(k in state.cards)) state.cards[k] = null; });
      }).then(function () { state.cardAt = Date.now(); state.cardLoading = false; run(); });
    }
    var l = lang(doc);
    found.forEach(function (f) {
      var st = state.cards[f.key], p = f.field.querySelector(":scope > [data-hg-keep-card]");
      var sig = st ? [st.state, st.deleteOn, st.soon, l].join("|") : "";
      if (p && p.getAttribute("data-hg-keep-sig") !== sig) { p.parentNode.removeChild(p); p = null; }
      if (!st || p) return;
      p = pillFor(doc, st, l, "data-hg-keep-card");
      p.setAttribute("data-hg-keep-sig", sig);
      tip(p, hintContent(st, l, state.cardObserve));
      f.field.appendChild(p);
    });
  }

  // ---- would-delete list (managers) on the Collections page ----
  function human(n) {
    return n >= 1099511627776 ? (n / 1099511627776).toFixed(1) + " TB" : (n / 1073741824).toFixed(1) + " GB";
  }

  function listSection(doc, data, l) {
    var t = TEXT[l], sec = el(doc, "section", { "data-hg-keep-list": "", "class": "hg-keep-list" });
    sec.appendChild(el(doc, "h2", {}, t.listTitle));
    sec.appendChild(el(doc, "p", { "class": "hg-keep-note" }, data.deleteEnabled ? t.listOn : t.listObserve));
    var c = data.counts || {};
    sec.appendChild(el(doc, "p", { "class": "hg-keep-counts" }, fill(t.counts, { n: c.titles || 0, k: c.keep || 0, o: c.old || 0, ni: c.niche || 0, g: c.goated || 0, ov: c.fan || 0, t: c.temporary || 0, s: c.soon || 0 })));
    var items = data.items || [];
    if (!items.length) sec.appendChild(el(doc, "p", { "class": "hg-keep-note" }, t.none));
    var ul = el(doc, "ul", { "class": "hg-keep-items" });
    items.slice(0, state.listAll ? items.length : 30).forEach(function (it) {
      var li = el(doc, "li", { "class": it.soon ? "hg-keep-soon-row" : "" });
      li.appendChild(el(doc, "span", { "class": "hg-keep-date" }, day(it.deleteOn, l)));
      var kt = String(it.key || "").split("/");
      var a = el(doc, "a", { href: "/" + (kt[0] === "tv" ? "tv" : "movie") + "/" + (Number(kt[1]) || 0) }, (it.t || "") + (it.y ? " (" + it.y + ")" : ""));
      li.appendChild(a);
      var why = t.whyList[it.why] || "";
      li.appendChild(el(doc, "span", { "class": "hg-keep-meta" }, [human(it.size || 0), t.tier[it.tier] || "", why].filter(Boolean).join(" · ")));
      ul.appendChild(li);
    });
    sec.appendChild(ul);
    if (!state.listAll && items.length > 30) {
      var more = el(doc, "button", { type: "button", "class": "hg-keep-toggle" }, fill(t.more, { n: items.length }));
      more.addEventListener("click", function () { state.listAll = true; var s = doc.querySelector("[data-hg-keep-list]"); if (s) s.parentNode.removeChild(s); run(); });
      sec.appendChild(more);
    }
    if ((data.promote || []).length) {
      sec.appendChild(el(doc, "p", { "class": "hg-keep-note" }, t.promote));
      var pl = el(doc, "ul", { "class": "hg-keep-items" });
      data.promote.forEach(function (p) {
        pl.appendChild(el(doc, "li", {}, (p.title || "") + " · " + (p.seasons || []).map(function (s) { return t.season + " " + s; }).join(", ")));
      });
      sec.appendChild(pl);
    }
    return sec;
  }

  function adminList(doc) {
    var root = doc.getElementById("hg-collections");
    if (!/^\/collections\/?$/.test(window.location.pathname) || !root) return;
    if (!state.listLoading && (!state.list || Date.now() - state.listAt > 60000)) {
      state.listLoading = true;
      request("GET", API + "list").then(function (d) { state.list = d; }, function () { state.list = { denied: true }; }).then(function () {
        state.listAt = Date.now();
        state.listLoading = false;
        var s = doc.querySelector("[data-hg-keep-list]");
        if (s) s.parentNode.removeChild(s);
        run();
      });
    }
    if (!state.list || state.list.denied || root.querySelector(":scope > [data-hg-keep-list]")) return;
    root.appendChild(listSection(doc, state.list, lang(doc)));
  }

  function style(doc) {
    if (doc.getElementById("hg-keep-style")) return;
    var s = doc.createElement("style");
    s.id = "hg-keep-style";
    s.textContent = ".hg-keep-pill{display:inline-flex;flex-shrink:0;align-items:center;margin-left:.375rem;padding:0 .5rem;border-radius:9999px;font-size:.75rem;line-height:1.25rem;white-space:nowrap;cursor:default;border:1px solid}" +
      ".hg-keep-keep{background:rgba(79,70,229,.25);color:#c7d2fe;border-color:rgba(129,140,248,.5)}.hg-keep-temp{background:rgba(75,85,99,.45);color:#e5e7eb;border-color:rgba(156,163,175,.5)}" +
      ".hg-keep-soon{background:rgba(234,88,12,.3);color:#fed7aa;border-color:rgba(251,146,60,.7)}.hg-keep-short{display:none}" +
      "@media (max-width:639px){[data-hg-keep-card] .hg-keep-long{display:none}[data-hg-keep-card] .hg-keep-short{display:inline}}" +
      "[data-hg-keep-fact]>span:first-child{white-space:nowrap;flex-shrink:0}.hg-keep-value{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:.375rem}.hg-keep-value{min-width:0}.hg-keep-value .hg-keep-pill{margin-left:0;min-width:0;flex:0 1 auto;white-space:normal;text-align:right;line-height:1.1rem;padding:.1rem .5rem;border-radius:.75rem}" +
      ".hg-keep-toggle{padding:0 .6rem;border-radius:.375rem;font-size:.75rem;line-height:1.5rem;background:#374151;color:#e5e7eb;border:1px solid #6b7280;cursor:pointer;white-space:nowrap}.hg-keep-toggle:hover{background:#4b5563}" +
      ".hg-keep-list{margin:2rem 0 1rem;padding:1rem;border:1px solid #374151;border-radius:.5rem;background:rgba(31,41,55,.6)}.hg-keep-list h2{font-size:1.25rem;font-weight:700;margin:0 0 .5rem}" +
      ".hg-keep-note,.hg-keep-counts{color:#9ca3af;font-size:.875rem;margin:.25rem 0 .75rem}.hg-keep-items{list-style:none;margin:0 0 .75rem;padding:0}" +
      ".hg-keep-items li{display:flex;flex-wrap:wrap;gap:.25rem .75rem;align-items:baseline;padding:.375rem 0;border-top:1px solid #374151;font-size:.875rem;min-width:0}" +
      ".hg-keep-items a{color:#e5e7eb;text-decoration:none;font-weight:600;overflow-wrap:anywhere}.hg-keep-items a:hover{text-decoration:underline}" +
      "[data-hg-delete-fact]>span:first-child{white-space:nowrap;flex-shrink:0}.hg-delete-value{display:flex;flex-direction:column;align-items:flex-end;gap:.375rem;min-width:0;text-align:right}" +
      ".hg-delete-line{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:.375rem;font-size:.875rem}.hg-delete-error{color:#fca5a5}" +
      ".hg-delete-scope{background:#374151;color:#e5e7eb;border:1px solid #6b7280;border-radius:.375rem;font-size:.75rem;line-height:1.25rem;padding:0 1.75rem 0 .5rem}" +
      ".hg-delete-confirm{display:flex;flex-direction:column;gap:.375rem;padding:.6rem .75rem;border:1px solid rgba(248,113,113,.6);border-radius:.375rem;background:rgba(127,29,29,.35);text-align:left;max-width:28rem}" +
      ".hg-delete-note{color:#d1d5db;font-size:.8rem}.hg-delete-buttons{display:flex;gap:.5rem;justify-content:flex-end}" +
      ".hg-keep-date{min-width:6.5rem;color:#d1d5db}.hg-keep-soon-row .hg-keep-date{color:#fdba74;font-weight:600}.hg-keep-meta{color:#9ca3af;font-size:.8rem}";
    (doc.head || doc.documentElement).appendChild(s);
  }

  function run() {
    if (typeof document === "undefined") return;
    try { style(document); attachDialogs(document); titleFact(document); deleteFact(document); cards(document); adminList(document); } catch (e) { /* never break Seerr */ }
  }

  function ours(node) {
    if (node.nodeType !== 1) return true;
    if (node.id === BOX) return true;
    var attrs = ["data-hg-keep-fact", "data-hg-keep-card", "data-hg-keep-list", "data-hg-keep-pill", "data-hg-delete-fact"];
    for (var i = 0; i < attrs.length; i++) if (node.hasAttribute(attrs[i])) return true;
    return false;
  }

  var api = { day: day, pendingFor: pendingFor, run: run, state: state };
  if (typeof window === "undefined") return;
  window.hgRetention = api;
  try {
    hookXHR(window.XMLHttpRequest);
    if (!window.MutationObserver || !document.body) return;
    run();
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var added = records[i].addedNodes;
        for (var n = 0; n < added.length; n++) if (!ours(added[n])) { run(); return; }
      }
    }).observe(document.body, { childList: true, subtree: true });
  } catch (e) { /* never break Seerr */ }
})();
