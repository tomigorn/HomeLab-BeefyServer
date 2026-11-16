// Shared bar, one codebase: canonical in HomeLab-BeefyServer Docker/Seerr/indexer-bar/www/, vendored unchanged in HomeLab-FastPi Docker/Audiobook-Requests/books-proxy/www/bar/ (scripts/sync-bar.sh). Config and data format: indexer-bar/schema.md.
(function () {
  "use strict";
  if (window.__indexerBar) return;
  var tag = document.currentScript || document.querySelector("script[data-ib-data]");
  var cfg = (tag && tag.dataset) || {};
  if (!cfg.ibData) return;
  window.__indexerBar = true;

  var DATA = cfg.ibData, QUOTA = cfg.ibQuota || null, REFRESH = 60000, STALE = 10 * 60000, KEY = "indexer-bar-mini";
  var RANK = { ok: 0, yellow: 1, warn: 2, orange: 3, full: 4 };
  var bar, data = null, quota = null, panelOpen = false, mini = false, authed = null, lastLoad = 0, RETRY = 4000;
  var NO_BAR = cfg.ibNoBar ? new RegExp(cfg.ibNoBar) : null;

  try { mini = localStorage.getItem(KEY) === "1"; } catch (e) {}

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function add(parent) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]); return parent; }
  // README "Hint boxes"; a page without tip.js gets no hints (aria-labels stay)
  function tip(n, content, opts) { if (window.hgTip) window.hgTip.set(n, content, opts); return n; }
  function hinted(n, content) { n.tabIndex = 0; return tip(n, content); }
  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
  // "A. B; c." → ["A", "B", "C"]
  function sentences(s) { return s ? s.split(/[.;]\s+/).map(function (x) { return cap(x.trim().replace(/\.$/, "")); }).filter(Boolean) : []; }
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
    add(s, document.createTextNode((m.approx ? "≈" : "") + num(m.used) + "/" + num(m.limit)), el("span", "ib-word", " " + word));
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

  var TEXT = {
    en: { free: "free", used: "Used:", freeL: "Free:", toLimit: function (r) { return "Free until the limit (" + r + " %):"; }, reached: "limit reached", head: "Storage", quota: "My requests", frees: "next free ", days: function (n) { return " in " + n + " days"; },
      quotaNote: function (q) { return q.limit + " requests per person in " + q.days + " days, deleted requests count too."; },
      quotaRows: function (q) { return [q.limit + " requests per person in " + q.days + " days", "Deleted requests count too"]; } },
    de: { free: "frei", used: "Belegt:", freeL: "Frei:", toLimit: function (r) { return "Frei bis zur Grenze (" + r + " %):"; }, reached: "Grenze erreicht", head: "Speicher", quota: "Meine Anfragen", frees: "wieder frei ", days: function (n) { return " in " + n + " Tagen"; },
      quotaNote: function (q) { return q.limit + " Anfragen pro Person in " + q.days + " Tagen, gelöschte Anfragen zählen mit."; },
      quotaRows: function (q) { return [q.limit + " Anfragen pro Person in " + q.days + " Tagen", "Gelöschte Anfragen zählen mit"]; } }
  };
  var PAID = {
    en: { life: "Paid account (lifetime)", plain: "Paid account", until: "Paid subscription until ", ended: "Paid subscription, ended ", locale: "en-GB" },
    de: { life: "Bezahltes Konto (lebenslang)", plain: "Bezahltes Konto", until: "Bezahltes Abo bis ", ended: "Bezahltes Abo, beendet am ", locale: "de-CH" }
  };
  function paidText(p, l) {
    var t = PAID[l];
    if (p.lifetime) return t.life;
    if (!p.expires) return t.plain;
    var d = p.expires.split("-"), date = new Date(+d[0], +d[1] - 1, +d[2]).toLocaleDateString(t.locale, { day: "numeric", month: "short", year: "numeric" });
    return (p.expired ? t.ended : t.until) + date;
  }
  // Name with a ★ for paid accounts and subscriptions (usage.json "premium").
  function named(tag, r) {
    var n = el(tag, "ib-name", r.name);
    if (!r.premium) return n;
    var text = paidText(r.premium, lang()), star = el("span", "ib-star", "★");
    tip(star, text);
    star.setAttribute("aria-label", text);
    star.setAttribute("role", "img");
    star.tabIndex = 0;
    return add(n, star);
  }
  function tr(v, l) { return v == null ? "" : typeof v === "string" ? v : v[l] || v.en || ""; }
  function lang() {
    var hook = cfg.ibLangHook && window[cfg.ibLangHook];
    if (hook && hook.lang) { try { return hook.lang() === "de" ? "de" : "en"; } catch (e) { /* own detection */ } }
    var probe = cfg.ibLangProbe && document.querySelector(cfg.ibLangProbe);
    if (probe && cfg.ibLangDe) return probe.textContent.indexOf(cfg.ibLangDe) >= 0 ? "de" : "en";
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
  function diskName(d, l) { return tr(d.label, l) || d.name; }
  function isPct(v) { return typeof v === "number" && v > 0 && v < 100; }
  function toLimit(d) {
    if (typeof d.freeToLimit === "number") return Math.max(0, d.freeToLimit);
    return Math.max(0, (d.total * d.red) / 100 - d.used);
  }
  function diskHint(d, l) {
    var sub = tr(d.sub, l), t = TEXT[l];
    var rows = [[t.used, diskUsed(d, l) + " · " + Math.round(d.pct) + " %"], [t.freeL, bytes(d.free, l)]], left;
    if (isPct(d.red)) rows.push([t.toLimit(d.red), (left = toLimit(d)) > 0 ? bytes(left, l) : t.reached]);
    if (Array.isArray(d.parts) && d.parts.length > 1) rows.push([d.parts.length + (d.partsLabel ? " " + d.partsLabel : "") + ":", d.parts.map(function (p) { return Math.round(p) + " %"; }).join(" · ")]);
    return { title: diskName(d, l) + (sub ? " — " + sub : ""), rows: rows, sections: [{ rows: sentences(tr(d.hint, l)) }] };
  }
  function diskMeter(d) {
    var box = el("span", "ib-meter ib-" + d.level), fill = el("span", "ib-fill");
    fill.style.width = Math.min(100, d.pct) + "%";
    fill.style.display = "block";
    add(box, fill);
    [[d.yellow, "ib-mark"], [d.red, "ib-mark ib-mark-limit"]].forEach(function (m) {
      if (!isPct(m[0])) return;
      var k = el("span", m[1]);
      k.style.left = m[0] + "%";
      k.setAttribute("aria-hidden", "true");
      box.appendChild(k);
    });
    return box;
  }
  function disks(d) { return d && Array.isArray(d.storage) ? d.storage : []; }
  function diskChips(box, d) {
    var l = lang();
    disks(d).forEach(function (x) {
      var c = add(el("span", "ib-chip"), el("span", "ib-name", diskName(x, l)), diskMeter(x),
        el("span", x.level === "ok" ? "" : "ib-" + x.level + "-text", diskUsed(x, l)), el("span", "ib-muted", Math.round(x.pct) + " %"));
      c.setAttribute("data-ib-disk", x.id || x.name);
      hinted(c, diskHint(x, l));
      box.appendChild(c);
    });
  }
  function diskPanel(p, d) {
    var list = disks(d), l = lang(), t = TEXT[l];
    if (!list.length) return;
    var tbl = el("table");
    list.forEach(function (x) {
      var rule = tr(x.hint, l);
      add(tbl, add(el("tr"), add(el("td"), el("div", "ib-name", diskName(x, l)), el("div", "ib-muted", tr(x.sub, l))),
        add(el("td"), add(el("div", "ib-line"), diskMeter(x), el("span", x.level === "ok" ? "" : "ib-" + x.level + "-text", diskUsed(x, l) + " · " + Math.round(x.pct) + " %")),
          add(el("div", "ib-line"), el("span", "ib-muted", bytes(x.free, l) + " " + t.free)))));
      if (rule) { var rc = el("td", "ib-muted ib-rule", rule); rc.colSpan = 2; add(tbl, add(el("tr"), rc)); }
    });
    add(p, el("h4", null, t.head), tbl);
  }

  var SUB_WINDOW = "per day (00:00 UTC)", SUB_APPROX = "≈ = estimated from Bazarr's searches.";
  function subRows(d) { return d && Array.isArray(d.subtitles) ? d.subtitles : []; }
  function subMetric(m, word) {
    if (!m) return [];
    if (m.limit != null) return [meter(m), metricText(m, word)];
    return m.used ? [add(el("span", "ib-muted"), document.createTextNode(num(m.used)), el("span", "ib-word", " " + word))] : [];
  }
  function subState(r) { return r.status === "paused" && r.statusText ? el("span", "ib-full-text", r.statusText) : resetText(r); }
  function subParts(r) { return subMetric(r.searches, "searches").concat(subMetric(r.downloads, "downloads"), [subState(r)]); }
  function subCell(r) {
    var cell = el("td"), st = subState(r);
    [subMetric(r.searches, "searches"), subMetric(r.downloads, "downloads")].forEach(function (parts) {
      if (parts.length) cell.appendChild(add.apply(null, [el("div", "ib-line")].concat(parts)));
    });
    if (st) add(cell, add(el("div", "ib-line"), st));
    return cell;
  }
  function subHint(r) {
    var rows = ["Subtitle provider"], approx = false;
    if (r.premium) rows.push("★ " + paidText(r.premium, lang()));
    [["searches", "Searches:"], ["downloads", "Downloads:"]].forEach(function (k) {
      var m = r[k[0]];
      if (!m || (m.limit == null && !m.used)) return;
      approx = approx || !!m.approx;
      rows.push([k[1], (m.approx ? "≈" : "") + num(m.used) + (m.limit != null ? "/" + num(m.limit) : "")]);
    });
    if (r.status === "paused" && r.statusText) rows.push(cap(r.statusText));
    rows.push(["Counted:", SUB_WINDOW]);
    return { title: r.name, rows: rows, sections: approx ? [{ rows: [SUB_APPROX] }] : [] };
  }
  function subChips(box, d) {
    if (d && d.subtitlesOk === false) add(box, el("span", "ib-chip ib-muted", "Subtitle usage unavailable"));
    subRows(d).forEach(function (r) {
      var c = add.apply(null, [el("span", "ib-chip"), named("span", r)].concat(subParts(r)));
      c.setAttribute("data-ib-subtitle", r.id || r.name);
      hinted(c, subHint(r));
      box.appendChild(c);
    });
  }
  function subPanel(p, d) {
    var rows = subRows(d);
    if (!rows.length && !(d && d.subtitlesOk === false)) return;
    if (!rows.length) { add(p, el("h4", null, "Subtitles"), el("p", "ib-muted", "Usage unavailable right now (Bazarr not reachable).")); return; }
    var t = el("table"), approx = false;
    rows.forEach(function (r) {
      approx = approx || ["searches", "downloads"].some(function (k) { return r[k] && r[k].approx; });
      add(t, add(el("tr"), add(el("td"), named("div", r), el("div", "ib-muted", SUB_WINDOW)),
        subCell(r)));
    });
    add(p, el("h4", null, "Subtitles"), t);
    if (approx) add(p, el("p", "ib-note", SUB_APPROX));
  }

  function quotaLevel(q) { return q.used >= q.limit ? "full" : q.used >= q.limit - 1 ? "warn" : "ok"; }
  function quotaParts(q, l) {
    var m = { used: q.used, limit: q.limit, level: quotaLevel(q) }, t = TEXT[l];
    var s = add(el("span", m.level === "ok" ? "" : "ib-" + m.level + "-text"), document.createTextNode(q.used + "/" + q.limit), el("span", "ib-word", t.days(q.days)));
    return [meter(m), s, m.level === "full" && q.freesAt ? el("span", "ib-full-text", t.frees + at(q.freesAt)) : null];
  }
  function quotaChip(box) {
    if (!quota) return;
    var l = lang(), c = add.apply(null, [el("span", "ib-chip"), el("span", "ib-name", TEXT[l].quota)].concat(quotaParts(quota, l)));
    c.setAttribute("data-ib-quota", "");
    hinted(c, { title: TEXT[l].quota, rows: TEXT[l].quotaRows(quota) });
    box.appendChild(c);
  }
  function quotaPanel(p) {
    if (!quota) return;
    var l = lang(), t = TEXT[l];
    add(p, el("h4", null, t.quota), add(el("table"), add(el("tr"), add(el("td"), el("div", "ib-muted", t.quotaNote(quota))),
      add(el("td"), add.apply(null, [el("div", "ib-line")].concat(quotaParts(quota, l)))))));
  }

  function chips(box, d) {
    var rows = d && d.ok ? d.indexers.filter(function (r) { return r.limited; }) : [];
    if (!d || !d.ok) add(box, el("span", "ib-chip ib-muted", "Indexer usage unavailable"));
    rows.forEach(function (r) {
      var c = add(el("span", "ib-chip"), named("span", r));
      [["searches", "searches"], ["downloads", "downloads"]].forEach(function (k) {
        var m = r[k[0]];
        if (m.limit == null) return;
        add(c, meter(m), metricText(m, k[1]));
      });
      add(c, resetText(r));
      box.appendChild(c);
    });
    (d && d.ok && d.showUnlimited ? d.indexers.filter(function (r) { return !r.limited; }) : []).forEach(function (r) {
      var c = add(el("span", "ib-chip"), named("span", r));
      [["searches", "searches"], ["downloads", "downloads"]].forEach(function (k) {
        add(c, add(el("span", "ib-muted"), document.createTextNode(num(r[k[0]].used) + "/∞"), el("span", "ib-word", " " + k[1])));
      });
      c.setAttribute("data-ib-indexer", r.name);
      box.appendChild(c);
    });
    subChips(box, d);
    quotaChip(box);
    var subs = d ? d.subscriptions || [] : [];
    var due = subs.filter(function (s) { return s.level !== "ok"; });
    due.forEach(function (s) {
      add(box, add(el("span", "ib-chip"), el("span", "ib-dot ib-" + s.level), named("span", s),
        el("span", "ib-" + s.level + "-text", subText(s))));
    });
    if (!due.length) {
      var next = subs.filter(function (s) { return !s.lifetime; }).sort(function (a, b) { return a.daysLeft - b.daysLeft; })[0];
      if (next) add(box, add(el("span", "ib-chip ib-muted"), el("span", "ib-label", "Next renewal: "),
        document.createTextNode(next.name + " in " + next.daysLeft + " d")));
    }
    diskChips(box, d);
  }

  var KINDS = {
    indexer: { en: "Indexers", de: "Indexer" },
    "usenet provider": { en: "Usenet", de: "Usenet" },
    vpn: { en: "VPN", de: "VPN" },
    "": { en: "Other", de: "Weitere" }
  };
  function ends(x) { return x.lifetime || typeof x.daysLeft !== "number" ? Infinity : x.daysLeft; }
  function bySoonest(a, b) { return ends(a) - ends(b) || a.name.localeCompare(b.name); }
  // Groups per kind, soonest end first inside and across groups; lifetime entries and lifetime-only groups last.
  function subGroups(subs, l) {
    var map = {}, list = [];
    subs.forEach(function (x) {
      var key = String(x.kind || "").trim(), id = key.toLowerCase();
      if (!map[id]) list.push(map[id] = { head: KINDS[id] ? KINDS[id][l] : key, items: [] });
      map[id].items.push(x);
    });
    list.forEach(function (g) { g.items.sort(bySoonest); });
    return list.sort(function (a, b) { return ends(a.items[0]) - ends(b.items[0]) || a.head.localeCompare(b.head); });
  }
  function panel(d) {
    var p = el("div", "ib-panel"), l = lang();
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
          var name = add(el("td"), named("div", r), el("div", "ib-muted", r.window === "day" ? "per day (midnight)" : "last 24 h"));
          add(t, add(el("tr"), name, cell));
        });
        add(p, el("h4", null, "Limited indexers"), t);
      }
      if (free.length) {
        var u = el("table");
        free.forEach(function (r) {
          add(u, add(el("tr"), named("td", r),
            el("td", "ib-muted", num(r.searches.used) + " searches · " + num(r.downloads.used) + " downloads · ∞")));
        });
        add(p, el("h4", null, "Unlimited (last 24 h)"), u);
      }
    }
    subPanel(p, d);
    var subs = d ? d.subscriptions || [] : [];
    if (subs.length) {
      add(p, el("h4", null, "Subscriptions"));
      subGroups(subs, l).forEach(function (g) {
        var s = el("table");
        g.items.forEach(function (x) {
          add(s, add(el("tr"), named("td", x),
            add(el("td"), add(el("div", "ib-line"), x.lifetime ? null : el("span", "ib-dot ib-" + x.level),
              el("span", x.lifetime ? "ib-muted" : "ib-" + x.level + "-text", subText(x))))));
        });
        add(p, add(el("div", "ib-subs-group"), el("h5", "ib-subs-kind", g.head), s));
      });
    }
    diskPanel(p, d);
    quotaPanel(p);
    if (d && d.note) add(p, el("p", "ib-note", tr(d.note, l)));
    if (d && d.generated) add(p, el("p", "ib-note", "Updated " + at(d.generated)));
    return p;
  }

  function render() {
    try {
      if (!bar) return;
      var d = usable(), root = document.documentElement;
      if (window.hgTip && bar.querySelector("[aria-describedby='hg-tip']")) window.hgTip.hide();
      root.classList.add("ib-on");
      root.classList.toggle("ib-mini", mini);
      bar.textContent = "";
      var rows = d && d.ok ? d.indexers.filter(function (r) { return r.limited; }) : [];
      var level = d ? worst(rows.concat(subRows(d), d.subscriptions || [], disks(d), quota ? [{ level: quotaLevel(quota) }] : [])) : null;
      var title = add(el("button", "ib-title"), el("span", "ib-dot" + (level ? " ib-" + level : "")), el("span", null, "Indexers"));
      tip(title, mini ? "Show indexer usage" : "Hide indexer usage", { tap: false });
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

  function visible() { return authed && !(NO_BAR && NO_BAR.test(location.pathname)); }

  function sync() {
    if (visible()) { mount(); render(); return; }
    // first answer pending (Next's replaceState at start): keep the strip bar-early.js reserved
    if (authed === null && !(NO_BAR && NO_BAR.test(location.pathname))) return;
    if (bar && bar.isConnected) bar.parentNode.removeChild(bar);
    document.documentElement.classList.remove("ib-on", "ib-mini");
  }

  function get(url) {
    return fetch(url, { cache: "no-store", credentials: "same-origin", redirect: "manual" });
  }
  // The data needs a login (401/403 or a login redirect without): the bar only exists once it was served.
  function load() {
    lastLoad = Date.now();
    try {
      get(DATA)
        .then(function (r) {
          if (r.status === 401 || r.status === 403 || r.type === "opaqueredirect" || (r.status >= 300 && r.status < 400)) { authed = false; data = null; return null; }
          if (r.ok) return r.json().then(function (j) { return { j: j }; });
          if (authed) return { j: null };
          authed = false;
          return null;
        })
        .then(function (got) {
          if (!got) return sync();
          var j = got.j;
          // authed only together with the data: a navigation during the first load must not draw "usage unavailable"
          if (!QUOTA) { data = j && Array.isArray(j.indexers) ? j : null; authed = true; quota = null; return sync(); }
          return get(QUOTA).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }).then(function (q) {
            data = j && Array.isArray(j.indexers) ? j : null;
            authed = true;
            quota = q && typeof q.used === "number" && typeof q.limit === "number" ? q : null;
            sync();
          });
        })
        .catch(function () { data = null; if (authed === null) authed = false; sync(); });
    } catch (e) {}
  }

  function navigated() { if (!authed) load();
    sync(); }

  function hookHistory() {
    ["pushState", "replaceState"].forEach(function (m) {
      var orig = history[m];
      if (typeof orig !== "function") return;
      history[m] = function () { var r = orig.apply(this, arguments); setTimeout(navigated, 0); return r; };
    });
    window.addEventListener("popstate", navigated);
  }

  function mount() {
    if (!document.body || !visible()) return;
    if (!bar) {
      bar = el("div");
      bar.id = "indexer-bar";
      bar.setAttribute("role", "region");
      bar.setAttribute("aria-label", "Indexer usage");
    }
    if (!bar.isConnected) document.body.appendChild(bar);
  }

  function start() {
    try {
      load();
      hookHistory();
      setInterval(function () { if (Date.now() - lastLoad >= (authed ? REFRESH : RETRY) - 500) load(); }, RETRY);
      new MutationObserver(function () { if (authed && (!bar || !bar.isConnected)) sync(); }).observe(document.body, { childList: true });
      document.addEventListener("click", function () { if (panelOpen) { panelOpen = false; render(); } });
      document.addEventListener("keydown", function (e) { if (e.key === "Escape" && panelOpen) { panelOpen = false; render(); } });
      document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
    } catch (e) {}
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
