// Se carga sincrónico en <head>: aplica el tema guardado antes del primer pintado.
(function () {
  var t = "dark";
  try { t = localStorage.getItem("janus.theme") || "dark"; } catch (e) { /* sin storage */ }
  if (t === "system") t = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t === "light" ? "light" : "dark");
})();
