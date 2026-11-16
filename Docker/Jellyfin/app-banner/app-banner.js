(function () {
  var APPLE = { name: "App Store", url: "https://apps.apple.com/app/id6761283970" };
  var GOOGLE = { name: "Google Play", url: "https://play.google.com/store/apps/details?id=org.moonfin.androidtv" };
  var TV = /SMART-TV|SmartTV|Tizen|Web0S|webOS|GoogleTV|Android TV|BRAVIA|\bAFT[A-Z0-9]*\b|\bCrKey\b/i;
  // What most of our library needs (Docker/Jellyfin README "Moonfin app prompt").
  var FORMATS = [
    { name: "MKV", types: ["video/x-matroska"] },
    { name: "HEVC 10-bit", types: ['video/mp4; codecs="hvc1.2.4.L153.B0"', 'video/mp4; codecs="hev1.2.4.L153.B0"'] },
    { name: "Dolby Digital Plus", types: ['audio/mp4; codecs="ec-3"'] },
  ];

  function storeFor(ua, maxTouchPoints, nativeShell) {
    ua = ua || "";
    if (nativeShell || TV.test(ua)) return null;
    if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && maxTouchPoints > 1)) return APPLE;
    if (/Android/.test(ua)) return GOOGLE;
    return null;
  }

  function missingFormats(canPlay) {
    return FORMATS.filter(function (f) { return !f.types.some(canPlay); }).map(function (f) { return f.name; });
  }

  function promptOnStart(store, missing) {
    return Boolean(store) && missing.length > 0;
  }

  var QUICK_CONNECT = /^\/quickconnect(?:\.html)?\/?$/i;
  var WAIT = /^\/(?:login|selectserver|addserver|forgotpassword|forgotpasswordpin)?(?:\.html)?\/?$/i;

  // The TV's QR code opens /web/#/quickconnect?code=…; signed out, the app first shows /login?url=%2Fquickconnect…
  function routeKind(href) {
    var page, route;
    try {
      page = new URL(href, "https://x/");
      route = new URL(page.hash.replace(/^#!?/, "") || "/", "https://x/");
    } catch (e) {
      return "prompt";
    }
    var path = page.pathname.replace(/^.*\/web(?=\/|$)/i, "").replace(/\/index\.html$/i, "") || "/";
    if (QUICK_CONNECT.test(path) || QUICK_CONNECT.test(route.pathname)) return "skip";
    var next = route.searchParams.get("url");
    if (next && routeKind(new URL(next, "https://x/web/").href) === "skip") return "skip";
    return WAIT.test(route.pathname) ? "wait" : "prompt";
  }

  // Run check() on every route change: shows the start prompt on the first normal page, closes it unanswered when the app moves to a page that must not show it.
  function startPrompt(getHref, show, hide) {
    var state = "idle";
    return function check() {
      if (state === "done") return state;
      var kind = routeKind(getHref());
      if (state === "idle" && kind === "prompt") {
        state = "shown";
        show().then(function (choice) { if (state === "shown") state = choice === "quickconnect" ? "idle" : "done"; });
      } else if (state === "shown" && kind !== "prompt") {
        state = "idle";
        hide();
      }
      return state;
    };
  }

  function quickConnectHref(href) {
    var page = new URL(href);
    page.hash = "/quickconnect";
    return page.href;
  }

  function isPlaybackInfo(url, method) {
    return /^post$/i.test(method || "") && /\/Items\/[^/?#]+\/PlaybackInfo(?:[?#]|$)/i.test(String(url));
  }

  function texts(lang, mode) {
    var de = /^de\b/i.test(lang || "");
    var t = de ? {
      title: "Schau in der Moonfin-App",
      body: "Dieser Browser kann die meisten unserer Filme nicht direkt abspielen: der Server muss sie umwandeln, ohne 4K HDR und mit längerem Start. Die kostenlose Moonfin-App spielt alles in voller Qualität.",
      yes: "Ja, Moonfin laden",
      no: mode === "play" ? "Trotzdem im Browser abspielen" : "Nein, ich stöbere nur",
      more: "Weitere Optionen",
      quickconnect: "Fernseher mit Quick-Connect-Code verbinden",
      back: "← Zurück",
    } : {
      title: "Watch in the Moonfin app",
      body: "This browser can't play most of our films directly: the server has to convert them, without 4K HDR and with a slower start. The free Moonfin app plays everything in full quality.",
      yes: "Yes, get Moonfin",
      no: mode === "play" ? "Play in the browser anyway" : "No, I'm only browsing",
      more: "More options",
      quickconnect: "Connect a TV with a Quick Connect code",
      back: "← Back",
    };
    return t;
  }

  function layout(mode) {
    return mode === "play" ? { back: true, shown: ["yes"], more: ["no"], link: [] } : { back: false, shown: ["yes", "no"], more: [], link: ["quickconnect"] };
  }

  function canPlay(type) {
    try {
      if (document.createElement("video").canPlayType(type)) return true;
      return Boolean(window.MediaSource && MediaSource.isTypeSupported(type));
    } catch (e) {
      return false;
    }
  }

  function button(label, primary) {
    var b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.style.cssText = "display:block;width:100%;margin-top:12px;padding:14px 16px;border-radius:24px;font:inherit;font-size:16px;font-weight:600;cursor:pointer;" +
      (primary ? "background:#00a4dc;color:#fff;border:0" : "background:transparent;color:#fff;border:1px solid rgba(255,255,255,.4)");
    return b;
  }

  // Resolves "app", "browser", "back" (play cancelled, stay on the page) or "quickconnect" (no answer, the next normal page asks again).
  function ask(store, mode) {
    return new Promise(function (resolve) {
      var old = document.getElementById("hgAppPrompt");
      if (old) old.remove();
      var t = texts(navigator.language, mode);
      var screen = document.createElement("div");
      screen.id = "hgAppPrompt";
      screen.setAttribute("data-mode", mode);
      screen.setAttribute("role", "dialog");
      screen.setAttribute("aria-modal", "true");
      screen.setAttribute("aria-label", t.title);
      // Auto margins centre the box but collapse when the viewport is too short, so the screen scrolls instead of clipping.
      screen.style.cssText = "position:fixed;inset:0;z-index:2147483000;display:flex;flex-direction:column;align-items:center;overflow-y:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;box-sizing:border-box;" +
        "padding:calc(24px + env(safe-area-inset-top)) calc(24px + env(safe-area-inset-right)) calc(12px + env(safe-area-inset-bottom)) calc(24px + env(safe-area-inset-left));background:rgba(5,8,12,.96);color:#fff;font-family:inherit;text-align:center";
      var box = document.createElement("div");
      box.style.cssText = "max-width:420px;width:100%;margin:auto 0;flex:none";
      var title = document.createElement("h2");
      title.textContent = t.title;
      title.style.cssText = "margin:0 0 12px;font-size:24px";
      var body = document.createElement("p");
      body.textContent = t.body;
      body.style.cssText = "margin:0 0 20px;font-size:16px;line-height:1.45;opacity:.85";
      var yes = button(t.yes, true);
      var no = button(t.no, false);
      yes.addEventListener("click", function () {
        window.open(store.url, "_blank", "noopener");
        screen.remove();
        resolve("app");
      });
      no.addEventListener("click", function () {
        screen.remove();
        resolve("browser");
      });
      box.appendChild(title);
      box.appendChild(body);
      var buttons = { yes: yes, no: no };
      var l = layout(mode);
      if (l.back) {
        var back = document.createElement("button");
        back.type = "button";
        back.textContent = t.back;
        back.style.cssText = "position:absolute;top:calc(12px + env(safe-area-inset-top));left:12px;padding:10px 12px;background:none;border:0;color:#fff;font:inherit;font-size:16px;cursor:pointer";
        back.addEventListener("click", function () {
          screen.remove();
          resolve("back");
        });
        screen.appendChild(back);
      }
      l.shown.forEach(function (k) { box.appendChild(buttons[k]); });
      if (l.more.length) {
        var more = document.createElement("button");
        more.type = "button";
        more.textContent = t.more;
        more.style.cssText = "display:block;margin:28px auto 0;padding:6px;background:none;border:0;color:rgba(255,255,255,.35);font:inherit;font-size:13px;text-decoration:underline;cursor:pointer";
        var hidden = document.createElement("div");
        hidden.hidden = true;
        l.more.forEach(function (k) { hidden.appendChild(buttons[k]); });
        more.addEventListener("click", function () {
          more.remove();
          hidden.hidden = false;
        });
        box.appendChild(more);
        box.appendChild(hidden);
      }
      screen.appendChild(box);
      l.link.forEach(function (k) {
        var link = document.createElement("a");
        link.href = quickConnectHref(location.href);
        link.setAttribute("data-choice", k);
        link.textContent = t[k];
        link.style.cssText = "flex:none;display:inline-block;margin-top:20px;padding:12px 8px;max-width:420px;color:rgba(255,255,255,.6);font-size:14px;line-height:1.3;text-decoration:underline;cursor:pointer";
        link.addEventListener("click", function (e) {
          e.preventDefault();
          screen.remove();
          resolve(k);
          location.href = link.href;
        });
        screen.appendChild(link);
      });
      document.body.appendChild(screen);
      yes.focus();
    });
  }

  // Holds every PlaybackInfo request (fetch and XMLHttpRequest) until ask() answers; asks on every play, nothing is remembered.
  function gatePlayback(win, ask, skip) {
    skip = skip || function () { return false; };
    var pending = null;
    function decide() {
      if (!pending) {
        pending = ask().then(function (choice) {
          pending = null;
          return choice;
        });
      }
      return pending;
    }
    var fetch0 = win.fetch;
    win.fetch = function (input, init) {
      var url = typeof input === "string" ? input : input && input.url;
      var method = (init && init.method) || (input && input.method) || "GET";
      if (!isPlaybackInfo(url, method) || skip()) return fetch0.apply(this, arguments);
      var self = this, args = arguments;
      return decide().then(function (choice) {
        if (choice !== "browser") throw new Error("Playback cancelled: " + choice);
        return fetch0.apply(self, args);
      });
    };
    var proto = win.XMLHttpRequest.prototype;
    var open0 = proto.open, send0 = proto.send;
    proto.open = function (method, url) {
      this.hgPlayback = isPlaybackInfo(url, method) && !skip();
      return open0.apply(this, arguments);
    };
    proto.send = function () {
      if (!this.hgPlayback) return send0.apply(this, arguments);
      var xhr = this, args = arguments;
      decide().then(function (choice) {
        if (choice === "browser") send0.apply(xhr, args); else xhr.abort();
      });
    };
  }

  function start() {
    var store = storeFor(navigator.userAgent, navigator.maxTouchPoints || 0, Boolean(window.NativeShell));
    var missing = missingFormats(canPlay);
    if (!promptOnStart(store, missing)) return;
    gatePlayback(window, function () { return ask(store, "play"); }, function () { return routeKind(location.href) === "skip"; });
    var check = startPrompt(function () { return location.href; }, function () { return ask(store, "start"); }, function () {
      var screen = document.querySelector('#hgAppPrompt[data-mode="start"]');
      if (screen) screen.remove();
    });
    // The router mostly uses history.pushState, which fires no event.
    var timer = setInterval(function () {
      if (check() === "done") clearInterval(timer);
    }, 300);
    window.addEventListener("hashchange", check);
    check();
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { storeFor: storeFor, missingFormats: missingFormats, promptOnStart: promptOnStart, isPlaybackInfo: isPlaybackInfo, gatePlayback: gatePlayback, layout: layout, texts: texts, routeKind: routeKind, startPrompt: startPrompt, quickConnectHref: quickConnectHref, APPLE: APPLE, GOOGLE: GOOGLE, FORMATS: FORMATS };
  } else if (document.body) {
    start();
  } else {
    document.addEventListener("DOMContentLoaded", start);
  }
})();
