(function () {
  // Desktop downloads (Docker/Jellyfin README "Desktop downloads"): Jellyfin's own Download has no quality choice, so the browser and Jellyfin Media Player
  // ask jellyfin-popular first and show "Full file" / "Phone version" when a title can have a phone version; any error downloads the full file as before.
  var API = "hss-popular/download-choice";
  var GATHER_MS = 400;
  var MAX_ITEMS = 50;
  var PATH_RE = /^((?:\/emby|\/mediabrowser)?)\/items\/([0-9a-f]{32})\/download$/i;
  var TEXTS = {
    en: {
      title: "Download", full: "Full file", phone: "Phone version", titles: "{n} titles",
      order: "smaller, made on request (usually 5–30 min)", preparingHint: "being prepared: {what}",
      now: "{size} now", onRequest: "{n} made on request", inPrep: "{n} being prepared",
      download: "Download", cancel: "Cancel", ok: "OK", failed: "That did not work. Please try again.",
      ordered: "The phone version is being prepared, usually 5 to 30 minutes. You get a ✅ message when it is ready; then download again and pick the phone version.",
      preparing: "The phone version is being prepared ({what}). You get a ✅ message when it is ready; then download again and pick the phone version.",
    },
    de: {
      title: "Herunterladen", full: "Ganze Datei", phone: "Handy-Version", titles: "{n} Titel",
      order: "kleiner, wird auf Anfrage erstellt (meist 5–30 Min.)", preparingHint: "wird vorbereitet: {what}",
      now: "{size} jetzt", onRequest: "{n} auf Anfrage", inPrep: "{n} in Vorbereitung",
      download: "Herunterladen", cancel: "Abbrechen", ok: "OK", failed: "Das hat nicht geklappt. Bitte nochmals versuchen.",
      ordered: "Die Handy-Version wird vorbereitet, meist 5 bis 30 Minuten. Du bekommst eine ✅ Nachricht, sobald sie bereit ist; dann nochmals herunterladen und die Handy-Version wählen.",
      preparing: "Die Handy-Version wird vorbereitet ({what}). Du bekommst eine ✅ Nachricht, sobald sie bereit ist; dann nochmals herunterladen und die Handy-Version wählen.",
    },
  };

  function texts(lang) {
    return String(lang || "").toLowerCase().indexOf("de") === 0 ? TEXTS.de : TEXTS.en;
  }

  function itemOf(url, origin) {
    var u;
    try {
      u = new URL(url, origin);
    } catch (e) {
      return null;
    }
    if (!url || u.origin !== origin) return null;
    var m = PATH_RE.exec(u.pathname);
    return m ? m[2].toLowerCase() : null;
  }

  function swapItem(url, from, to) {
    if (from === to) return url;
    return url.replace(new RegExp("/items/" + from + "/download(?=[?#]|$)", "i"), "/Items/" + to + "/Download");
  }

  function formatSize(bytes, lang) {
    if (typeof bytes !== "number" || !(bytes > 0)) return "";
    var gb = bytes >= 1e9;
    var s = gb ? (bytes / 1e9).toFixed(1) + " GB" : Math.round(bytes / 1e6) + " MB";
    return texts(lang) === TEXTS.de ? s.replace(".", ",") : s;
  }

  function join(parts) {
    return parts.filter(Boolean).join(" · ");
  }

  // The two options for the dialog from the server's offer ({id: {name, full: {size, label}, phone: null | {state, ...}}}).
  function choices(items, lang) {
    var t = texts(lang);
    var list = Object.keys(items).map(function (k) { return items[k]; });
    if (list.length === 1) {
      var one = list[0];
      var p = one.phone || {};
      var detail = p.state === "ready" ? join([p.label, formatSize(p.size, lang)]) : p.state === "preparing" ? t.preparingHint.replace("{what}", p.what) : t.order;
      return { title: one.name || t.title, options: [{ value: "full", label: t.full, detail: join([one.full.label, formatSize(one.full.size, lang)]) }, { value: "phone", label: t.phone, detail: detail }] };
    }
    var full = 0, now = 0, order = 0, prep = 0;
    list.forEach(function (x) {
      full += x.full.size || 0;
      var st = x.phone && x.phone.state;
      if (st === "ready") now += x.phone.size || 0;
      else if (st === "order") order += 1;
      else if (st === "preparing") prep += 1;
      else now += x.full.size || 0;
    });
    var parts = [now ? t.now.replace("{size}", formatSize(now, lang)) : "", order ? t.onRequest.replace("{n}", order) : "", prep ? t.inPrep.replace("{n}", prep) : ""];
    return { title: t.titles.replace("{n}", list.length), options: [{ value: "full", label: t.full, detail: formatSize(full, lang) }, { value: "phone", label: t.phone, detail: join(parts) }] };
  }

  // After "Phone version": the URLs to download (the caught URL with the file's id) and the note to show, if any.
  function plan(result, urls, lang) {
    var t = texts(lang);
    var downloads = (result.downloads || []).filter(function (d) { return urls[d.item]; }).map(function (d) { return { item: d.item, url: swapItem(urls[d.item], d.item, d.file) }; });
    var message = (result.ordered || []).length ? t.ordered : (result.preparing || []).length ? t.preparing.replace("{what}", result.preparing[0].what) : null;
    return { downloads: downloads, message: message };
  }

  // The same header jellyfin-apiclient sends (setRequestHeaders: the names as it stores them), so the server sees which app and device asks.
  function authHeader(api) {
    var q = function (v) { return String(v == null ? "" : v).replace(/"/g, ""); };
    return 'MediaBrowser Client="' + q(api.appName()) + '", Device="' + q(api.deviceName()) + '", DeviceId="' + q(api.deviceId()) +
      '", Version="' + q(api.appVersion()) + '", Token="' + q(api.accessToken()) + '"';
  }

  function css() {
    if (document.getElementById("hssdl-style")) return;
    var s = document.createElement("style");
    s.id = "hssdl-style";
    s.textContent = ".hssdl-back{position:fixed;inset:0;z-index:100000;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;padding:16px}" +
      ".hssdl-box{background:#202020;color:#fff;border-radius:8px;max-width:440px;width:100%;padding:20px;box-shadow:0 8px 32px rgba(0,0,0,.6);font-family:inherit}" +
      ".hssdl-box h2{margin:0 0 14px;font-size:1.2em;font-weight:500}" +
      ".hssdl-opt{display:flex;align-items:center;gap:12px;width:100%;text-align:left;background:#2b2b2b;color:#fff;border:2px solid transparent;border-radius:6px;padding:12px;margin:0 0 8px;cursor:pointer;font:inherit}" +
      ".hssdl-opt[aria-checked=true]{border-color:#00a4dc;background:#263038}" +
      ".hssdl-dot{flex:none;width:16px;height:16px;border-radius:50%;border:2px solid #bbb}" +
      ".hssdl-opt[aria-checked=true] .hssdl-dot{border-color:#00a4dc;background:radial-gradient(#00a4dc 45%,transparent 50%)}" +
      ".hssdl-label{display:block;font-weight:500}.hssdl-detail{display:block;opacity:.75;font-size:.9em;margin-top:2px}" +
      ".hssdl-msg{margin:4px 0 14px;line-height:1.4}" +
      ".hssdl-row{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}" +
      ".hssdl-btn{font:inherit;border:0;border-radius:4px;padding:8px 16px;cursor:pointer;background:#3a3a3a;color:#fff}.hssdl-btn.hssdl-go{background:#00a4dc}";
    document.head.appendChild(s);
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function dialog(choice, t, onPick) {
    css();
    var back = el("div", "hssdl-back");
    var box = el("div", "hssdl-box");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    back.appendChild(box);
    var picked = "full";
    function close() {
      document.removeEventListener("keydown", onKey, true);
      back.remove();
    }
    function onKey(e) {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
    }
    function message(text) {
      box.textContent = "";
      box.appendChild(el("p", "hssdl-msg", text));
      var row = el("div", "hssdl-row");
      var ok = el("button", "hssdl-btn hssdl-go", t.ok);
      ok.type = "button";
      ok.onclick = close;
      row.appendChild(ok);
      box.appendChild(row);
      ok.focus();
    }
    box.appendChild(el("h2", null, t.title + ": " + choice.title));
    var list = el("div");
    list.setAttribute("role", "radiogroup");
    var buttons = choice.options.map(function (o) {
      var b = el("button", "hssdl-opt");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("data-choice", o.value);
      b.appendChild(el("span", "hssdl-dot"));
      var txt = el("span");
      txt.appendChild(el("span", "hssdl-label", o.label));
      txt.appendChild(el("span", "hssdl-detail", o.detail));
      b.appendChild(txt);
      b.onclick = function () { picked = o.value; sync(); };
      list.appendChild(b);
      return b;
    });
    function sync() {
      buttons.forEach(function (b) { b.setAttribute("aria-checked", String(b.getAttribute("data-choice") === picked)); });
    }
    sync();
    box.appendChild(list);
    var row = el("div", "hssdl-row");
    var cancel = el("button", "hssdl-btn", t.cancel);
    cancel.type = "button";
    cancel.onclick = close;
    var go = el("button", "hssdl-btn hssdl-go", t.download);
    go.type = "button";
    go.onclick = function () {
      go.disabled = true;
      onPick(picked, close, message);
    };
    row.appendChild(cancel);
    row.appendChild(go);
    box.appendChild(row);
    back.addEventListener("click", function (e) { if (e.target === back) close(); });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(back);
    go.focus();
  }

  function post(api, body) {
    return window.fetch(api.getUrl(API), { method: "POST", headers: { "Content-Type": "application/json", Authorization: authHeader(api) }, body: JSON.stringify(body) })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); });
  }

  function decide(batch) {
    var api = window.ApiClient;
    var lang = document.documentElement.lang || navigator.language;
    var urls = {}, runs = {};
    batch.forEach(function (d) { urls[d.id] = d.url; runs[d.id] = d.run; });
    function fullFiles() { batch.forEach(function (d) { d.run(d.url); }); }
    if (!api || !api.accessToken || !api.accessToken() || Object.keys(urls).length > MAX_ITEMS) return fullFiles();
    post(api, { items: Object.keys(urls) }).then(function (offer) {
      if (!offer || !offer.choice || !offer.items) return fullFiles();
      var t = texts(lang);
      dialog(choices(offer.items, lang), t, function (picked, close, message) {
        if (picked !== "phone") { close(); fullFiles(); return; }
        post(api, { items: Object.keys(urls), pick: "phone" }).then(function (result) {
          var p = plan(result, urls, lang);
          p.downloads.forEach(function (d, i) { setTimeout(function () { runs[d.item](d.url); }, 150 * i); });
          if (p.message) message(p.message);
          else close();
        }).catch(function () { message(t.failed); });
      });
    }).catch(fullFiles);
  }

  function install() {
    var pending = [];
    var timer = null;
    // "Download all" clicks one link every 100 ms: wait until they stop, then ask once for all of them
    function queue(d) {
      pending.push(d);
      clearTimeout(timer);
      timer = setTimeout(function () {
        var batch = pending;
        pending = [];
        decide(batch);
      }, GATHER_MS);
    }
    // the browser: jellyfin-web multiDownload clicks a detached <a download>
    var click = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {
      var id = this.hasAttribute("download") && !this.hssPass ? itemOf(this.href, window.location.origin) : null;
      if (!id) return click.apply(this, arguments);
      queue({ id: id, url: this.href, run: function (url) {
        var a = document.createElement("a");
        a.download = "";
        a.href = url;
        a.hssPass = true;
        click.call(a);
      } });
    };
    // Jellyfin Media Player: NativeShell.downloadFile opens the URL in the system browser
    var shell = window.NativeShell;
    if (shell && typeof shell.downloadFile === "function" && !shell.hssDownloadChoice) {
      var native = shell.downloadFile;
      shell.hssDownloadChoice = true;
      shell.downloadFile = function (info) {
        var id = info && itemOf(info.url, window.location.origin);
        if (!id) return native.apply(shell, arguments);
        queue({ id: id, url: info.url, run: function (url) { native.call(shell, Object.assign({}, info, { url: url })); } });
      };
    }
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { itemOf: itemOf, swapItem: swapItem, formatSize: formatSize, choices: choices, plan: plan, texts: texts, authHeader: authHeader };
  } else {
    install();
  }
})();
