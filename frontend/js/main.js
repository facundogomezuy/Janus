// Janus — arranque del frontend.

import { Api, resolveBackend } from "./api.js";
import { $, fmtInt, h, icon, logoMark, sleep, toast } from "./dom.js";
import { createEventsDrawer, openSettings } from "./panels.js";
import { toggleTheme } from "./theme.js";
import { createHistory } from "./views/history.js";
import { createIntercept } from "./views/intercept.js";
import { createRepeater } from "./views/repeater.js";
import { createScope } from "./views/scope.js";
import { createSetup } from "./views/setup.js";

const T = window.__TAURI__;
const tauriWindow = T?.window?.getCurrentWindow?.();

const NAV = [
  { id: "history", label: "History", icon: "history", key: "1" },
  { id: "intercept", label: "Intercept", icon: "hand", key: "2" },
  { id: "repeater", label: "Repeater", icon: "repeat", key: "3" },
  { id: "scope", label: "Scope", icon: "target", key: "4" },
  { id: "setup", label: "Setup", icon: "shield", key: "5" },
];

// --- bus de eventos + contexto compartido con las vistas -------------------------------
const listeners = new Map();
const app = {
  api: null,
  version: "",
  status: null,
  settings: {},
  proxy: {},
  intercept: { enabled: false, pending: 0 },
  ca: null,
  sysproxy: null,
  browsers: [],
  views: {},
  current: null,
  on(type, fn) {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(fn);
  },
  emit(type, data) {
    for (const fn of listeners.get(type) || []) {
      try { fn(data); } catch (e) { console.error(`handler de ${type}`, e); }
    }
  },
};

// --- controles de ventana (Tauri, ventana sin marco) -------------------------------------------
function windowControls() {
  if (!tauriWindow) return null;
  const maxBtn = h("button.win-btn", { title: "Maximizar", onclick: () => tauriWindow.toggleMaximize() }, icon("win-max"));
  const box = h("div.win-controls",
    h("button.win-btn", { title: "Minimizar", onclick: () => tauriWindow.minimize() }, icon("win-min")),
    maxBtn,
    h("button.win-btn.close", { title: "Cerrar", onclick: () => tauriWindow.close() }, icon("win-close")));
  const sync = async () => {
    try {
      const max = await tauriWindow.isMaximized();
      maxBtn.replaceChildren(icon(max ? "win-restore" : "win-max"));
      maxBtn.title = max ? "Restaurar" : "Maximizar";
    } catch { /* */ }
  };
  tauriWindow.onResized?.(sync);
  sync();
  return box;
}

// --- splash -------------------------------------------------------------------------------------------
const splash = $("#splash");
function splashChrome() {
  if (!tauriWindow) return;
  splash.append(h("div.drag-zone", { "data-tauri-drag-region": "" }), windowControls());
}
function splashText(title, text) {
  $(".splash h1").textContent = title;
  $(".splash p").textContent = text;
}
function splashError(err) {
  const box = $(".splash .box");
  $(".splash .bar")?.remove();
  splashText("No se pudo iniciar Janus", "El motor del proxy no respondió.");
  box.querySelector(".err")?.remove();
  box.querySelector(".row")?.remove();
  box.append(
    h("div.err.selectable", String(err?.message || err) + (err?.logs ? `\n\n${err.logs}` : "")),
    h("div.row", { style: { display: "flex", gap: "8px" } },
      h("button.btn.primary", { onclick: retry }, icon("refresh"), "Reintentar"),
      T ? h("button.btn", { onclick: () => T.core.invoke("open_logs").catch(() => {}) }, icon("folder"), "Abrir logs") : null));
}
async function retry() {
  if (T) {
    try { await T.core.invoke("restart_backend"); } catch { /* */ }
  }
  location.reload();
}

/** El motor murió con la app abierta: pantalla de error con reinicio. */
function fatal(message, logs) {
  if ($(".splash.fatal")) return;
  const box = h("div.box",
    logoMark(64),
    h("h1", "El motor se detuvo"),
    h("p", "El proxy dejó de responder. Podés reiniciarlo sin perder el historial."),
    h("div.err.selectable", message + (logs ? `\n\n${logs}` : "")),
    h("div.row", { style: { display: "flex", gap: "8px" } },
      h("button.btn.primary", { onclick: retry }, icon("refresh"), "Reiniciar motor"),
      h("button.btn", { onclick: () => T.core.invoke("open_logs").catch(() => {}) }, icon("folder"), "Abrir logs")));
  const overlay = h("div.splash.fatal", tauriWindow ? h("div.drag-zone", { "data-tauri-drag-region": "" }) : null,
    tauriWindow ? windowControls() : null, box);
  document.body.append(overlay);
}

