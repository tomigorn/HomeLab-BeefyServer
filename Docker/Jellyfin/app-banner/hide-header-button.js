(function () {
  // Moonbase adds an "Open Moonfin" button to the web header and puts it back when removed, so it is hidden with CSS.
  var style = document.createElement("style");
  style.textContent = ".headerMoonfinButton{display:none!important}";
  (document.head || document.documentElement).appendChild(style);
})();
