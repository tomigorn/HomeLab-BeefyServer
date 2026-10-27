(function () {
  var SETTLE_MS = 1200;
  var CACHE_MS = 60000;
  var HINTS = {
    en: {
      _: "Nothing here yet.",
      ContinueWatching: "Nothing to continue — start something to see it here.",
      NextUp: "No series in progress yet.",
      WatchAgain: "Nothing here yet — finish a series or a film collection to see it here.",
      BecauseYouWatched: "Nothing here yet — watch a few films to get recommendations.",
      HiddenGems: "No highly rated unwatched titles right now.",
      FinishTheseCollections: "No film collection started yet.",
      MyList: "Nothing here yet — mark films or series with ♥ to see them here.",
      CollectionsSection: "No film collections yet.",
      Studio: "Nothing here yet.",
      Starring: "Nothing here yet — watch a few titles to get actor picks.",
      DirectedBy: "Nothing here yet — watch a few films to get director picks.",
      UpcomingShows: "No episodes airing in the next month.",
      UpcomingMovies: "No films releasing in the next months."
    },
    de: {
      _: "Noch nichts hier.",
      ContinueWatching: "Nichts fortzusetzen – starte etwas, dann erscheint es hier.",
      NextUp: "Noch keine angefangene Serie.",
      WatchAgain: "Noch nichts hier – schau eine Serie oder Filmreihe fertig, dann erscheint sie hier.",
      BecauseYouWatched: "Noch nichts hier – schau ein paar Filme, dann gibt es Empfehlungen.",
      HiddenGems: "Gerade keine hoch bewerteten, ungesehenen Titel.",
      FinishTheseCollections: "Noch keine Filmreihe angefangen.",
      MyList: "Noch nichts hier – markiere Filme oder Serien mit ♥, dann erscheinen sie hier.",
      CollectionsSection: "Noch keine Filmreihen.",
      Studio: "Noch nichts hier.",
      Starring: "Noch nichts hier – schau ein paar Titel, dann gibt es Tipps nach Schauspielern.",
      DirectedBy: "Noch nichts hier – schau ein paar Filme, dann gibt es Tipps nach Regie.",
      UpcomingShows: "Im nächsten Monat laufen keine neuen Folgen.",
      UpcomingMovies: "In den nächsten Monaten erscheinen keine Filme."
    }
  };

  function langOf(lang) {
    return String(lang || "").toLowerCase().indexOf("de") === 0 ? "de" : "en";
  }

  function hintFor(id, lang) {
    var t = HINTS[langOf(lang)];
    return t[id] || t._;
  }

  function upcomingSeries(container) {
    var cards = container.querySelectorAll(".upcoming-show-card");
    var seen = {};
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      var name = card.querySelector(".cardText-first .textActionButton");
      var key = name ? name.textContent : "";
      if (!key) continue;
      if (seen[key]) {
        if (card.style.display !== "none") card.style.display = "none";
        continue;
      }
      seen[key] = true;
      var ep = card.querySelector(".cardText-secondary .textActionButton");
      var m = ep && /^(S\d+)E\d+/.exec(ep.textContent);
      if (m && ep.textContent !== m[1]) {
        ep.title = ep.textContent;
        ep.textContent = m[1];
      }
    }
  }

  function upcomingDates(container, lang, now) {
    var locale = langOf(lang) === "de" ? "de-CH" : "en-GB";
    var year = (now || new Date()).getFullYear();
    var els = container.querySelectorAll('[class*="upcoming-"][class*="-card"] .cardText-tertiary .textActionButton');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.hasAttribute("data-hss-date")) continue;
      var m = /(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$/.exec(el.textContent);
      if (!m) continue;
      var d = new Date(+m[3], +m[2] - 1, +m[1]);
      var opts = { weekday: "short", day: "numeric", month: "short" };
      if (d.getFullYear() !== year) opts.year = "numeric";
      var text = d.toLocaleDateString(locale, opts);
      el.setAttribute("data-hss-date", "");
      el.title = el.textContent;
      el.textContent = locale === "en-GB" ? text.replace(/,/g, "") : text;
    }
  }

  function namesOf(resp) {
    var out = {};
    var items = resp && resp.Items;
    if (!items || !items.length) return out;
    for (var i = 0; i < items.length; i++) {
      if (items[i] && typeof items[i].Section === "string" && items[i].DisplayText) out[items[i].Section] = String(items[i].DisplayText);
    }
    return out;
  }

  function enabledOf(resp) {
    var list = resp && resp.EnabledSections;
    if (!list || !list.length) return [];
    var out = [];
    for (var i = 0; i < list.length; i++) {
      if (typeof list[i] === "string" && /^[A-Za-z0-9]+$/.test(list[i])) out.push(list[i]);
    }
    return out;
  }

  function realOf(container, id) {
    var out = [];
    var kids = container.children;
    for (var i = 0; i < kids.length; i++) {
      var el = kids[i];
      if (el.hasAttribute("data-hss-placeholder") || !el.classList.contains("verticalSection")) continue;
      for (var j = 0; j < el.classList.length; j++) {
        var c = el.classList[j];
        if (c === id || c.indexOf(id + "-") === 0) { out.push(el); break; }
      }
    }
    return out;
  }

  function filled(el) {
    var items = el.querySelector(".itemsContainer");
    return !el.classList.contains("hide") && !!items && items.children.length > 0;
  }

  function build(doc, id, name, hint) {
    var box = doc.createElement("div");
    box.className = "verticalSection hssEmptySection";
    box.setAttribute("data-hss-placeholder", id);
    var head = doc.createElement("div");
    head.className = "sectionTitleContainer sectionTitleContainer-cards padded-left";
    var h2 = doc.createElement("h2");
    h2.className = "sectionTitle sectionTitle-cards";
    h2.textContent = name;
    head.appendChild(h2);
    var p = doc.createElement("div");
    p.className = "padded-left padded-right";
    p.setAttribute("data-hss-hint", "");
    p.style.opacity = "0.6";
    p.style.margin = "0.25em 0 1.5em";
    p.textContent = hint;
    box.appendChild(head);
    box.appendChild(p);
    return box;
  }

  function render(container, enabled, names, lang) {
    var doc = container.ownerDocument;
    var anchor = null;
    var wanted = {};
    for (var i = 0; i < enabled.length; i++) {
      var id = enabled[i];
      wanted[id] = true;
      var real = realOf(container, id);
      var any = false;
      for (var j = 0; j < real.length; j++) if (filled(real[j])) any = true;
      var ph = container.querySelector('[data-hss-placeholder="' + id + '"]');
      if (any) {
        if (ph) ph.remove();
        anchor = real[real.length - 1];
        continue;
      }
      var name = (names && names[id]) || id;
      var hint = hintFor(id, lang);
      if (!ph) ph = build(doc, id, name, hint);
      else {
        if (ph.querySelector("h2").textContent !== name) ph.querySelector("h2").textContent = name;
        if (ph.querySelector("[data-hss-hint]").textContent !== hint) ph.querySelector("[data-hss-hint]").textContent = hint;
      }
      var after = real.length ? real[real.length - 1] : anchor;
      var order = after ? after.style.order : "0";
      if (ph.style.order !== order) ph.style.order = order;
      var next = after ? after.nextSibling : container.firstChild;
      if (next !== ph) container.insertBefore(ph, next);
      anchor = ph;
    }
    var all = container.querySelectorAll("[data-hss-placeholder]");
    for (var k = 0; k < all.length; k++) if (!wanted[all[k].getAttribute("data-hss-placeholder")]) all[k].remove();
  }

  function start(win) {
    var doc = win.document;
    var cache = null;
    var timer = null;

    function data(api) {
      var userId = api.getCurrentUserId();
      if (cache && cache.userId === userId && Date.now() - cache.at < CACHE_MS) return cache.promise;
      var lang = doc.documentElement.lang || win.navigator.language || "en";
      var promise = Promise.all([
        api.getJSON(api.getUrl("ModularHomeViews/UserSettings", { userId: userId })),
        api.getJSON(api.getUrl("ModularHomeViews/Sections", { language: lang }))
      ]).then(function (r) { return { enabled: enabledOf(r[0]), names: namesOf(r[1]), lang: lang }; });
      cache = { userId: userId, at: Date.now(), promise: promise };
      promise.catch(function () { cache = null; });
      return promise;
    }

    function run() {
      timer = null;
      var api = win.ApiClient;
      if (!api || !win.HssPageMeta || !api.getCurrentUserId || !api.getCurrentUserId()) return;
      var containers = doc.querySelectorAll(".homeSectionsContainer");
      var live = [];
      for (var i = 0; i < containers.length; i++) if (containers[i].querySelector(".verticalSection[data-page]")) live.push(containers[i]);
      if (!live.length) return;
      var lang = doc.documentElement.lang || win.navigator.language || "en";
      observer.disconnect();
      try {
        for (var u = 0; u < live.length; u++) {
          upcomingSeries(live[u]);
          upcomingDates(live[u], lang);
        }
      } finally {
        observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
      }
      data(api).then(function (d) {
        if (!d.enabled.length) return;
        observer.disconnect();
        try {
          for (var j = 0; j < live.length; j++) render(live[j], d.enabled, d.names, d.lang);
        } finally {
          observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
        }
      }).catch(function () {});
    }

    var observer = new win.MutationObserver(function () {
      if (timer) win.clearTimeout(timer);
      timer = win.setTimeout(run, SETTLE_MS);
    });
    observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { render: render, upcomingDates: upcomingDates, upcomingSeries: upcomingSeries, hintFor: hintFor, namesOf: namesOf, enabledOf: enabledOf, start: start };
  } else if (window.MutationObserver && document.body) {
    start(window);
  } else if (window.MutationObserver) {
    document.addEventListener("DOMContentLoaded", function () { start(window); });
  }
})();
