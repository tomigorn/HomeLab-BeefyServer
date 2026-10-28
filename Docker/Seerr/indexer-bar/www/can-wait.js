// "Can wait" checkbox in Seerr's request dialog (README "Can wait"). Unticked = the request carries the Radarr/Sonarr tag download-now and downloads at once. Anything unexpected = no box, no tag: the request waits for the night.
(function () {
  "use strict";
  var TAG = "download-now";
  var TITLE = /^(Request (Movie|Series|Collection)( in 4K)?|(Film|Serie|Sammlung)( in 4K)? anfragen)$/;
  var BOX = "hg-can-wait";
  var cache = {};

  function isRequestTitle(text) {
    return TITLE.test((text || "").trim());
  }

  function service(mediaType) {
    return mediaType === "movie" ? "radarr" : mediaType === "tv" ? "sonarr" : null;
  }

  function serverId(servers, body) {
    if (body.serverId !== undefined && body.serverId !== null) return body.serverId;
    var is4k = !!body.is4k;
    for (var i = 0; i < servers.length; i++) if (servers[i].isDefault && !!servers[i].is4k === is4k) return servers[i].id;
    return null;
  }

  function tagId(details) {
    var tags = (details && details.tags) || [];
    for (var i = 0; i < tags.length; i++) if (tags[i].label === TAG) return tags[i].id;
    return null;
  }

  function withTag(body, id, defaults) {
    var b = JSON.parse(JSON.stringify(body));
    var tags = Array.isArray(b.tags) ? b.tags.slice() : (defaults || []).slice();
    if (tags.indexOf(id) < 0) tags.push(id);
    b.tags = tags;
    return b;
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

  // resolves to the body with the tag added, or rejects (then the body goes out unchanged)
  function tagged(body) {
    var svc = service(body.mediaType);
    if (!svc) return Promise.reject(new Error("media type"));
    var key = svc + "|" + (body.serverId == null ? "" : body.serverId) + "|" + !!body.is4k;
    if (!cache[key]) {
      cache[key] = getJSON("/api/v1/service/" + svc).then(function (servers) {
        var id = serverId(servers, body);
        if (id === null) throw new Error("no server");
        var server = servers.filter(function (s) { return s.id === id; })[0] || {};
        return getJSON("/api/v1/service/" + svc + "/" + id).then(function (d) {
          var t = tagId(d);
          if (t === null) throw new Error("no tag " + TAG);
          return { id: t, defaults: server.activeTags || [] };
        });
      });
      cache[key].catch(function () { delete cache[key]; });
    }
    return cache[key].then(function (r) { return withTag(body, r.id, r.defaults); });
  }

  function wantsNow() {
    var input = document.querySelector("#" + BOX + " input");
    return !!input && input.checked === false;
  }

  function isNewRequest(method, url) {
    if (String(method).toUpperCase() !== "POST") return false;
    var path = String(url).replace(/^https?:\/\/[^/]+/, "").split("?")[0];
    return path === "/api/v1/request";
  }

  function hookXHR(XHR) {
    if (!XHR || !XHR.prototype || XHR.prototype.__hgCanWait) return;
    var open = XHR.prototype.open, send = XHR.prototype.send;
    XHR.prototype.__hgCanWait = true;
    XHR.prototype.open = function (method, url) {
      this.__hgReq = [method, url];
      return open.apply(this, arguments);
    };
    XHR.prototype.send = function (body) {
      var self = this, req = this.__hgReq || [];
      if (this.__hgSkip || !isNewRequest(req[0], req[1]) || typeof body !== "string" || !wantsNow()) return send.apply(this, arguments);
      var parsed;
      try { parsed = JSON.parse(body); } catch (e) { return send.apply(this, arguments); }
      tagged(parsed).then(function (b) { send.call(self, JSON.stringify(b)); }, function () { send.call(self, body); });
    };
  }

  function germanUI(doc) {
    var nl = typeof window !== "undefined" && window.hgNightLabel;
    if (nl && nl.lang) { try { return nl.lang() === "de"; } catch (e) { /* own detection */ } }
    var tv = doc.querySelector('a[href="/discover/tv"]');
    return !!tv && /\bSerien\b/.test(tv.textContent);
  }

  function makeBox(doc) {
    var wrap = doc.createElement("div");
    wrap.id = BOX;
    wrap.className = "mt-4";
    wrap.style.cssText = "display:flex;align-items:flex-start;gap:.6rem;padding:.6rem .75rem;border:1px solid #4b5563;border-radius:.375rem;background:rgba(17,24,39,.6);color:#e5e7eb;font-size:.875rem;line-height:1.25rem;";
    var input = doc.createElement("input");
    input.type = "checkbox";
    input.checked = true;
    input.id = BOX + "-input";
    input.style.cssText = "margin-top:.15rem;width:1rem;height:1rem;flex:none;accent-color:#6366f1;";
    var label = doc.createElement("label");
    label.htmlFor = input.id;
    label.style.cssText = "cursor:pointer;";
    var strong = doc.createElement("strong");
    var de = germanUI(doc);
    strong.textContent = de ? "Kann warten — heute Nacht herunterladen (günstigerer Strom)" : "Can wait — download tonight (cheaper power)";
    var hint = doc.createElement("div");
    hint.style.cssText = "color:#9ca3af;font-size:.8rem;";
    hint.textContent = de ? "Abwählen, wenn du es jetzt brauchst: der Download startet sofort." : "Untick if you need it now: the download starts at once.";
    label.appendChild(strong);
    label.appendChild(hint);
    wrap.appendChild(input);
    wrap.appendChild(label);
    return wrap;
  }

  function attach(doc) {
    var dialogs = doc.querySelectorAll('[role="dialog"]');
    for (var i = 0; i < dialogs.length; i++) {
      var d = dialogs[i];
      if (d.querySelector("#" + BOX)) continue;
      var title = d.querySelector('[data-testid="modal-title"]');
      var ok = d.querySelector('[data-testid="modal-ok-button"]');
      if (!title || !ok || !isRequestTitle(title.textContent)) continue;
      var row = ok.parentElement;
      if (!row || !row.parentElement) continue;
      row.parentElement.insertBefore(makeBox(doc), row);
    }
  }

  var api = { isRequestTitle: isRequestTitle, serverId: serverId, tagId: tagId, withTag: withTag, isNewRequest: isNewRequest, attach: attach, hookXHR: hookXHR, tagged: tagged };
  if (typeof window === "undefined") return;
  window.hgCanWait = api;
  try {
    hookXHR(window.XMLHttpRequest);
    if (!window.MutationObserver || !document.body) return;
    attach(document);
    new MutationObserver(function () {
      try { attach(document); } catch (e) { /* never break Seerr */ }
    }).observe(document.body, { childList: true, subtree: true });
  } catch (e) { /* never break Seerr */ }
})();
