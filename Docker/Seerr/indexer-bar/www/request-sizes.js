// Request sizes (README "Request sizes"): a "Size" line under Profile on every Requests row (bytes on disk from request-state.json "sizes", by Seerr request id) and our own "Largest / Smallest first" control beside Seerr's sort. Sorting moves rows with CSS order only (no DOM moves) and turns 100 rows per page on, so every request is sorted. Missing or unmatched data = Seerr unchanged.
(function () {
  "use strict";
  var DATA_URL = "/_indexer-bar/request-state.json";
  var LIST = /^(https?:\/\/[^/]+)?\/api\/v1\/request\?(.*&)?take=/;
  var MEDIA = /^\/(movie|tv)\/(\d+)/;
  var REQUEST_PAGE = /\/requests\/?$/;
  var TTL = 2 * 60 * 1000;
  var KEEP_LISTS = 12;
  var MODE_KEY = "hg-size-sort";
  var PREV_KEY = "hg-size-sort-page-size";
  var ALL_ROWS = "100";
  var TEXT = {
    en: { size: "Size", of: "{a} of {b} episodes", queued: "in queue", off: "Off", desc: "Largest", asc: "Smallest", aria: "Sort by size", page: "Sorted within this page ({a} of {b})", locale: "en-GB" },
    de: { size: "Grösse", of: "{a} von {b} Episoden", queued: "in der Warteschlange", off: "Aus", desc: "Grösste", asc: "Kleinste", aria: "Nach Grösse sortieren", page: "Innerhalb dieser Seite sortiert ({a} von {b})", locale: "de-CH" },
  };
  var RESTORE_WAIT = 2000;
  var state = { sizes: null, at: 0, loading: null, lists: [], mode: "", fetched: {}, restore: null };

  function store(key, value) {
    try {
      if (value) window.localStorage.setItem(key, value); else window.localStorage.removeItem(key);
    } catch (e) { /* no storage: per page only */ }
  }

  function stored(key) {
    try { return window.localStorage.getItem(key) || ""; } catch (e) { return ""; }
  }

  function fill(t, a, b) { return t.replace("{a}", a).replace("{b}", b); }

  function bytes(n, l) {
    var unit = n >= 1e12 ? ["TB", 1e12, 2] : n >= 1e9 ? ["GB", 1e9, 1] : ["MB", 1e6, 0];
    var v = n / unit[1];
    try { return v.toLocaleString(TEXT[l].locale, { minimumFractionDigits: unit[2], maximumFractionDigits: unit[2] }) + " " + unit[0]; } catch (e) { return v.toFixed(unit[2]) + " " + unit[0]; }
  }

  function sizeText(entry, l) {
    if (!entry || typeof entry !== "object") return "—";
    if (entry.size > 0) return bytes(entry.size, l) + (entry.of > entry.files ? " · " + fill(TEXT[l].of, entry.files, entry.of) : "");
    if (entry.queued > 0) return "≈ " + bytes(entry.queued, l) + " · " + TEXT[l].queued;
    return "—";
  }

  function sortValue(entry) {
    if (!entry || typeof entry !== "object") return null;
    return entry.size > 0 ? entry.size : entry.queued > 0 ? entry.queued : null;
  }

  // indexes of rows in display order; titles without a size last, Seerr's order among equals
  function ranking(values, mode) {
    var idx = values.map(function (v, i) { return i; });
    if (mode !== "desc" && mode !== "asc") return idx;
    return idx.sort(function (a, b) {
      var x = values[a], y = values[b];
      if (x === null || y === null) return x === null && y === null ? a - b : x === null ? 1 : -1;
      return x === y ? a - b : mode === "desc" ? y - x : x - y;
    });
  }

  function lang(doc) {
    try { if (window.hgNightLabel && window.hgNightLabel.lang) return window.hgNightLabel.lang() === "de" ? "de" : "en"; } catch (e) { /* own probe */ }
    var tv = doc.querySelector('a[href="/discover/tv"]');
    return tv && /\bSerien\b/.test(tv.textContent) ? "de" : "en";
  }

  function remember(url, json) {
    if (!json || !Array.isArray(json.results)) return;
    var rows = json.results.map(function (r) {
      var media = (r && r.media) || {};
      return { id: r && r.id != null ? String(r.id) : null, key: r && r.type && media.tmdbId ? r.type + "/" + media.tmdbId : null };
    });
    var total = json.pageInfo && typeof json.pageInfo.results === "number" ? json.pageInfo.results : rows.length;
    state.lists = state.lists.filter(function (x) { return x.url !== url; });
    state.lists.unshift({ url: url, rows: rows, total: total });
    if (state.lists.length > KEEP_LISTS) state.lists.length = KEEP_LISTS;
  }

  function getJSON(url) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      x.__hgSkip = true;
      x.open("GET", url);
      x.withCredentials = true;
      x.onload = function () {
        try { x.status === 200 ? resolve(JSON.parse(x.responseText)) : reject(new Error(String(x.status))); } catch (e) { reject(e); }
      };
      x.onerror = function () { reject(new Error("network")); };
      x.send();
    });
  }

  function load(force) {
    if (state.loading || (!force && state.at && Date.now() - state.at < TTL)) return state.loading;
    state.loading = getJSON(DATA_URL).then(function (d) {
      state.sizes = d && d.sizes && typeof d.sizes === "object" && !Array.isArray(d.sizes) ? d.sizes : null;
    }, function () { state.sizes = null; }).then(function () {
      state.at = Date.now();
      state.loading = null;
      run();
    });
    return state.loading;
  }

  // the list call Seerr made before this script hooked XHR: read it again once
  function refetch() {
    var perf = window.performance, entries = perf && perf.getEntriesByType ? perf.getEntriesByType("resource") : [];
    for (var i = entries.length - 1; i >= 0; i--) {
      var url = entries[i].name;
      if (!LIST.test(url) || state.fetched[url]) continue;
      state.fetched[url] = true;
      getJSON(url).then(function (d) { remember(url, d); run(); }, function () { /* Seerr unchanged */ });
      return;
    }
  }

  function findList(doc) {
    var sort = doc.getElementById("sort");
    if (!sort || !doc.getElementById("mediaType") || !doc.getElementById("pageSize")) return null;
    var header = sort.closest("div.mb-4"), box = header && header.parentElement;
    if (!box || !sort.parentElement || !sort.parentElement.parentElement) return null;
    var rows = [];
    for (var c = box.firstElementChild; c; c = c.nextElementSibling) if (c.classList.contains("py-2") && c.firstElementChild) rows.push(c);
    return { sort: sort, group: sort.parentElement, controls: sort.parentElement.parentElement, box: box, rows: rows, pageSize: doc.getElementById("pageSize") };
  }

  function rowKey(row) {
    var links = row.querySelectorAll("a[href]");
    for (var i = 0; i < links.length; i++) {
      var m = MEDIA.exec(links[i].getAttribute("href") || "");
      if (m) return m[1] + "/" + m[2];
    }
    return null;
  }

  // the captured list Seerr is showing: same length, every loaded row's title in the same place; nothing loaded yet = the newest list of that length
  function match(rows) {
    if (!rows.length) return null;
    var keys = rows.map(rowKey), none = keys.every(function (k) { return k === null; });
    for (var i = 0; i < state.lists.length; i++) {
      var list = state.lists[i];
      if (list.rows.length !== rows.length) continue;
      if (none) return list;
      if (keys.every(function (k, n) { return k === null || k === list.rows[n].key; })) return list;
    }
    return null;
  }

  function sizeLine(doc, row, text, l) {
    var line = row.querySelector("[data-hg-size]");
    var fields = row.querySelectorAll(".card-field:not([data-hg-size])");
    var last = fields.length ? fields[fields.length - 1] : null;
    if (!last) return;
    var col = last.parentElement;
    if (!line) {
      line = doc.createElement("div");
      line.className = "card-field";
      line.setAttribute("data-hg-size", "");
      var name = doc.createElement("span"), value = doc.createElement("span");
      name.className = "card-field-name";
      value.className = "flex truncate text-sm text-gray-300";
      line.appendChild(name);
      line.appendChild(value);
    }
    if (line.firstChild.textContent !== TEXT[l].size) line.firstChild.textContent = TEXT[l].size;
    if (line.lastChild.textContent !== text) line.lastChild.textContent = text;
    if (line.parentElement !== col || line.nextElementSibling) col.appendChild(line);
  }

  // one more line: every row of the list (placeholders too) one line taller on wide screens, so nothing jumps when a row loads
  function tall(row) {
    var card = row.firstElementChild;
    if (card && !card.hasAttribute("data-hg-size-tall")) card.setAttribute("data-hg-size-tall", "");
  }

  function dropLines(rows) {
    rows.forEach(function (row) {
      var line = row.querySelector("[data-hg-size]");
      if (line) line.parentNode.removeChild(line);
      var card = row.firstElementChild;
      if (card) card.removeAttribute("data-hg-size-tall");
    });
  }

  function unsort(list) {
    if (list.box.hasAttribute("data-hg-size-sorted")) list.box.removeAttribute("data-hg-size-sorted");
    var marked = list.box.querySelectorAll(":scope > [data-hg-size-row]");
    for (var i = 0; i < marked.length; i++) {
      marked[i].removeAttribute("data-hg-size-row");
      marked[i].style.removeProperty("order");
    }
    if (list.group.hasAttribute("data-hg-size-overridden")) list.group.removeAttribute("data-hg-size-overridden");
  }

  function sortRows(list, values) {
    var order = ranking(values, state.mode);
    if (list.box.getAttribute("data-hg-size-sorted") !== state.mode) list.box.setAttribute("data-hg-size-sorted", state.mode);
    if (!list.group.hasAttribute("data-hg-size-overridden")) list.group.setAttribute("data-hg-size-overridden", "");
    order.forEach(function (rowIndex, place) {
      var row = list.rows[rowIndex];
      if (!row.hasAttribute("data-hg-size-row")) row.setAttribute("data-hg-size-row", "");
      if (row.style.order !== String(place + 1)) row.style.order = String(place + 1);
    });
  }

  function setSelect(sel, value) {
    sel.value = value;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // every request on one page while sorting by size (Seerr's own page size, 100 = its largest)
  function showAll(list, current) {
    if (!current || current.total <= list.rows.length || Number(list.pageSize.value) >= Number(ALL_ROWS)) return;
    store(PREV_KEY, list.pageSize.value);
    setSelect(list.pageSize, ALL_ROWS);
  }

  function restorePageSize(doc) {
    var prev = stored(PREV_KEY), sel = doc.getElementById("pageSize");
    store(PREV_KEY, "");
    if (prev && sel && sel.value === ALL_ROWS && prev !== ALL_ROWS) setSelect(sel, prev);
  }

  // after a Seerr sort: Seerr first loads its new list (a page size change in the same moment gets lost), then the old page size
  function restoreLater(doc) {
    if (state.restore) clearTimeout(state.restore);
    state.restore = setTimeout(function () { state.restore = null; restorePageSize(doc); }, RESTORE_WAIT);
  }

  function listLoaded() {
    if (!state.restore) return;
    clearTimeout(state.restore);
    state.restore = setTimeout(function () { state.restore = null; restorePageSize(document); }, 0);
  }

  function setMode(doc, mode, byUser) {
    if (mode === state.mode) return;
    state.mode = mode;
    store(MODE_KEY, mode);
    var list = findList(doc);
    if (!mode && byUser) restorePageSize(doc);
    else if (!mode) restoreLater(doc);
    else if (byUser && list) showAll(list, match(list.rows));
    run();
  }

  function control(doc, list, l) {
    var box = list.controls.querySelector(":scope > [data-hg-size-sort]");
    if (!box) {
      box = doc.createElement("div");
      box.className = "mb-2 flex flex-grow sm:mb-0 lg:flex-grow-0";
      box.setAttribute("data-hg-size-sort", "");
      var icon = doc.createElement("span");
      icon.className = "inline-flex cursor-default items-center rounded-l-md border border-r-0 border-gray-500 bg-gray-800 px-3 text-sm font-semibold text-gray-100";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "GB";
      var sel = doc.createElement("select");
      sel.className = "rounded-r-only";
      ["", "desc", "asc"].forEach(function (v) {
        var o = doc.createElement("option");
        o.value = v;
        sel.appendChild(o);
      });
      sel.addEventListener("change", function () { setMode(doc, sel.value, true); });
      box.appendChild(icon);
      box.appendChild(sel);
    }
    if (box.previousElementSibling !== list.group) list.group.after(box);
    if (!list.controls.hasAttribute("data-hg-size-controls")) list.controls.setAttribute("data-hg-size-controls", "");
    var select = box.querySelector("select"), names = [TEXT[l].off, TEXT[l].desc, TEXT[l].asc];
    for (var i = 0; i < select.options.length; i++) if (select.options[i].textContent !== names[i]) select.options[i].textContent = names[i];
    if (select.getAttribute("aria-label") !== TEXT[l].aria) select.setAttribute("aria-label", TEXT[l].aria);
    if (select.value !== state.mode) select.value = state.mode;
    return select;
  }

  function apply(doc) {
    var list = findList(doc);
    if (!list) return;
    var l = lang(doc), select = control(doc, list, l);
    if (!state.at && !state.loading) load(false);
    var current = match(list.rows);
    if (!current && list.rows.length) refetch();
    if (!current || !state.sizes) {
      unsort(list);
      if (!state.sizes) dropLines(list.rows);
      return;
    }
    var values = list.rows.map(function (row, i) {
      var entry = state.sizes[current.rows[i].id];
      tall(row);
      sizeLine(doc, row, sizeText(entry, l), l);
      return sortValue(entry);
    });
    if (state.mode) sortRows(list, values); else unsort(list);
    var hint = state.mode && current.total > list.rows.length ? fill(TEXT[l].page, list.rows.length, current.total) : "";
    if ((select.getAttribute("title") || "") !== hint) { if (hint) select.setAttribute("title", hint); else select.removeAttribute("title"); }
  }

  function style(doc) {
    if (doc.getElementById("hg-size-style")) return;
    var s = doc.createElement("style");
    s.id = "hg-size-style";
    s.textContent = "[data-hg-size-sorted]{display:flex;flex-direction:column}[data-hg-size-sorted]>*{order:-1}[data-hg-size-sorted]>[data-hg-size-row]~:not([data-hg-size-row]){order:100000}[data-hg-size-overridden]{opacity:.55}[data-hg-size-controls]{flex-wrap:wrap;row-gap:.5rem;justify-content:flex-end}[data-hg-size-sort] select{min-width:8.25rem}@media (min-width:1280px){[data-hg-size-tall]{height:8.5rem !important}}";
    (doc.head || doc.documentElement).appendChild(s);
  }

  function run() {
    if (typeof document === "undefined") return;
    try { style(document); apply(document); } catch (e) { /* never break Seerr */ }
  }

  function hookXHR(XHR) {
    if (!XHR || !XHR.prototype || XHR.prototype.__hgSizes) return;
    var open = XHR.prototype.open, send = XHR.prototype.send;
    XHR.prototype.__hgSizes = true;
    XHR.prototype.open = function (method, url) {
      this.__hgSizesUrl = String(method).toUpperCase() === "GET" && LIST.test(String(url)) ? String(url) : null;
      return open.apply(this, arguments);
    };
    XHR.prototype.send = function () {
      var self = this;
      if (!this.__hgSkip && this.__hgSizesUrl && this.addEventListener) {
        this.addEventListener("load", function () {
          try {
            if (self.status !== 200) return;
            remember(self.__hgSizesUrl, self.responseType === "json" ? self.response : JSON.parse(self.responseText));
            listLoaded();
            load(false);
            run();
          } catch (e) { /* not JSON */ }
        });
      }
      return send.apply(this, arguments);
    };
  }

  var api = { sizeText: sizeText, ranking: ranking, bytes: bytes, remember: remember, apply: apply, state: state };
  if (typeof window === "undefined") return;
  window.hgRequestSizes = api;
  try {
    hookXHR(window.XMLHttpRequest);
    state.mode = /^(desc|asc)$/.test(stored(MODE_KEY)) ? stored(MODE_KEY) : "";
    if (REQUEST_PAGE.test(window.location.pathname)) load(false);
    // a Seerr sort or direction chosen again: Seerr's order back
    document.addEventListener("change", function (e) { if (e.target && e.target.id === "sort") setMode(document, "", false); });
    document.addEventListener("click", function (e) {
      var b = e.target && e.target.closest && e.target.closest("button"), sort = document.getElementById("sort");
      if (b && sort && b.parentElement && sort.parentElement && sort.parentElement.contains(b)) setMode(document, "", false);
    });
    if (!window.MutationObserver || !document.body) return;
    run();
    // runs before the next paint: a row Seerr just rendered gets its line at once
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var added = records[i].addedNodes;
        for (var n = 0; n < added.length; n++) {
          var a = added[n];
          if (a.nodeType === 1 && !a.hasAttribute("data-hg-size") && !a.hasAttribute("data-hg-size-sort")) { run(); return; }
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  } catch (e) { /* never break Seerr */ }
})();
