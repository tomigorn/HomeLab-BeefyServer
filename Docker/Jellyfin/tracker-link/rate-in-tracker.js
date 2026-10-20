(function () {
  var BASE = "https://tracker.holy-grail.ch/go/tmdb/";
  var WAIT_MS = 10000;

  function tmdbOf(item) {
    var id = item && item.ProviderIds && item.ProviderIds.Tmdb;
    return id && /^[0-9]+$/.test(String(id)) ? String(id) : null;
  }

  function link(kind, item) {
    var id = tmdbOf(item);
    if (!id) return null;
    return BASE + kind + "/" + id + "?title=" + encodeURIComponent(item.Name || "");
  }

  function trackerUrl(item, series) {
    if (!item) return null;
    if (item.Type === "Movie") return link("movie", item);
    if (item.Type === "Series") return link("show", item);
    if (item.Type === "Episode" || item.Type === "Season") return series ? link("show", series) : null;
    return null;
  }

  function ensure(box, url) {
    var old = box.querySelector("[data-tracker-link]");
    if (old && old.getAttribute("href") === url) {
      if (box.classList.contains("hide")) box.classList.remove("hide");
      return;
    }
    box.querySelectorAll("[data-tracker-link], [data-tracker-sep]").forEach(function (el) { el.remove(); });
    if (!url) return;
    if (box.querySelector("a")) {
      var sep = document.createElement("span");
      sep.setAttribute("data-tracker-sep", "");
      sep.textContent = ", ";
      box.appendChild(sep);
    }
    var a = document.createElement("a");
    a.setAttribute("is", "emby-linkbutton");
    a.className = "button-link";
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.setAttribute("data-tracker-link", "");
    a.textContent = "Rate in Tracker";
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
        attach(page, trackerUrl(item, series));
      });
    }).catch(function () {});
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { trackerUrl: trackerUrl };
  } else {
    window.trackerUrl = trackerUrl;
    document.addEventListener("viewshow", onViewShow);
  }
})();