if (T?.event?.listen) {
  T.event.listen("backend://exited", async () => {
    let info = {};
    try { info = await T.core.invoke("backend_info"); } catch { /* */ }
    fatal(info.error || "El motor de Janus terminó.", info.logs);
  });
}

// --- armazón -----------------------------------------------------------------------------------------------
let navButtons = {};
let sb = {};
let pills = {};

function buildShell() {
  const nav = h("nav.nav", NAV.map((n) => {
    const btn = h("button.nav-tab", { title: `${n.label} (Ctrl+${n.key})`, onclick: () => go(n.id) },
      icon(n.icon), h("span.lbl", n.label));
    navButtons[n.id] = btn;
    return btn;
  }));
  navButtons.intercept.append(h("span.count", { hidden: true }));

  pills.proxy = h("button.pill.idle", { title: "Estado del proxy · clic para ajustes", onclick: () => app.openSettings() },
    h("span.dot"), h("span.txt", "Proxy"));
  pills.intercept = h("button.pill", { title: "Intercept (Ctrl+I)", onclick: () => app.views.intercept.toggle() },
    icon("hand", "sm"), h("span.txt", "Intercept off"));
  const themeBtn = h("button.icon-btn", { title: "Cambiar tema", onclick: () => toggleTheme() }, icon("sun"));
  const settingsBtn = h("button.icon-btn", { title: "Ajustes (Ctrl+,)", onclick: () => app.openSettings() }, icon("gear"));

  const brand = h("div.brand", { "data-tauri-drag-region": "" }, logoMark(22), h("span.name", "janus"));
  const titlebar = h("header.titlebar", { "data-tauri-drag-region": "" },
    brand, nav, h("div.drag", { "data-tauri-drag-region": "" }),
    h("div.tb-actions", pills.proxy, pills.intercept, themeBtn, settingsBtn),
    windowControls());

  sb.proxy = h("button.sb-item", { onclick: () => app.openSettings() }, h("span.dot"), h("span.t", "Proxy"));
  sb.intercept = h("button.sb-item", { onclick: () => go("intercept") }, icon("hand"), h("span.t", "Intercept apagado"));
  sb.ca = h("button.sb-item", { onclick: () => go("setup") }, icon("award"), h("span.t", "CA"));
  sb.sys = h("button.sb-item.warn", { hidden: true, onclick: () => go("setup") }, icon("network"), h("span.t", "Proxy del sistema activo"));
  sb.flows = h("span.sb-item", icon("layers"), h("span.t", "0 flows"));
  sb.events = h("button.sb-item", { title: "Eventos del proxy", onclick: () => app.drawer.open() }, icon("bell"), h("span.t", "0"));
  sb.ws = h("span.sb-item", h("span.dot"), h("span.t", "Conectando…"));
  sb.version = h("span.sb-item", "");
  const statusbar = h("footer.statusbar", sb.proxy, sb.intercept, sb.ca, sb.sys, h("span.spacer"),
    sb.flows, sb.events, sb.ws, sb.version);

  const views = h("main.views");
  const appEl = $("#app");
  appEl.replaceChildren(titlebar, views, statusbar);
  return views;
}

function go(id) {
  const next = app.views[id];
  if (!next || app.current === id) return;
  const prev = app.views[app.current];
  prev?.onHide?.();
  prev?.el.classList.remove("active");
  next.el.classList.add("active");
  app.current = id;
  for (const [vid, b] of Object.entries(navButtons)) b.classList.toggle("active", vid === id);
  try { localStorage.setItem("janus.view", id); } catch { /* */ }
  next.onShow?.();
}

// --- estado en barra de título / barra de estado -------------------------------------------------------------
app.proxyAddress = () => {
  const host = app.settings["proxy.listen_host"] || "127.0.0.1";
  const shown = host === "0.0.0.0" ? "127.0.0.1" : host;
  return `${shown}:${app.settings["proxy.listen_port"] || 8080}`;
};

