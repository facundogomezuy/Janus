// Tema: oscuro (default), claro o el del sistema. Se aplica antes de pintar.

const KEY = "janus.theme";
const media = window.matchMedia("(prefers-color-scheme: light)");

export function getTheme() {
  try { return localStorage.getItem(KEY) || "dark"; } catch { return "dark"; }
}

function resolved(t) {
  if (t === "system") return media.matches ? "light" : "dark";
  return t === "light" ? "light" : "dark";
}

export function applyTheme() {
  document.documentElement.dataset.theme = resolved(getTheme());
}

export function setTheme(t) {
  try { localStorage.setItem(KEY, t); } catch { /* */ }
  applyTheme();
}

export function toggleTheme() {
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

media.addEventListener("change", () => { if (getTheme() === "system") applyTheme(); });
applyTheme();
