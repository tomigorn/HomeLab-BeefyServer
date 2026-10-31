(function () {
  // "Remove from Continue watching" on Continue watching cards (README "Remove from Continue watching"): only the resume position goes to 0, undo puts it back.
  // Played, play count, last played, favourite and lists stay; Next Up keeps the series (it is keyed by the last played date, which stays).
  var ROWS = ".verticalSection.ContinueWatching .itemsContainer, #resumableItems";
  var TEXT = {
    en: { label: "Remove from Continue watching", removed: "Removed from Continue watching", undo: "Undo", failed: "Could not remove it, try again", undoFailed: "Could not put it back" },
    de: { label: "Aus «Weiterschauen» entfernen", removed: "Aus «Weiterschauen» entfernt", undo: "Rückgängig", failed: "Entfernen fehlgeschlagen, nochmals versuchen", undoFailed: "Zurückholen fehlgeschlagen" }
  };
  var CSS = [
    ".hssCwRemoveTouch { position: absolute; top: 0; right: 0; z-index: 2; }",
    ".layout-tv .hssCwRemove { display: none !important; }",
    ".hssCwToast { position: fixed; z-index: 1500; left: 50%; bottom: 5em; transform: translateX(-50%); display: flex; align-items: center; gap: 1em; max-width: 90vw; padding: 0.6em 0.8em 0.6em 1.2em; border-radius: 0.4em; background: #202020; color: #fff; font-weight: 600; box-shadow: 0 0.4em 1.5em rgba(0, 0, 0, 0.7); }",
    ".hssCwUndo { border: 0; border-radius: 0.3em; padding: 0.4em 0.8em; background: #00a4dc; color: #fff; font: inherit; cursor: pointer; }",
    ".hssCwUndo:hover, .hssCwUndo:focus { background: #0cb5f0; outline: none; }"
  ].join("\n");

  function tr(lang, key) {
    return TEXT[String(lang || "").toLowerCase().indexOf("de") === 0 ? "de" : "en"][key];
  }

  function hover(win) {
    return !(win && win.matchMedia) || win.matchMedia("(hover: hover)").matches;
  }

  // Jellyfin here has EnableLegacyAuthorization off: only the Authorization header counts.
  function auth(a) {
    return { Authorization: 'MediaBrowser Token="' + a.accessToken() + '"' };
  }

  function styles(doc) {
    if (doc.getElementById("hss-cw-style")) return;
    var s = doc.createElement("style");
    s.id = "hss-cw-style";
    s.textContent = CSS;
    (doc.head || doc.documentElement).appendChild(s);
  }

  function button(doc, cls, iconCls, id, lang, name) {
    var b = doc.createElement("button");
    b.type = "button";
    b.className = cls + " hssCwRemove";
    b.setAttribute("data-hss-cw", id);
    b.setAttribute("title", tr(lang, "label"));
    b.setAttribute("aria-label", (name ? name + ": " : "") + tr(lang, "label"));
    var i = doc.createElement("span");
    i.className = iconCls + " visibility_off";
    i.setAttribute("aria-hidden", "true");
    b.appendChild(i);
    return b;
  }

  function decorate(doc, card, lang) {
    var id = card.getAttribute("data-id");
    var mine = card.querySelector(".hssCwRemove");
    if (mine && mine.getAttribute("data-hss-cw") === id) return;
    if (mine) mine.remove();
    var box = card.querySelector(".cardScalable");
    if (!id || !box) return;
    var named = card.querySelector(".cardText bdi");
    var name = named ? named.textContent.trim() : "";
    var over = hover(doc.defaultView) && box.querySelector(".cardOverlayContainer");
    if (over) {
      var br = over.querySelector(".cardOverlayButton-br");
      if (!br) br = over.appendChild(doc.createElement("div"));
      br.classList.add("cardOverlayButton-br", "flex");
      br.appendChild(button(doc, "cardOverlayButton cardOverlayButton-hover paper-icon-button-light", "material-icons cardOverlayButtonIcon cardOverlayButtonIcon-hover", id, lang, name));
    } else {
      box.appendChild(button(doc, "cardOverlayButton paper-icon-button-light hssCwRemoveTouch", "material-icons cardOverlayButtonIcon", id, lang, name));
    }
  }

  function start(win, opts) {
    var doc = win.document;
    var undoMs = (opts && opts.undoMs) || 5000;
    var toastTimer = null;
    var pending = null;

    function lang() { return doc.documentElement.lang || win.navigator.language || "en"; }
    function tv() { return doc.documentElement.classList.contains("layout-tv"); }

    function scan() {
      pending = null;
      if (tv() || !win.ApiClient || !doc.body) return;
      styles(doc);
      var l = lang();
      var rows = doc.querySelectorAll(ROWS);
      for (var i = 0; i < rows.length; i++) {
        var cards = rows[i].querySelectorAll(".card[data-id]");
        for (var j = 0; j < cards.length; j++) decorate(doc, cards[j], l);
      }
      if (obs) obs.takeRecords();
    }
    function later() {
      if (!pending) pending = win.setTimeout(scan, 120);
    }

    function closeToast() {
      if (toastTimer) { win.clearTimeout(toastTimer); toastTimer = null; }
      var old = doc.querySelector(".hssCwToast");
      if (old) old.remove();
    }

    function toast(text, undo) {
      closeToast();
      var t = doc.createElement("div");
      t.className = "hssCwToast";
      t.setAttribute("role", "status");
      var span = doc.createElement("span");
      span.textContent = text;
      t.appendChild(span);
      if (undo) {
        var u = doc.createElement("button");
        u.type = "button";
        u.className = "hssCwUndo";
        u.textContent = tr(lang(), "undo");
        u.addEventListener("click", function (e) {
          e.preventDefault();
          closeToast();
          undo();
        });
        t.appendChild(u);
      }
      doc.body.appendChild(t);
      toastTimer = win.setTimeout(closeToast, undo ? undoMs : 4000);
    }

    function write(a, id, ticks) {
      return win.fetch(a.getUrl("UserItems/" + id + "/UserData"), {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, auth(a)),
        body: JSON.stringify({ PlaybackPositionTicks: ticks })
      }).then(function (r) {
        if (r.status < 200 || r.status > 299) throw new Error("HTTP " + r.status);
      });
    }

    function remove(btn) {
      var a = win.ApiClient;
      var card = btn.closest(".card");
      var id = btn.getAttribute("data-hss-cw");
      if (!a || !card || btn.disabled) return;
      var l = lang();
      btn.disabled = true;
      win.fetch(a.getUrl("UserItems/" + id + "/UserData"), { headers: auth(a) }).then(function (r) {
        if (r.status !== 200) throw new Error("HTTP " + r.status);
        return r.json();
      }).then(function (data) {
        var ticks = data && typeof data.PlaybackPositionTicks === "number" ? data.PlaybackPositionTicks : 0;
        return write(a, id, 0).then(function () {
          var parent = card.parentNode;
          var next = card.nextSibling;
          card.remove();
          btn.disabled = false;
          toast(tr(l, "removed"), ticks > 0 ? function () {
            write(a, id, ticks).then(function () {
              if (!parent || !parent.isConnected || parent.querySelector('.card[data-id="' + id + '"]')) return;
              parent.insertBefore(card, next && next.parentNode === parent ? next : null);
            }, function () { toast(tr(l, "undoFailed")); });
          } : null);
        });
      }).then(null, function () {
        btn.disabled = false;
        toast(tr(l, "failed"));
      });
    }

    doc.addEventListener("click", function (e) {
      var b = e.target && e.target.closest && e.target.closest("button.hssCwRemove");
      if (!b) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      remove(b);
    }, true);

    var obs = win.MutationObserver ? new win.MutationObserver(function (list) {
      for (var i = 0; i < list.length; i++) {
        var t = list[i].target;
        if (t.closest && t.closest(".hssCwToast")) continue;
        if (list[i].type === "attributes") return later();
        var added = list[i].addedNodes;
        for (var k = 0; k < added.length; k++) if (added[k].nodeType === 1) return later();
      }
    }) : null;
    if (obs) obs.observe(doc.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-id"] });
    doc.addEventListener("viewshow", later, true);
    win.addEventListener("hashchange", later);
    later();
    return { scan: scan };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { start: start, TEXT: TEXT };
  } else if (!window.HssContinueRemove) {
    window.HssContinueRemove = start(window);
  }
})();
