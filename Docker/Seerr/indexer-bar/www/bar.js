(function () {
  "use strict";
  if (window.__indexerBar) return;
  window.__indexerBar = true;

  var DATA = "/_indexer-bar/usage.json", REFRESH = 60000, STALE = 10 * 60000, KEY = "indexer-bar-mini";
  var RANK = { ok: 0, yellow: 1, warn: 2, orange: 3, full: 4 };
  var bar, data = null, panelOpen = false, mini = false;

  try { mini = localStorage.getItem(KEY) === "1"; } catch (e) {}

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function add(parent) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]); return parent; }
  function num(n) {
    if (n >= 10000) return (Math.round(n / 100) / 10).toString().replace(/\.0$/, "") + "k";
    return String(n);
  }
  function at(iso) {
    var d = new Date(iso), now = new Date();
    var t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    var tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    if (d.toDateString() === now.toDateString()) return t;
    if (d.toDateString() === tomorrow.toDateString()) return "tomorrow " + t;
    return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }) + " " + t;
  }
  function day(iso) {
    var p = iso.split("-");
    return new Date(+p[0], +p[1] - 1, +p[2]).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
  }
  function worst(list) {
    return list.reduce(function (w, x) { return RANK[x.level] > RANK[w] ? x.level : w; }, "ok");
  }
  function meter(m) {
    var box = el("span", "ib-meter ib-" + m.level), fill = el("span", "ib-fill");
    fill.style.width = Math.min(100, m.limit ? (100 * m.used) / m.limit : 0) + "%";
    fill.style.display = "block";
    return add(box, fill);
  }
  function usable() {
    return data && data.generated && Date.now() - Date.parse(data.generated) < STALE ? data : null;
  }
  function metricText(m, word) {
    var s = el("span", m.level === "ok" ? "" : "ib-" + m.level + "-text");
    add(s, document.createTextNode(num(m.used) + "/" + num(m.limit)), el("span", "ib-word", " " + word));
    return s;
  }
  function resetText(r) {
    var ms = [r.searches, r.downloads].filter(function (m) { return m.limit != null && m.used >= m.limit && m.resetsAt; });
    if (!ms.length) return null;
    var t = ms.map(function (m) { return m.resetsAt; }).sort().pop();
    return el("span", "ib-full-text", "used up, resets " + at(t));
  }
  function subText(s) {
    if (s.lifetime) return "lifetime";
    if (s.expired) return "expired " + day(s.expires);
    if (s.daysLeft === 0) return "ends today";
    return (s.daysLeft === 1 ? "1 day" : s.daysLeft + " days") + " left · " + day(s.expires);
  }

  var DISK_TEXT = {
    en: { free: "free", head: "Storage", SSD: "fast tier, new downloads", HDD: "archive",
      rule: { SSD: function (d) { return "Above " + d.yellow + " % the night mover moves titles nobody played for 30 days (oldest first) to the HDD, down to 60 %; from " + d.red + " % new downloads go straight to the HDD."; },
        HDD: function (d) { return "Archive for older titles. From " + d.red + " % new downloads are no longer imported."; } } },
    de: { free: "frei", head: "Speicher", SSD: "schnell, neue Downloads", HDD: "Archiv",
      rule: { SSD: function (d) { return "Ab " + d.yellow + " % verschiebt der Nacht-Mover Titel, die 30 Tage niemand geschaut hat (älteste zuerst), auf die HDD, bis 60 %; ab " + d.red + " % landen neue Downloads direkt auf der HDD."; },
        HDD: function (d) { return "Archiv für ältere Titel. Ab " + d.red + " % werden neue Downloads nicht mehr importiert."; } } }
  };
  function lang() {
    var nl = window.hgNightLabel;
    if (nl && nl.lang) { try { return nl.lang() === "de" ? "de" : "en"; } catch (e) { /* own detection */ } }
    var tv = document.querySelector('a[href="/discover/tv"]');
    if (tv) return /\bSerien\b/.test(tv.textContent) ? "de" : "en";
    return /^de\b/i.test(navigator.language || "") ? "de" : "en";
  }
  function bytes(n, l) {
    var tb = n / 1099511627776, s = tb >= 1 ? (tb >= 100 ? Math.round(tb) : Math.round(tb * 10) / 10) + " TB" : Math.round(n / 1073741824) + " GB";
    return l === "de" ? s.replace(".", ",") : s;
  }
  function diskUsed(d, l) {
    var used = bytes(d.used, l), total = bytes(d.total, l);
    if (used.slice(-2) === total.slice(-2)) used = used.slice(0, -3);
    return used + "/" + total;
  }
  function diskHint(d, l) {
    var t = DISK_TEXT[l], rule = t.rule[d.name];
    var parts = Array.isArray(d.parts) && d.parts.length > 1 ? " (" + d.parts.length + " SSDs: " + d.parts.map(function (p) { return Math.round(p) + " %"; }).join(" · ") + ")" : "";
    return d.name + ": " + bytes(d.free, l) + " " + t.free + parts + "." + (rule ? " " + rule(d) : "");
  }
  function diskMeter(d) {
    var box = el("span", "ib-meter ib-" + d.level), fill = el("span", "ib-fill");
    fill.style.width = Math.min(100, d.pct) + "%";
    fill.style.display = "block";
    return add(box, fill);
  }
  function disks(d) { return d && Array.isArray(d.storage) ? d.storage : []; }
  function diskChips(box, d) {
    var l = lang();
    disks(d).forEach(function (x) {
      var c = add(el("span", "ib-chip"), el("span", "ib-name", x.name), diskMeter(x),
        el("span", x.level === "ok" ? "" : "ib-" + x.level + "-text", diskUsed(x, l)), el("span", "ib-muted", Math.round(x.pct) + " %"));
      c.setAttribute("data-ib-disk", x.name);
      c.title = diskHint(x, l);
      box.appendChild(c);
    });
  }
  function diskPanel(p, d) {
    var list = disks(d), l = lang(), t = DISK_TEXT[l];
    if (!list.length) return;
    var tbl = el("table");
    list.forEach(function (x) {
      var rule = t.rule[x.name];
      add(tbl, add(el("tr"), add(el("td"), el("div", "ib-name", x.name), el("div", "ib-muted", t[x.name] || "")),
        add(el("td"), add(el("div", "ib-line"), diskMeter(x), el("span", x.level === "ok" ? "" : "ib-" + x.level + "-text", diskUsed(x, l) + " · " + Math.round(x.pct) + " %")),
          add(el("div", "ib-line"), el("span", "ib-muted", bytes(x.free, l) + " " + t.free)))));
      if (rule) { var rc = el("td", "ib-muted ib-rule", rule(x)); rc.colSpan = 2; add(tbl, add(el("tr"), rc)); }
    });
    add(p, el("h4", null, t.head), tbl);
  }

  function chips(box, d) {
    var rows = d && d.ok ? d.indexers.filter(function (r) { return r.limited; }) : [];
    if (!d || !d.ok) add(box, el("span", "ib-chip ib-muted", "Indexer usage unavailable"));
    rows.forEach(function (r) {
      var c = add(el("span", "ib-chip"), el("span", "ib-name", r.name));
      [["searches", "searches"], ["downloads", "downloads"]].forEach(function (k) {
        var m = r[k[0]];
        if (m.limit == null) return;
        add(c, meter(m), metricText(m, k[1]));
      });
      add(c, resetText(r));
      box.appendChild(c);
    });
    var subs = d ? d.subscriptions || [] : [];
    var due = subs.filter(function (s) { return s.level !== "ok"; });
    due.forEach(function (s) {
      add(box, add(el("span", "ib-chip"), el("span", "ib-dot ib-" + s.level), el("span", "ib-name", s.name),
        el("span", "ib-" + s.level + "-text", subText(s))));
    });
    if (!due.length) {
      var next = subs.filter(function (s) { return !s.lifetime; }).sort(function (a, b) { return a.daysLeft - b.daysLeft; })[0];
      if (next) add(box, add(el("span", "ib-chip ib-muted"), el("span", "ib-label", "Next renewal: "),
        document.createTextNode(next.name + " in " + next.daysLeft + " d")));
    }
    diskChips(box, d);
  }

  function panel(d) {
    var p = el("div", "ib-panel");
    p.addEventListener("click", function (e) { e.stopPropagation(); });
    if (!d || !d.ok) add(p, el("h4", null, "Indexers"), el("p", "ib-muted", "Usage unavailable right now (Prowlarr not reachable). Requests still work."));
    if (d && d.ok) {
      var lim = d.indexers.filter(function (r) { return r.limited; }), free = d.indexers.filter(function (r) { return !r.limited; });
      if (lim.length) {
        var t = el("table");
        lim.forEach(function (r) {
          var cell = el("td");
          [["searches", "searches"], ["downloads", "downloads"]].forEach(function (k) {
            var m = r[k[0]], line = el("div", "ib-line");
            if (m.limit == null) add(line, el("span", "ib-muted", num(m.used) + " " + k[1] + " · ∞"));
            else add(line, meter(m), metricText(m, k[1]));
            cell.appendChild(line);
          });
          var rs = resetText(r);
          if (rs) add(cell, add(el("div", "ib-line"), rs));
          var name = add(el("td"), el("div", "ib-name", r.name), el("div", "ib-muted", r.window === "day" ? "per day (midnight)" : "last 24 h"));
          add(t, add(el("tr"), name, cell));
        });
        add(p, el("h4", null, "Limited indexers"), t);
      }
      if (free.length) {
        var u = el("table");
        free.forEach(function (r) {
          add(u, add(el("tr"), el("td", "ib-name", r.name),
            el("td", "ib-muted", num(r.searches.used) + " searches · " + num(r.downloads.used) + " downloads · ∞")));
        });
        add(p, el("h4", null, "Unlimited (last 24 h)"), u);
      }
    }
    var subs = d ? d.subscriptions || [] : [];
    if (subs.length) {
      var s = el("table");
      subs.forEach(function (x) {
        add(s, add(el("tr"), add(el("td"), el("div", "ib-name", x.name), el("div", "ib-muted", x.kind || "")),
          add(el("td"), add(el("div", "ib-line"), x.lifetime ? null : el("span", "ib-dot ib-" + x.level),
            el("span", x.lifetime ? "ib-muted" : "ib-" + x.level + "-text", subText(x))))));
      });
      add(p, el("h4", null, "Subscriptions"), s);
    }
    diskPanel(p, d);
    add(p, el("p", "ib-note", "When a limited indexer is used up, requests may wait until it resets; the other indexers keep searching. Searches made by hand on the indexer websites are not counted."));
    if (d && d.generated) add(p, el("p", "ib-note", "Updated " + at(d.generated)));
    return p;
  }

  function render() {
    try {
      var d = usable(), root = document.documentElement;
      root.classList.add("ib-on");
      root.classList.toggle("ib-mini", mini);
      bar.textContent = "";
      var rows = d && d.ok ? d.indexers.filter(function (r) { return r.limited; }) : [];
      var level = d ? worst(rows.concat(d.subscriptions || [], disks(d))) : null;
      var title = add(el("button", "ib-title"), el("span", "ib-dot" + (level ? " ib-" + level : "")), el("span", null, "Indexers"));
      title.title = mini ? "Show indexer usage" : "Hide indexer usage";
      title.setAttribute("aria-expanded", String(!mini));
      title.addEventListener("click", function (e) { e.stopPropagation(); toggleMini(); });
      bar.appendChild(title);
      var box = el("div", "ib-chips");
      if (mini) {
        var txt = !d ? "usage unavailable" : level === "full" ? "something is used up or ending" : level !== "ok" ? "getting close" : "all fine";
        add(box, el("span", "ib-chip " + (level && level !== "ok" ? "ib-" + level + "-text" : "ib-muted"), txt));
      } else chips(box, d);
      bar.appendChild(box);
      var more = el("button", "ib-more", panelOpen ? "close ▴" : "more ▾");
      more.setAttribute("aria-expanded", String(panelOpen));
      more.addEventListener("click", function (e) { e.stopPropagation(); panelOpen = !panelOpen; render(); });
      bar.appendChild(more);
      if (panelOpen) bar.appendChild(panel(d));
    } catch (e) {}
  }

  function toggleMini() {
    mini = !mini;
    panelOpen = false;
    try { localStorage.setItem(KEY, mini ? "1" : "0"); } catch (e) {}
    render();
  }

  function load() {
    try {
      fetch(DATA, { cache: "no-store", credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { data = j && Array.isArray(j.indexers) ? j : null; render(); })
        .catch(function () { data = null; render(); });
    } catch (e) {}
  }

  function mount() {
    if (!document.body) return;
    if (!bar) {
      bar = el("div");
      bar.id = "indexer-bar";
      bar.setAttribute("role", "region");
      bar.setAttribute("aria-label", "Indexer usage");
      render();
    }
    if (!bar.isConnected) document.body.appendChild(bar);
  }

  function start() {
    try {
      mount();
      load();
      setInterval(load, REFRESH);
      new MutationObserver(function () { if (!bar.isConnected) mount(); }).observe(document.body, { childList: true });
      document.addEventListener("click", function () { if (panelOpen) { panelOpen = false; render(); } });
      document.addEventListener("keydown", function (e) { if (e.key === "Escape" && panelOpen) { panelOpen = false; render(); } });
      document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
    } catch (e) {}
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
