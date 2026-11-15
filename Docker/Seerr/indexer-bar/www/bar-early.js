// Shared bar (see bar.js), loaded render-blocking before it: reserves the bar's strip before the first paint so the page does not drop when the bar arrives; bar.js takes it away again without a session.
(function () {
  "use strict";
  try {
    var tag = document.currentScript || document.querySelector("script[data-ib-no-bar]");
    var noBar = tag && tag.getAttribute("data-ib-no-bar"), root = document.documentElement;
    // Seerr's /manual: its 404 stays hidden (menu-links.css) until manual.js has put our page in
    if (location.pathname.replace(/\/+$/, "") === "/manual") root.setAttribute("data-hg-early", "manual");
    if (noBar && new RegExp(noBar).test(location.pathname)) return;
    root.classList.add("ib-on");
    try { if (localStorage.getItem("indexer-bar-mini") === "1") root.classList.add("ib-mini"); } catch (e) {}
  } catch (e) {}
})();