function renderProxy() {
  const p = app.proxy || {};
  const addr = `${p.listen_host || "127.0.0.1"}:${p.listen_port ?? app.settings["proxy.listen_port"]}`;
  let cls = "idle";
  let text = "Proxy iniciando…";
  let sbText = "Proxy iniciando…";
  if (p.error) {
    cls = "bad";
    text = "Proxy con error";
    sbText = p.error;
  } else if (p.running) {
    cls = "ok";
    text = `Proxy :${p.listen_port}`;
    sbText = `Escuchando en ${addr}${p.upstream ? ` → ${p.upstream}` : ""}`;
  }
  pills.proxy.className = `pill ${cls}`;
  pills.proxy.querySelector(".txt").textContent = text;
  pills.proxy.title = p.error ? `${p.error}${p.hint ? "\n" + p.hint : ""}` : `${sbText} · clic para ajustes`;
  sb.proxy.className = `sb-item ${cls === "ok" ? "ok" : cls === "bad" ? "bad" : ""}`;
  sb.proxy.querySelector(".t").textContent = sbText;
}

app.setInterceptState = (s) => {
  app.intercept = { ...app.intercept, ...s };
  const { enabled, pending } = app.intercept;
  pills.intercept.classList.toggle("intercept-on", enabled);
  pills.intercept.querySelector(".txt").textContent = enabled ? "Intercept on" : "Intercept off";
  sb.intercept.className = `sb-item ${enabled ? "warn" : ""}`;
  sb.intercept.querySelector(".t").textContent = enabled
    ? `Intercept encendido${pending ? ` · ${pending} retenido${pending === 1 ? "" : "s"}` : ""}`
    : "Intercept apagado";
  const count = navButtons.intercept.querySelector(".count");
  count.hidden = !pending;
  count.textContent = String(pending || "");
};

app.setCa = (ca) => {
  app.ca = ca;
  if (!ca) return;
  const ok = ca.installed;
  sb.ca.className = `sb-item ${ok ? "ok" : ok === false ? "warn" : ""}`;
  sb.ca.querySelector(".t").textContent = ok ? "CA instalada" : ok === false ? "CA no instalada" : "CA";
};

app.setSysProxy = (s) => {
  app.sysproxy = s;
  sb.sys.hidden = !s?.enabled;
};

let unseen = 0;
app.setEventCount = (n, { seen = false, latest } = {}) => {
  unseen = seen ? 0 : unseen + (latest ? 1 : 0);
  sb.events.querySelector(".t").textContent = String(n);
  sb.events.className = `sb-item ${unseen ? "warn" : ""}`;
  if (latest && !seen && latest.source === "tls" && unseen === 1) {
    toast("warn", latest.message, latest.hint || "", 6500);
  }
};

function renderFlows() {
  sb.flows.querySelector(".t").textContent = `${fmtInt(app.status?.flows ?? 0)} flows`;
}

function setWs(state) {
  const live = state === "open";
  sb.ws.className = `sb-item ${live ? "ok" : "warn"}`;
  sb.ws.querySelector(".t").textContent = live ? "En vivo" : "Reconectando…";
}

// --- acciones compartidas ------------------------------------------------------------------------------------
app.go = go;
app.openSettings = () => openSettings(app);
app.openFolder = async (target) => {
  try { await app.api.post("/api/app/open", { target }); } catch (e) { toast("err", "No se pudo abrir la carpeta", e.message); }
};
app.launchBrowser = async (url = null, id = null) => {
  try {
    const r = await app.api.post("/api/browsers/launch", { id: id || app.views.setup?.chosenBrowser || null, url });
    toast("ok", `${r.browser.name} abierto con Janus`, "Perfil aislado: el proxy y el certificado ya están configurados.");
  } catch (e) {
    toast("err", "No se pudo abrir el navegador", e.message);
  }
};
app.sendToRepeater = async (flowId, { quiet = false } = {}) => {
  const tab = await app.views.repeater.fromFlow(flowId, { quiet });
  if (tab && quiet) toast("ok", "Enviado a Repeater", `Pestaña ${tab.name}`);
};
app.createRepeaterTab = (body) => app.views.repeater.createTab(body);
app.addScopeRule = (kind, matcher) => app.views.scope.addRule(kind, matcher);

// --- atajos ------------------------------------------------------------------------------------------------------
const BLOCKED = ["r", "p", "s", "u", "g", "j", "o", "n", "t", "w"];

