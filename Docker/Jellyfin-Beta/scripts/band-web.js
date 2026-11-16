// Beta only: the web "Not downloaded yet" band (popular.js .hssTopBand) in each poster's complement colour; colours computed on the server (band_render.py) in window.HssBandColours = {"/tmdbPosterPath.jpg": ["#bg", "#fg"]}.
(function () {
  "use strict";
  function apply(root) {
    var map = window.HssBandColours || {};
    var bands = root.querySelectorAll ? root.querySelectorAll(".hssTopBand:not([data-hss-band])") : [];
    for (var i = 0; i < bands.length; i++) {
      var b = bands[i], p = b.parentNode, m = /\/t\/p\/[a-z0-9]+(\/[A-Za-z0-9_.-]+)/.exec((p && p.style && p.style.backgroundImage) || "");
      var c = m && map[m[1]];
      if (!c) continue;
      b.setAttribute("data-hss-band", "1");
      p.setAttribute("data-hss-banded", "1");
      b.style.background = c[0];
      b.style.borderTop = "calc(var(--hss-p) * 0.0067) solid " + (c[1] === "#000000" ? "rgba(0,0,0,.45)" : "rgba(255,255,255,.55)");
      var t = b.querySelector(".hssTopBandText"), ic = b.querySelector(".hssTopBandIcon");
      if (t) t.style.color = c[1];
      if (ic) { ic.style.background = c[1]; ic.style.setProperty("--hss-band-arrow", c[0]); }
    }
  }
  var css = document.createElement("style");
  // A dimmed card (release found) dims its whole poster with a filter, the band too; here only the picture is darkened, the band stays at full colour.
  css.textContent = [
    ".hssTopBand[data-hss-band] .hssTopBandIcon::after { background: var(--hss-band-arrow, #101014) !important; }",
    ".hssTopBand[data-hss-band] { z-index: 2; }",
    ".hssTopDim .hssTopPoster[data-hss-banded] { filter: none; }",
    ".hssTopDim .hssTopPoster[data-hss-banded]::after { content: ''; position: absolute; inset: 0; z-index: 1; background: rgba(0, 0, 0, 0.4); pointer-events: none; }",
    ".hssTopDim:hover .hssTopPoster[data-hss-banded]::after, .hssTopDim:focus-within .hssTopPoster[data-hss-banded]::after { background: rgba(0, 0, 0, 0.15); }"
  ].join("\n");
  document.head.appendChild(css);
  var queued = false;
  new MutationObserver(function () {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; apply(document); });
  }).observe(document.documentElement, { childList: true, subtree: true });
  apply(document);
})();
