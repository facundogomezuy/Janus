// Vista Repeater: pestañas persistidas, envío crudo y visor de la respuesta.

import { debounce, emptyState, fmtBytes, fmtMs, h, icon, openMenu, promptDialog, split, statusClass, toast } from "../dom.js";
import { HttpEditor } from "../editor.js";
import { b64ToBytes, headerFromText } from "../http.js";
import { MessageViewer } from "../viewer.js";

export function createRepeater(app) {
  let tabs = [];
  let currentId = null;
  const sending = new Map(); // tabId -> AbortController
  let fixCL = true;

  // --- pestañas ------------------------------------------------------------------------------
  // Dónde empezó la secuencia de clics: si el 1.º cierra una pestaña, la tira se
  // corre y el 2.º de ese doble clic cae sobre otra (o sobre "+"); ahí no actúa.
  let pressedOn = null;
  document.addEventListener("mousedown", (e) => { if (e.detail <= 1) pressedOn = null; }, true);
  const press = (key) => (e) => { if (e.detail <= 1) pressedOn = key; };
  const sameTarget = (e, key) => e.detail <= 1 || pressedOn === key;
  const strip = h("div.rtabs");
  const addBtn = h("button.icon-btn.rtab-add", {
    title: "Nueva pestaña (Ctrl+T)",
    onmousedown: press("+"),
    onclick: (e) => { if (sameTarget(e, "+")) createTab(); },
  }, icon("plus"));

  // --- barra de destino ----------------------------------------------------------------------
  const schemeSeg = h("div.segmented",
    h("button", { dataset: { v: "https" }, onclick: () => setTls(true) }, icon("lock", "sm"), " HTTPS"),
    h("button", { dataset: { v: "http" }, onclick: () => setTls(false) }, "HTTP"));
  const hostIn = h("input.input.mono.host", { placeholder: "host", spellcheck: false });
  const portIn = h("input.input.mono.port", { placeholder: "puerto", inputmode: "numeric" });
  const syncHostBtn = h("button.icon-btn", { title: "Tomar host y puerto del header Host", onclick: () => syncFromHostHeader() }, icon("link"));
  const sendBtn = h("button.btn.primary", { onclick: () => send() });
  const clChk = h("input", { type: "checkbox", checked: true });
  const stats = h("span.res-stats");
  const target = h("div.target-bar", schemeSeg, hostIn, portIn, syncHostBtn, sendBtn,
    h("label.check", { title: "Recalcular Content-Length antes de mandar" }, clChk, "Content-Length automático"), stats);
  clChk.addEventListener("change", () => { fixCL = clChk.checked; });

  // --- editor + visor ---------------------------------------------------------------------------
  const editor = new HttpEditor({ onSubmit: () => send(), onChange: () => saveSoon() });
  const reqPane = h("div.pane", h("div.pane-head", h("span.pane-title", "Request"), h("span.spacer"),
    h("span.hint", "Ctrl+⏎ para enviar")), editor.el);
  const viewer = new MessageViewer({ title: "Response", key: "rep" });
  const panes = split("h", reqPane, viewer.el, { key: "repeater", initial: 0.5, min: 240 });
  const work = h("div", { style: { display: "flex", flexDirection: "column", flex: "1", minHeight: "0" } }, target, panes);
  const emptyHost = h("div", { style: { flex: "1", display: "flex" } });
  const el = h("section.view", strip, work, emptyHost);

  const cur = () => tabs.find((t) => t.id === currentId);

  function tabButton(t) {
    const dot = t.response?.response ? h(`span.rdot`, { style: { background: `var(--${statusVar(t.response.response.status_code)})` } }) : null;
    return h(`button.rtab${t.id === currentId ? ".on" : ""}`, {
      title: `${t.name}\n${t.tls ? "https" : "http"}://${t.host}:${t.port}`,
      // sin re-render si ya está activa: un doble clic necesita que el botón siga en el DOM
      onmousedown: press(t.id),
      onclick: (e) => { if (sameTarget(e, t.id) && t.id !== currentId) select(t.id); },
      onauxclick: (e) => { if (e.button === 1) closeTab(t.id); },
      ondblclick: (e) => { if (sameTarget(e, t.id)) rename(t.id); },
      oncontextmenu: (e) => { e.preventDefault(); tabMenu(e.clientX, e.clientY, t); },
    }, dot, h("span.rn", t.name), h("span.rx", {
      title: "Cerrar",
      onclick: (e) => {
        e.stopPropagation();
        if (e.detail > 1) return; // el 2.º clic de un doble clic nunca cierra una pestaña
        closeTab(t.id);
      },
    }, icon("x", "sm")));
  }

  function renderStrip() {
    strip.replaceChildren(...tabs.map(tabButton), addBtn);
  }

  function statusVar(code) {
    const c = statusClass(code);
    return { "s-2": "ok", "s-3": "warn", "s-4": "err", "s-5": "err", "s-1": "info" }[c] || "text-3";
  }

  function renderTarget({ force = false } = {}) {
    const t = cur();
    if (!t) return;
    for (const b of schemeSeg.children) b.classList.toggle("on", (b.dataset.v === "https") === t.tls);
    if (force || document.activeElement !== hostIn) hostIn.value = t.host;
    if (force || document.activeElement !== portIn) portIn.value = t.port;
    const busy = sending.has(t.id);
    sendBtn.replaceChildren(...(busy
      ? [h("span.spinner"), "Cancelar"]
      : [icon("send"), "Enviar", h("kbd", "Ctrl+⏎")]));
    sendBtn.classList.toggle("primary", !busy);
  }

  function renderResponse() {
    const t = cur();
    stats.replaceChildren();
    if (!t) return;
    if (sending.has(t.id)) {
      viewer.setEmpty("Enviando…", { icon: "send", title: `${t.tls ? "https" : "http"}://${t.host}:${t.port}` });
      return;
    }
    const r = t.response;
    if (!r) {
      viewer.setEmpty("Mandá el request para ver la respuesta acá.", { icon: "send", title: "Sin respuesta todavía" });
      return;
    }
    const bits = [];
    if (r.response) bits.push(h(`b.${statusClass(r.response.status_code)}`, `${r.response.status_code} ${r.response.reason}`.trim()));
    bits.push(h("span", h("b", fmtMs(r.elapsed_ms))));
    bits.push(h("span", h("b", fmtBytes(r.bytes_received))));
    if (r.tls_version) bits.push(h("span", { title: r.cipher || "" }, r.tls_version));
    if (r.peer) bits.push(h("span", r.peer));
    stats.replaceChildren(...bits);
    if (r.response) {
      viewer.set(r.response, { rawBytes: b64ToBytes(r.raw_b64) });
      if (r.error) toast("warn", "Respuesta incompleta", r.error);
    } else {
      viewer.setError(r.error || "sin respuesta");
    }
  }

  function renderAll() {
    renderStrip();
    if (!tabs.length) {
      work.hidden = true;
      emptyHost.hidden = false;
      emptyHost.replaceChildren(emptyState({
        icon: "repeat",
        title: "Repeater vacío",
        text: "Mandá un request desde History (clic derecho → Enviar a Repeater, o Ctrl+R) o armá uno desde cero.",
        actions: [h("button.btn.primary", { onclick: () => createTab() }, icon("plus"), "Nueva pestaña")],
      }));
      return;
    }
    emptyHost.hidden = true;
    work.hidden = false;
    renderTarget();
    renderResponse();
  }

  function select(id) {
    flushSave();
    currentId = id;
    const t = cur();
    if (!t) return renderAll();
    editor.value = t.message.text;
    renderAll();
    renderTarget({ force: true });
  }

  // --- persistencia --------------------------------------------------------------------------------
  function readForm(t) {
    t.host = hostIn.value.trim();
    const port = parseInt(portIn.value, 10);
    if (port > 0 && port < 65536) t.port = port;
    t.message = { text: editor.value, encoding: t.message.encoding };
  }

  const save = async (tab) => {
    if (!tab) return;
    try {
      await app.api.patch(`/api/repeater/tabs/${tab.id}`, {
        host: tab.host, port: tab.port, tls: tab.tls, message: tab.message,
      });
    } catch (e) {
      if (e.status !== 404) toast("err", "No se pudo guardar la pestaña", e.message);
    }
  };
  const saveDebounced = debounce(save, 700);
  function saveSoon() {
    const t = cur();
    if (!t) return;
    readForm(t);
    saveDebounced(t);
  }
  function flushSave() {
    const t = cur();
    if (t && editor.value !== t.message.text) readForm(t);
    saveDebounced.cancel();
    if (t) save(t);
  }

  hostIn.addEventListener("input", saveSoon);
  portIn.addEventListener("input", saveSoon);
  for (const inp of [hostIn, portIn]) {
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  }

  function setTls(tls) {
    const t = cur();
    if (!t || t.tls === tls) return;
    const wasDefault = t.port === (t.tls ? 443 : 80);
    t.tls = tls;
    if (wasDefault) t.port = tls ? 443 : 80;
    portIn.value = t.port;
    renderTarget();
    saveSoon();
  }

  function syncFromHostHeader() {
    const t = cur();
    const hostHdr = headerFromText(editor.value, "host");
    if (!t || !hostHdr) {
      toast("info", "El request no tiene header Host");
      return;
    }
    const m = hostHdr.match(/^\[?([^\]]+?)\]?(?::(\d+))?$/);
    t.host = m[1];
    t.port = m[2] ? Number(m[2]) : (t.tls ? 443 : 80);
    hostIn.value = t.host;
    portIn.value = t.port;
    saveSoon();
  }

  // --- envío ------------------------------------------------------------------------------------------
  async function send() {
    const t = cur();
    if (!t) return;
    if (sending.has(t.id)) {
      sending.get(t.id).abort();
      return;
    }
    readForm(t);
    if (!t.host) {
      const hostHdr = headerFromText(t.message.text, "host");
      if (hostHdr) syncFromHostHeader();
      else { hostIn.focus(); toast("warn", "Falta el host de destino"); return; }
    }
    const ctrl = new AbortController();
    sending.set(t.id, ctrl);
    renderTarget();
    renderResponse();
    try {
      const res = await app.api.post(`/api/repeater/tabs/${t.id}/send`, {
        host: t.host, port: t.port, tls: t.tls, message: t.message, fix_content_length: fixCL,
      }, { signal: ctrl.signal });
      t.response = res;
    } catch (e) {
      if (e.name !== "AbortError") toast("err", "No se pudo enviar", e.message);
    } finally {
      sending.delete(t.id);
      renderStrip();
      if (t.id === currentId) {
        renderTarget();
        renderResponse();
      }
    }
  }

  // --- CRUD de pestañas ---------------------------------------------------------------------------------
  async function createTab(body = {}) {
    try {
      const tab = await app.api.post("/api/repeater/tabs", body);
      tabs.push(tab);
      select(tab.id);
      return tab;
    } catch (e) {
      toast("err", "No se pudo crear la pestaña", e.message);
      return null;
    }
  }

  async function closeTab(id) {
    const idx = tabs.findIndex((t) => t.id === id);
    if (idx < 0) return;
    sending.get(id)?.abort();
    tabs.splice(idx, 1);
    if (currentId === id) {
      currentId = null;
      saveDebounced.cancel();
      const next = tabs[idx] || tabs[idx - 1];
      if (next) select(next.id);
      else renderAll();
    } else {
      renderStrip();
    }
    try { await app.api.del(`/api/repeater/tabs/${id}`); } catch { /* ya no existe */ }
  }

  // Con un diálogo y no inline: editar dentro de la tira la reacomoda mientras
  // se escribe (foco, blur, clics que caen sobre otra pestaña, re-renders de un
  // envío que termina). El modal la deja quieta y no lo afecta nada de eso.
  async function rename(id) {
    const t = tabs.find((x) => x.id === id);
    if (!t) return;
    const value = await promptDialog({ title: "Renombrar pestaña", value: t.name, maxlength: 80 });
    const name = value?.trim();
    const tab = tabs.find((x) => x.id === id); // un resync pudo reemplazarla (o cerrarla) mientras tanto
    if (!tab || !name || name === tab.name) return;
    const prev = tab.name;
    tab.name = name;
    renderStrip();
    try {
      await app.api.patch(`/api/repeater/tabs/${id}`, { name });
    } catch (e) {
      tab.name = prev;
      renderStrip();
      toast("err", "No se pudo renombrar", e.message);
    }
  }

  function tabMenu(x, y, t) {
    openMenu(x, y, [
      { label: "Renombrar", icon: "edit", onClick: () => rename(t.id) },
      { label: "Duplicar", icon: "copy", onClick: () => {
        if (t.id === currentId) readForm(t);
        // máx. 80 caracteres; se corta por code points para no partir un emoji
        createTab({ host: t.host, port: t.port, tls: t.tls, message: t.message, name: `${Array.from(t.name).slice(0, 72).join("")} (copia)` });
      } },
      "sep",
      { label: "Cerrar", icon: "x", onClick: () => closeTab(t.id) },
      { label: "Cerrar las demás", disabled: tabs.length < 2, onClick: () => tabs.filter((o) => o.id !== t.id).forEach((o) => closeTab(o.id)) },
    ]);
  }

  async function load() {
    try {
      const data = await app.api.get("/api/repeater/tabs");
      tabs = data.tabs;
      const keep = tabs.find((t) => t.id === currentId) || tabs[tabs.length - 1];
      if (keep) select(keep.id);
      else renderAll();
    } catch (e) {
      toast("err", "No se pudieron cargar las pestañas", e.message);
    }
  }
  app.on("resync", load);
  load();

  return {
    id: "repeater",
    el,
    onShow() { if (cur()) editor.focus(); },
    onHide() { flushSave(); },
    async fromFlow(flowId, { quiet = false } = {}) {
      const tab = await createTab({ from_flow: flowId });
      if (tab && !quiet) app.go("repeater");
      return tab;
    },
    createTab: async (body) => {
      const tab = await createTab(body);
      if (tab) app.go("repeater");
      return tab;
    },
    shortcut(e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "t") { createTab(); return true; }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "w" && currentId) { closeTab(currentId); return true; }
      return false;
    },
  };
}
