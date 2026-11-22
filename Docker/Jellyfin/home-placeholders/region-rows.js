(function () {
  // Region rows (scripts/region-rows.py, Jellyfin README "Region rows"): window.HssRegionRows = [{id, name: {en, de}, items: [ids]}], set by the same injector entry.
  // Drawn by empty-sections.js (window.HssHomeExt) after Hidden Gems with the user's own session, so a title the user may not see is left out.
  var AFTER = "HiddenGems";
  var BEFORE = "DiscoverMovieGenres";
  var TTL_MS = 600000;
  var ID = /^HssRegion[A-Za-z]+$/;
  var HINT = { en: "Nothing here yet.", de: "Noch nichts hier." };

  function langOf(lang) {
    return String(lang || "").toLowerCase().indexOf("de") === 0 ? "de" : "en";
  }

  function rowsOf(win) {
    var rows = win && win.HssRegionRows;
    return Array.isArray(rows) ? rows.filter(function (r) { return r && ID.test(r.id) && Array.isArray(r.items) && r.name && typeof r.name.en === "string"; }) : [];
  }

  function layout(enabled, ids) {
    var out = enabled.filter(function (id) { return ids.indexOf(id) === -1; });
    if (!ids.length) return out;
    var at = out.indexOf(AFTER);
    if (at !== -1) at += 1;
    else at = out.indexOf(BEFORE) !== -1 ? out.indexOf(BEFORE) : out.length;
    return out.slice(0, at).concat(ids, out.slice(at));
  }

  function ordered(items, ids) {
    var by = {};
    for (var i = 0; i < items.length; i++) if (items[i] && items[i].Id) by[items[i].Id] = items[i];
    return ids.map(function (id) { return by[id]; }).filter(Boolean);
  }

  function loadRow(api, userId, row) {
    if (!row.items.length) return Promise.resolve([]);
    return api.getJSON(api.getUrl("Users/" + userId + "/Items", { Ids: row.items.join(","), Fields: "PrimaryImageAspectRatio", EnableImageTypes: "Primary,Backdrop,Thumb", ImageTypeLimit: 1 }))
      .then(function (r) { return ordered((r && r.Items) || [], row.items); });
  }

  function create(win) {
    var rows = rowsOf(win);
    var cache = null;
    var loading = null;

    function nameOf(id, lang) {
      var r = rows.filter(function (x) { return x.id === id; })[0];
      return r ? r.name[langOf(lang)] || r.name.en : id;
    }

    function load(api, userId, tools) {
      loading = Promise.all(rows.map(function (r) { return loadRow(api, userId, r).catch(function () { return []; }); })).then(function (lists) {
        var items = {};
        rows.forEach(function (r, i) { items[r.id] = lists[i]; });
        cache = { user: userId, at: Date.now(), items: items };
      }).then(function () {
        loading = null;
        tools.repaint();
      });
    }

    return {
      ids: rows.map(function (r) { return r.id; }),
      layout: function (enabled) { return layout(enabled, rows.map(function (r) { return r.id; })); },
      name: function (id, lang) { return nameOf(id, lang); },
      hint: function (id, lang) { return HINT[langOf(lang)]; },
      loading: function () { return !cache; },
      sync: function (container, api, lang, tools) {
        var userId = api.getCurrentUserId();
        if (!loading && (!cache || cache.user !== userId || Date.now() - cache.at > TTL_MS)) load(api, userId, tools);
        if (!cache || cache.user !== userId) return;
        rows.forEach(function (r) { tools.syncRecent(container, r.id, cache.items[r.id] || [], nameOf(r.id, lang), api); });
      }
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { layout: layout, rowsOf: rowsOf, ordered: ordered, loadRow: loadRow, create: create };
  } else {
    (window.HssHomeExt = window.HssHomeExt || []).push(create(window));
  }
})();
