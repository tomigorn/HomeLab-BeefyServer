(function () {
  var BASE = "https://report.holy-grail.ch/";
  var WAIT_MS = 10000;

  function num(v) {
    return v !== undefined && v !== null && /^[0-9]{1,10}$/.test(String(v)) ? String(v) : null;
  }

  function ids(item) {
    var p = (item && item.ProviderIds) || {};
    return { tmdb: num(p.Tmdb), tvdb: num(p.Tvdb) };
  }

  function reportUrl(item, series) {
    if (!item) return null;
    var q = new URLSearchParams();
    if (item.Type === "Movie") {
      var m = ids(item);
      if (!m.tmdb) return null;
      q.set("type", "movie");
      q.set("tmdb", m.tmdb);
      return BASE + "?" + q.toString();
    }
    var show = item.Type === "Series" ? item : (item.Type === "Episode" || item.Type === "Season") ? series : null;
    var s = ids(show);
    if (!s.tmdb && !s.tvdb) return null;
    q.set("type", "tv");
    if (s.tvdb) q.set("tvdb", s.tvdb);
    if (s.tmdb) q.set("tmdb", s.tmdb);
    if (item.Type === "Episode" && num(item.ParentIndexNumber) && num(item.IndexNumber)) {
      q.set("season", num(item.ParentIndexNumber));
      q.set("episode", num(item.IndexNumber));
    }
    return BASE + "?" + q.toString();
  }

  function label(lang) {
    return String(lang || "").toLowerCase().indexOf("de") === 0 ? "⚠ Problem melden" : "⚠ Report a problem";
  }

  function ensure(box, url) {
    var old = box.querySelector("[data-report-link]");
    if (old && old.getAttribute("href") === url) {
      if (box.classList.contains("hide")) box.classList.remove("hide");
      return;
    }
    box.querySelectorAll("[data-report-link], [data-report-sep]").forEach(function (el) { el.remove(); });
    if (!url) return;
    if (box.querySelector("a")) {
      var sep = document.createElement("span");
      sep.setAttribute("data-report-sep", "");
      sep.textContent = ", ";
      box.appendChild(sep);
    }
    var a = document.createElement("a");
    a.setAttribute("is", "emby-linkbutton");
    a.className = "button-link";
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.setAttribute("data-report-link", "");
    a.textContent = label(document.documentElement.lang || navigator.language);
    box.appendChild(a);
    if (box.classList.contains("hide")) box.classList.remove("hide");
  }

  var observer = null;

  function attach(page, url) {
    if (observer) observer.disconnect();
    var stop = Date.now() + WAIT_MS;
    function run() {
      if (Date.now() > stop) { observer.disconnect(); return; }
      var box = page.querySelector(".itemExternalLinks");
      if (box) ensure(box, url);
    }
    observer = new MutationObserver(run);
    observer.observe(page, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
    setTimeout(function () { observer.disconnect(); }, WAIT_MS);
    run();
  }

  function onViewShow(e) {
    var hash = window.location.hash || "";
    if (hash.indexOf("#/details") !== 0) return;
    var id = new URLSearchParams(hash.split("?")[1] || "").get("id");
    var api = window.ApiClient;
    if (!id || !api) return;
    var page = e.target && e.target.querySelector ? e.target : document;
    var userId = api.getCurrentUserId();
    api.getItem(userId, id).then(function (item) {
      var needsSeries = (item.Type === "Episode" || item.Type === "Season") && item.SeriesId;
      return (needsSeries ? api.getItem(userId, item.SeriesId) : Promise.resolve(null)).then(function (series) {
        attach(page, reportUrl(item, series));
      });
    }).catch(function () {});
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { reportUrl: reportUrl, label: label };
  } else {
    document.addEventListener("viewshow", onViewShow);
  }
})();
