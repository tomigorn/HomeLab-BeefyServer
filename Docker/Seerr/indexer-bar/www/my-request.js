// "My request" card on Seerr's title page (README "My request"): what a request set and changing or deleting it. The backend (/_download-now/request) decides who sees what against Seerr and holds every key; a 403 or anything unexpected = no card.
(function () {
  "use strict";
  var API = "/_download-now/request";
  var BOX = "hg-my-request";
  var GERMAN = /^(Verarbeiten|Verfügbar|Teilweise verfügbar|Angefragt|Ausstehend|Gelöscht)$/;
  var TEXT = {
    en: {
      mine: "My request", all: "Requests", requested: "Requested", by: "by", pending: "Pending approval", approved: "Approved",
      profile: "Quality", tags: "Audio & subtitles", add: "Add…", audio: "Audio", subs: "Subtitles", remove: "Remove",
      canWait: "Can wait — download at night (cheaper power)", seasons: "Seasons", root: "Folder", auto: { "old-rare": "Old & rare (automatic)" },
      save: "Save", saving: "Saving …", saved: "✅ Saved — Radarr/Sonarr search again within 2 minutes", nochange: "Nothing changed",
      del: "Delete request", deleted: "🗑️ Request deleted", error: "❌ Failed — try again, or ask an admin",
      shared: "Others requested this title too: ask an admin to change it.",
      confirm: "Delete your request for “{t}”?\n\n",
      clear: "This removes the title too: its Radarr/Sonarr entry, downloaded files (7 days in the recycle bin), running downloads and the Jellyfin entry. Nobody else requested it.",
      only: "Only your request goes; the title stays (someone else requested it too, or it was in the library before).",
      locale: "en-GB",
    },
    de: {
      mine: "Meine Anfrage", all: "Anfragen", requested: "Angefragt", by: "von", pending: "Wartet auf Freigabe", approved: "Freigegeben",
      profile: "Qualität", tags: "Ton & Untertitel", add: "Hinzufügen…", audio: "Ton", subs: "Untertitel", remove: "Entfernen",
      canWait: "Kann warten — nachts herunterladen (günstigerer Strom)", seasons: "Staffeln", root: "Ordner", auto: { "old-rare": "Alt & selten (automatisch)" },
      save: "Speichern", saving: "Speichert …", saved: "✅ Gespeichert — Radarr/Sonarr suchen innert 2 Minuten neu", nochange: "Nichts geändert",
      del: "Anfrage löschen", deleted: "🗑️ Anfrage gelöscht", error: "❌ Fehlgeschlagen — nochmals versuchen oder einen Admin fragen",
      shared: "Andere haben diesen Titel auch angefragt: für Änderungen einen Admin fragen.",
      confirm: "Deine Anfrage für «{t}» löschen?\n\n",
      clear: "Damit geht auch der Titel: Eintrag in Radarr/Sonarr, heruntergeladene Dateien (7 Tage im Papierkorb), laufende Downloads und der Jellyfin-Eintrag. Niemand sonst hat ihn angefragt.",
      only: "Nur deine Anfrage geht; der Titel bleibt (jemand anderes hat ihn auch angefragt, oder er war schon vorher in der Bibliothek).",
      locale: "de-CH",
    },
  };
  var CSS = "#hg-my-request{margin:1rem 0;max-width:48rem;border:1px solid #374151;border-radius:.75rem;background:rgba(31,41,55,.85);color:#e5e7eb;font-size:.875rem}" +
    "#hg-my-request>summary{cursor:pointer;padding:.75rem 1rem;font-weight:600;list-style-position:inside}" +
    "#hg-my-request [data-request]{padding:.25rem 1rem 1rem;border-top:1px solid #374151}" +
    "#hg-my-request .hg-meta{color:#9ca3af;margin:.5rem 0}#hg-my-request label{display:block;margin:.6rem 0 .2rem;color:#9ca3af;font-size:.8rem}" +
    "#hg-my-request select{width:100%;max-width:28rem;padding:.4rem;border-radius:.375rem;background:#111827;color:#f3f4f6;border:1px solid #4b5563}" +
    "#hg-my-request .hg-chips{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center}" +
    "#hg-my-request [data-tag],#hg-my-request .hg-auto{display:inline-flex;align-items:center;gap:.3rem;padding:.15rem .6rem;border-radius:9999px;background:#374151;border:1px solid #4b5563}" +
    "#hg-my-request [data-tag] button{background:none;border:0;color:#fca5a5;cursor:pointer;font-size:1rem;line-height:1;padding:0 .1rem}" +
    "#hg-my-request .hg-check{display:flex;gap:.5rem;align-items:center;margin-top:.8rem;color:#e5e7eb;font-size:.875rem}" +
    "#hg-my-request .hg-actions{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1rem}" +
    "#hg-my-request .hg-actions button{padding:.45rem 1rem;border-radius:.375rem;font-weight:600;border:1px solid #6366f1;background:#4f46e5;color:#fff;cursor:pointer}" +
    "#hg-my-request .hg-actions button[data-delete]{background:transparent;border-color:#ef4444;color:#fca5a5}" +
    "#hg-my-request .hg-actions button:disabled{opacity:.5;cursor:default}#hg-my-request [data-hg-icon]{cursor:help;margin-left:.2rem}" +
    "#hg-my-request [role=status]{display:block;margin-top:.6rem}";
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

  function el(doc, tag, attrs, text) {
    var e = doc.createElement(tag);
    for (var k in attrs || {}) if (Object.prototype.hasOwnProperty.call(attrs, k)) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // the title's 🌙🧭📅 from night-label.js, one merged hint on the whole group (hover; tap on touch)
  function icons(doc, key) {
    var nl = typeof window !== "undefined" && window.hgNightLabel;
    var list = [];
    try { list = nl && nl.icons ? nl.icons(key) || [] : []; } catch (e) { list = []; }
    if (!list.length) return [];
    var hint = nl.hintText ? nl.hintText(list) : list.map(function (x) { return x[1]; }).join(" · ");
    var s = el(doc, "span", { "data-hg-icon": "", role: "img", title: hint, "aria-label": hint }, list.map(function (x) { return x[0]; }).join(""));
    if (nl.hint) nl.hint(s, hint);
    return [s];
  }

  function day(iso, t) {
    var d = new Date(iso || "");
    if (isNaN(d.getTime())) return "";
    try { return d.toLocaleDateString(t.locale, { day: "numeric", month: "short", year: "numeric" }); } catch (e) { return iso; }
  }

  function tagText(tag, t) { return (tag.kind === "audio" ? t.audio : t.subs) + ": " + tag.name; }

  // one request: fields, Save, Delete; edits stay local until Save
  function item(doc, t, title, state, r, ctx) {
    var box = el(doc, "div", { "data-request": String(r.id) });
    var draft = { profileId: r.profile.id, tags: r.tags.map(function (x) { return x.label; }), canWait: r.canWait };
    var meta = el(doc, "div", { class: "hg-meta" }, t.requested + " " + day(r.createdAt, t) + (r.by ? " " + t.by + " " + r.by : "") + " · " + (r.status === 1 ? t.pending : t.approved) + (r.is4k ? " · 4K" : ""));
    if (r.seasons && r.seasons.length) meta.textContent += " · " + t.seasons + " " + r.seasons.join(", ");
    box.appendChild(meta);

    box.appendChild(el(doc, "label", {}, t.profile));
    var prof = el(doc, "select", { name: "profile" });
    state.profiles.forEach(function (p) { var o = el(doc, "option", { value: String(p.id) }, p.name); prof.appendChild(o); });
    prof.value = String(r.profile.id);
    prof.disabled = !r.editable;
    box.appendChild(prof);

    box.appendChild(el(doc, "label", {}, t.tags));
    var chips = el(doc, "div", { class: "hg-chips" });
    box.appendChild(chips);
    var add = el(doc, "select", { name: "add" });
    function drawTags() {
      chips.textContent = "";
      draft.tags.forEach(function (label) {
        var tag = state.tags.filter(function (x) { return x.label === label; })[0] || { label: label, kind: label.split("-")[0], name: label };
        var c = el(doc, "span", { "data-tag": label }, tagText(tag, t));
        if (r.editable) {
          var x = el(doc, "button", { type: "button", "aria-label": t.remove + " " + tagText(tag, t), title: t.remove }, "×");
          x.addEventListener("click", function () { draft.tags = draft.tags.filter(function (l) { return l !== label; }); drawTags(); dirty(); });
          c.appendChild(x);
        }
        chips.appendChild(c);
      });
      (r.auto || []).forEach(function (a) { chips.appendChild(el(doc, "span", { class: "hg-auto" }, t.auto[a] || a)); });
      add.textContent = "";
      add.appendChild(el(doc, "option", { value: "" }, t.add));
      state.tags.forEach(function (x) { if (draft.tags.indexOf(x.label) < 0) add.appendChild(el(doc, "option", { value: x.label }, tagText(x, t))); });
      add.value = "";
      if (r.editable) chips.appendChild(add);
    }
    add.addEventListener("change", function () { if (add.value) { draft.tags = draft.tags.concat([add.value]).sort(); drawTags(); dirty(); } });
    drawTags();

    var row = el(doc, "label", { class: "hg-check" });
    var cw = el(doc, "input", { type: "checkbox", name: "canWait" });
    cw.checked = r.canWait;
    cw.disabled = !r.editable;
    cw.addEventListener("change", function () { draft.canWait = cw.checked; dirty(); });
    row.appendChild(cw);
    row.appendChild(doc.createTextNode(t.canWait));
    box.appendChild(row);
    if (r.rootFolder) box.appendChild(el(doc, "div", { class: "hg-meta" }, t.root + ": " + r.rootFolder));
    if (!r.editable) box.appendChild(el(doc, "div", { class: "hg-meta" }, t.shared));

    var actions = el(doc, "div", { class: "hg-actions" });
    var status = el(doc, "span", { role: "status" });
    var save = null;
    if (r.editable) {
      save = el(doc, "button", { type: "button", "data-save": "" }, t.save);
      save.disabled = true;
      save.addEventListener("click", function () {
        var body = { type: title.type, tmdbId: title.tmdbId, action: "update", requestId: r.id };
        var before = r.tags.map(function (x) { return x.label; }).sort().join(",");
        if (draft.profileId !== r.profile.id) body.profileId = draft.profileId;
        if (draft.tags.slice().sort().join(",") !== before) body.tags = draft.tags.slice().sort();
        if (draft.canWait !== r.canWait) body.canWait = draft.canWait;
        if (Object.keys(body).length === 4) { status.textContent = t.nochange; return; }
        save.disabled = true;
        save.textContent = t.saving;
        request("POST", API, body).then(function (res) {
          var fresh = item(doc, t, title, state, res.request || r, ctx);
          box.parentNode.replaceChild(fresh, box);
          fresh.querySelector("[role=status]").textContent = t.saved;
          ctx.panel.open = true;
        }, function () { save.textContent = t.save; save.disabled = false; status.textContent = t.error; });
      });
      actions.appendChild(save);
    }
    prof.addEventListener("change", function () { draft.profileId = +prof.value; dirty(); });
    function dirty() {
      if (!save) return;
      var changed = draft.profileId !== r.profile.id || draft.canWait !== r.canWait || draft.tags.slice().sort().join(",") !== r.tags.map(function (x) { return x.label; }).sort().join(",");
      save.disabled = !changed;
    }
    var del = el(doc, "button", { type: "button", "data-delete": "" }, t.del);
    del.addEventListener("click", function () {
      var name = ctx.name;
      var text = t.confirm.replace("{t}", name) + (r.deleteEffect === "clear" ? t.clear : t.only);
      if (!(typeof window !== "undefined" ? window.confirm(text) : false)) return;
      del.disabled = true;
      request("POST", API, { type: title.type, tmdbId: title.tmdbId, action: "delete", requestId: r.id }).then(function () {
        box.textContent = "";
        box.appendChild(el(doc, "span", { role: "status" }, t.deleted));
      }, function () { del.disabled = false; status.textContent = t.error; });
    });
    actions.appendChild(del);
    box.appendChild(actions);
    box.appendChild(status);
    return box;
  }

  function render(doc, t, title, state) {
    var d = el(doc, "details", { id: BOX });
    var mine = state.requests.length === 1 && !state.manager;
    var sum = el(doc, "summary", {}, (mine ? t.mine : t.all + " (" + state.requests.length + ")") + (state.requests.length === 1 ? " · " + state.requests[0].profile.name : ""));
    icons(doc, title.key).forEach(function (i) { sum.appendChild(i); });
    d.appendChild(sum);
    var h1 = doc.querySelector(".media-header h1");
    var ctx = { panel: d, name: h1 ? (h1.firstChild && h1.firstChild.nodeType === 3 ? h1.firstChild.textContent : h1.textContent).trim() : "" };
    state.requests.forEach(function (r) { d.appendChild(item(doc, t, title, state, r, ctx)); });
    return d;
  }

  function style(doc) {
    if (doc.getElementById("hg-my-request-style")) return;
    var s = el(doc, "style", { id: "hg-my-request-style" }, CSS);
    (doc.head || doc.documentElement).appendChild(s);
  }

  function attach(doc, loc) {
    var title = titleOf(loc);
    if (current && (!title || current.key !== title.key)) {
      if (current.el && current.el.parentNode) current.el.parentNode.removeChild(current.el);
      current = null;
    }
    if (!title) return;
    var header = doc.querySelector(".media-header");
    if (!header) return;
    if (current) {
      if (current.el && current.el.previousElementSibling !== header) header.parentNode.insertBefore(current.el, header.nextSibling);
      return;
    }
    var mine = current = { key: title.key, el: null };
    if (!me) me = request("GET", "/api/v1/auth/me").catch(function () { me = null; return null; });
    me.then(function (user) {
      if (!user || typeof user.id !== "number") return null;
      return request("GET", API + "?type=" + title.type + "&tmdb=" + title.tmdbId).then(function (st) { return { st: st, user: user }; });
    }).then(function (r) {
      if (!r || current !== mine || !r.st || !Array.isArray(r.st.requests) || !r.st.requests.length) return;
      var header = doc.querySelector(".media-header");
      if (!header || doc.getElementById(BOX)) return;
      style(doc);
      mine.el = render(doc, TEXT[langOf(doc, r.user)], title, r.st);
      header.parentNode.insertBefore(mine.el, header.nextSibling);
    }).catch(function () { /* no card */ });
  }

  var api = { titleOf: titleOf, attach: attach };
  if (typeof window === "undefined") return;
  window.hgMyRequest = api;
  try {
    if (!window.MutationObserver || !document.body) return;
    attach(document, window.location);
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var t = records[i].target;
        if (!(t.closest && t.closest("#" + BOX))) { try { attach(document, window.location); } catch (e) { /* never break Seerr */ } return; }
      }
    }).observe(document.body, { childList: true, subtree: true });
  } catch (e) { /* never break Seerr */ }
})();
