// Beta only: the planned home rows (rows.py) on the classic web home, after the Home Screen Sections rows (Continue Watching, Next Up).
// window.HssBetaRows = [{key, name, kind}] in this month's order: favourites and new (last 30 days) are queried directly, every other row is the user's own collection tagged beta-row-<key> (per-user ones: only the owner may see theirs).
(function () {
  "use strict";
  var CSS = [
    ".betaRows { order: 9999; }",
    ".betaRow { margin: 0 0 1.6em; }",
    ".betaRow h2 { margin: 0 0 .4em 3.3%; font-size: 1.4em; font-weight: 500; }",
    ".betaRowScroll { display: flex; gap: .9em; overflow-x: auto; padding: 0 3.3% .4em; scroll-snap-type: x proximity; }",
    ".betaCard { flex: 0 0 auto; width: clamp(7.5rem, 12vw, 13rem); color: inherit; text-decoration: none; scroll-snap-align: start; }",
    ".betaCard img { display: block; width: 100%; aspect-ratio: 2 / 3; object-fit: cover; border-radius: .3em; background: #222; }",
    ".betaCard span { display: block; margin-top: .35em; font-size: .9em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: center; }",
    ".betaCard small { display: block; text-align: center; opacity: .6; font-size: .8em; }"
  ].join("\n");

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function card(api, item) {
    var a = el("a", "betaCard");
    a.href = "#/details?id=" + item.Id + "&serverId=" + api.serverId();
    var img = el("img");
    img.loading = "lazy";
    img.alt = "";
    var tag = item.ImageTags && item.ImageTags.Primary;
    if (tag) img.src = api.getUrl("Items/" + item.Id + "/Images/Primary", { fillHeight: 450, fillWidth: 300, quality: 90, tag: tag });
    a.appendChild(img);
    a.appendChild(el("span", "", item.Name));
    var year = item.ProductionYear ? String(item.ProductionYear) : "";
    a.appendChild(el("small", "", year));
    return a;
  }

  function section(spec, items) {
    if (!items.length) return null;
    var sec = el("div", "betaRow verticalSection");
    sec.setAttribute("data-beta-row", spec.key);
    sec.appendChild(el("h2", "sectionTitle", spec.name));
    var sc = el("div", "betaRowScroll");
    items.forEach(function (i) { sc.appendChild(card(window.ApiClient, i)); });
    sec.appendChild(sc);
    return sec;
  }

  function rowFor(api, user, spec) {
    var base = { userId: user, Recursive: true, IncludeItemTypes: "Movie,Series", Fields: "PrimaryImageAspectRatio,DateCreated", Limit: 30, EnableUserData: false };
    if (spec.kind === "favourites") {
      return api.getJSON(api.getUrl("Items", Object.assign({}, base, { Filters: "IsFavorite", SortBy: "SortName" }))).then(function (r) { return section(spec, r.Items || []); });
    }
    if (spec.kind === "new") {
      var since = Date.now() - 30 * 86400000;
      return api.getJSON(api.getUrl("Items", Object.assign({}, base, { SortBy: "DateCreated", SortOrder: "Descending", Limit: 60 }))).then(function (r) {
        return section(spec, (r.Items || []).filter(function (i) { return Date.parse(i.DateCreated) >= since; }).slice(0, 30));
      });
    }
    var q = { userId: user, Recursive: true, IncludeItemTypes: "BoxSet", Tags: "beta-row-" + spec.key, Fields: "Tags", Limit: 5 };
    return api.getJSON(api.getUrl("Items", q)).then(function (r) {
      var sets = (r.Items || []);
      if (!sets.length) return null;
      return api.getJSON(api.getUrl("Items", { userId: user, ParentId: sets[0].Id, Fields: "PrimaryImageAspectRatio", Limit: 30, EnableUserData: false })).then(function (c) {
        return section(spec, c.Items || []);
      });
    });
  }

  var busy = false;
  function render() {
    var api = window.ApiClient, rows = window.HssBetaRows;
    var page = document.querySelector("#indexPage:not(.hide) .homeSectionsContainer, .homePage:not(.hide) .homeSectionsContainer");
    var have = page && page.querySelector(".betaRows");
    // Home Screen Sections draws its rows (1-3) after ours arrive: keep ours last.
    if (have && have.nextElementSibling) page.appendChild(have);
    if (busy || !api || !rows || !page || have) return;
    var user = api.getCurrentUserId && api.getCurrentUserId();
    if (!user) return;
    busy = true;
    var box = el("div", "betaRows");
    page.appendChild(box);
    Promise.all(rows.map(function (spec) { return rowFor(api, user, spec).catch(function () { return null; }); })).then(function (secs) {
      secs.forEach(function (s) { if (s) box.appendChild(s); });
    }).then(function () { busy = false; }, function () { busy = false; });
  }

  var style = el("style");
  style.textContent = CSS;
  document.head.appendChild(style);
  var queued = false;
  new MutationObserver(function () {
    if (queued) return;
    queued = true;
    setTimeout(function () { queued = false; render(); }, 300);
  }).observe(document.documentElement, { childList: true, subtree: true });
  render();
})();