function onKey(e) {
  const mod = e.ctrlKey || e.metaKey;
  const k = e.key.toLowerCase();
  // Sin recargas, impresiones ni "guardar página" dentro de la app.
  const blocked = e.key === "F5" || (mod && BLOCKED.includes(k));
  if (document.querySelector(".modal-backdrop")) {
    if (blocked) e.preventDefault();
    return;
  }
  if (mod && /^[1-5]$/.test(e.key)) {
    e.preventDefault();
    go(NAV[Number(e.key) - 1].id);
    return;
  }
  if (mod && k === "i") { e.preventDefault(); app.views.intercept.toggle(); return; }
  if (mod && k === ",") { e.preventDefault(); app.openSettings(); return; }
  if (mod && k === "f" && app.current === "history") { e.preventDefault(); app.views.history.focusSearch(); return; }
  const view = app.views[app.current];
  if (view?.shortcut?.(e)) { e.preventDefault(); return; }
  if (blocked) e.preventDefault();
}

function guardBrowserBehavior() {
  document.addEventListener("keydown", onKey);
  document.addEventListener("contextmenu", (e) => {
    if (e.target.closest("input, textarea, .selectable")) return;
    e.preventDefault();
  });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => e.preventDefault());
}

// --- eventos del backend ------------------------------------------------------------------------------------------
function wireEvents() {
  app.on("proxy.status", (p) => { app.proxy = p; renderProxy(); });
  app.on("intercept.state", (s) => app.setInterceptState(s));
  app.on("settings.changed", (s) => { app.settings = s; renderProxy(); });
  app.on("flow.new", () => { app.status.flows = (app.status.flows || 0) + 1; renderFlows(); });
  app.on("flows.deleted", ({ ids }) => { app.status.flows = Math.max(0, (app.status.flows || 0) - ids.length); renderFlows(); });
  app.on("history.cleared", () => { app.status.flows = 0; renderFlows(); });
  app.on("resync", async () => {
    try {
      app.status = await app.api.get("/api/status");
      app.proxy = app.status.proxy;
      renderProxy();
      renderFlows();
    } catch { /* */ }
  });
}

// --- arranque -------------------------------------------------------------------------------------------------------
async function connect() {
  const backend = await resolveBackend((n) => {
    if (n === 25) splashText("Iniciando el motor…", "Preparando el proxy y el certificado.");
  });
  const api = new Api(backend);
  let lastErr;
  for (let i = 0; i < 60; i++) {
    try {
      const status = await api.get("/api/status");
      return { api, status };
    } catch (e) {
      lastErr = e;
      if (e.status === 401) throw new Error("La sesión venció. Volvé a abrir Janus.");
      await sleep(250);
    }
  }
  throw lastErr || new Error("sin respuesta del motor");
}

async function boot() {
  splashChrome();
  let conn;
  try {
    conn = await connect();
  } catch (e) {
    splashError(e);
    return;
  }
  app.api = conn.api;
  app.status = conn.status;
  app.version = conn.status.version;
  app.proxy = conn.status.proxy;
  try { app.settings = await app.api.get("/api/settings"); } catch { /* defaults */ }

  const viewsHost = buildShell();
  guardBrowserBehavior();
  wireEvents();
  app.drawer = createEventsDrawer(app);
  app.views.history = createHistory(app);
  app.views.intercept = createIntercept(app);
  app.views.repeater = createRepeater(app);
  app.views.scope = createScope(app);
  app.views.setup = createSetup(app);
  for (const n of NAV) viewsHost.append(app.views[n.id].el);

  sb.version.textContent = `v${app.version}`;
  renderProxy();
  renderFlows();
  app.setInterceptState(conn.status.intercept);
  app.setSysProxy(conn.status.sysproxy);

  let saved = "history";
  try { saved = localStorage.getItem("janus.view") || "history"; } catch { /* */ }
  go(app.views[saved] ? saved : "history");

  let firstOpen = true;
  app.api.stream((type, data) => app.emit(type, data), (state) => {
    setWs(state);
    if (state === "open") {
      if (!firstOpen) app.emit("resync");
      firstOpen = false;
    }
  });

  splash.classList.add("gone");
  setTimeout(() => splash.remove(), 400);

  if (app.proxy?.error) toast("err", app.proxy.error, app.proxy.hint || "", 9000);
}

boot();
