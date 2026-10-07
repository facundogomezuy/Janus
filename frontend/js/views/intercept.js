// Vista Intercept: cola de flows retenidos + editor.

import { emptyState, fmtAgo, h, icon, methodClass, split, toast } from "../dom.js";
import { HttpEditor } from "../editor.js";

export function createIntercept(app) {
  let state = { enabled: false, requests: true, responses: false, only_in_scope: true, filter: "", pending: 0 };
  const queue = new Map(); // id -> item (orden de llegada)
  const edits = new Map(); // id -> texto editado (si se cambió de ítem sin mandar)
  let current = null;

  // --- toolbar ---------------------------------------------------------------------
  const toggle = h("input", { type: "checkbox" });
  const toggleLbl = h("span.lbl", "Intercept apagado");
  const stateBox = h("label.switch.big.warn.ic-state", toggle, toggleLbl);
  const reqChk = h("input", { type: "checkbox" });
  const resChk = h("input", { type: "checkbox" });
  const scopeChk = h("input", { type: "checkbox" });
  const filter = h("input.input.mono", {
    placeholder: "Filtro avanzado (mitmproxy): ~d target.com & ~m POST",
    spellcheck: false, style: { flex: "1 1 150px", maxWidth: "320px", minWidth: "150px" },
    title: "Sintaxis de filtros de mitmproxy: ~d dominio · ~m método · ~u url · ~h header · ~b cuerpo · & | !",
  });
  const fwdAll = h("button.btn", { title: "Forward de todos los retenidos", onclick: () => bulk("forward-all") },
    icon("forward"), h("span.lbl-long", "Forward todo"));
  const dropAll = h("button.btn.danger", { title: "Drop de todos los retenidos", onclick: () => bulk("drop-all") },
    icon("ban"), h("span.lbl-long", "Drop todo"));
  const toolbar = h("div.toolbar",
    stateBox, h("span.sep"),
    h("label.check", reqChk, "Requests"),
    h("label.check", resChk, "Responses"),
    h("label.check", scopeChk, "Solo scope"),
    filter,
    h("span.spacer"), fwdAll, dropAll);

  toggle.addEventListener("change", () => configure({ enabled: toggle.checked }));
  reqChk.addEventListener("change", () => configure({ requests: reqChk.checked }));
  resChk.addEventListener("change", () => configure({ responses: resChk.checked }));
  scopeChk.addEventListener("change", () => configure({ only_in_scope: scopeChk.checked }));
  const commitFilter = async () => {
    if (filter.value.trim() === state.filter) return;
    const ok = await configure({ filter: filter.value.trim() });
    filter.classList.toggle("invalid", !ok);
  };
  filter.addEventListener("keydown", (e) => { if (e.key === "Enter") commitFilter(); });
  filter.addEventListener("blur", commitFilter);
  filter.addEventListener("input", () => filter.classList.remove("invalid"));

  // --- cola ------------------------------------------------------------------------------
  const list = h("div.queue");

  // --- editor ------------------------------------------------------------------------------
  const phaseBadge = h("span.badge");
  const reqLine = h("span.pane-sub");
  const waited = h("span.hint", { style: { marginRight: "8px", fontVariantNumeric: "tabular-nums" } });
  const editor = new HttpEditor({
    onSubmit: () => forward(),
    onChange: () => { if (current) edits.set(current, editor.value); dirtyMark.hidden = !editor.dirty; },
  });
  const dirtyMark = h("span.badge.warn", { hidden: true }, "editado");
  const revertBtn = h("button.icon-btn", { title: "Deshacer cambios", onclick: () => { editor.revert(); edits.delete(current); dirtyMark.hidden = true; } }, icon("undo"));
  const edHead = h("div.pane-head", phaseBadge, reqLine, dirtyMark, h("span.spacer"), waited, revertBtn);
  const interceptRes = h("input", { type: "checkbox" });
  const interceptResLbl = h("label.check", { title: "Retener también la respuesta de este request" }, interceptRes, "Interceptar la respuesta");
  const actions = h("div.actionbar",
    h("button.btn.primary", { onclick: () => forward() }, icon("forward"), "Forward", h("kbd", "Ctrl+⏎")),
    h("button.btn.danger", { onclick: () => drop() }, icon("ban"), "Drop"),
    interceptResLbl,
    h("span.spacer"),
    h("button.btn.ghost", { onclick: () => toRepeater() }, icon("repeat"), "Enviar a Repeater"));
  const edPane = h("div.pane", edHead, editor.el, actions);
  const rightHost = h("div", { style: { display: "flex", flexDirection: "column", minHeight: "0", minWidth: "0" } });
  const countBadge = h("span.badge.warn");
  const listPane = h("div.pane", h("div.pane-head", h("span.pane-title", "Retenidos"), h("span.spacer"), countBadge), list);
  const body = split("h", listPane, rightHost, { key: "intercept", initial: 0.27, min: 220 });
  const emptyHost = h("div", { style: { flex: "1", display: "flex", minHeight: "0" } });
  const el = h("section.view", toolbar, emptyHost, body);

  // --- render --------------------------------------------------------------------------------
  function syncToolbar() {
    toggle.checked = state.enabled;
    stateBox.classList.toggle("on", state.enabled);
    toggleLbl.textContent = state.enabled ? "Intercept encendido" : "Intercept apagado";
    reqChk.checked = state.requests;
    resChk.checked = state.responses;
    scopeChk.checked = state.only_in_scope;
    if (document.activeElement !== filter) filter.value = state.filter || "";
    fwdAll.disabled = dropAll.disabled = queue.size === 0;
  }

  function renderList() {
    countBadge.textContent = String(queue.size);
    list.replaceChildren(...[...queue.values()].map((it) =>
      h(`div.q-item${it.id === current ? ".selected" : ""}`, { dataset: { id: it.id }, onclick: () => show(it.id) },
        h(`span.qm.${methodClass(it.method)}`, it.method),
        h("span.qh", it.host + (it.port !== (it.scheme === "https" ? 443 : 80) ? `:${it.port}` : "")),
        h(`span.badge.${it.phase === "request" ? "req" : "res"}`, it.phase === "request" ? "REQ" : `RES ${it.status_code ?? ""}`),
        h("span.qp", { title: it.path }, it.path),
      )));
  }

  function renderLayout() {
    syncToolbar();
    if (!queue.size) {
      body.hidden = true;
      emptyHost.hidden = false;
      emptyHost.replaceChildren(state.enabled
        ? emptyState({
          icon: "hand",
          title: "Esperando tráfico…",
          text: `Cada ${[state.requests && "request", state.responses && "response"].filter(Boolean).join(" y ") || "mensaje"}`
            + `${state.only_in_scope ? " en scope" : ""}${state.filter ? " que cumpla el filtro" : ""} va a quedar retenido acá para que lo edites antes de que siga.`,
          actions: [h("button.btn", { onclick: () => configure({ enabled: false }) }, "Apagar intercept")],
        })
        : emptyState({
          icon: "hand",
          title: "Intercept apagado",
          text: "Activalo para frenar cada request antes de que salga: lo podés editar, reenviar o descartar. Atajo: Ctrl+I.",
          actions: [h("button.btn.primary", { onclick: () => configure({ enabled: true }) }, icon("hand"), "Activar intercept")],
        }));
      current = null;
      return;
    }
    emptyHost.hidden = true;
    body.hidden = false;
    if (!current || !queue.has(current)) show(queue.keys().next().value);
    else renderList();
  }

  function show(id) {
    const it = queue.get(id);
    if (!it) return;
    current = id;
    phaseBadge.className = `badge ${it.phase === "request" ? "req" : "res"}`;
    phaseBadge.textContent = it.phase === "request" ? "Request" : "Response";
    reqLine.textContent = it.phase === "request" ? it.url : `${it.status_code} · ${it.request_line}`;
    reqLine.title = it.url;
    const saved = edits.get(id);
    editor.value = it.message.text;
    if (saved != null && saved !== it.message.text) {
      editor.ta.value = saved;
      editor.ta.dispatchEvent(new Event("input"));
    }
    dirtyMark.hidden = !editor.dirty;
    interceptRes.checked = it.intercept_response;
    interceptResLbl.hidden = it.phase !== "request";
    if (rightHost.firstChild !== edPane) rightHost.replaceChildren(edPane);
    tick();
    renderList();
    editor.focus();
  }

  function tick() {
    const it = current && queue.get(current);
    waited.textContent = it ? `retenido hace ${fmtAgo(it.held_at)}` : "";
  }
  setInterval(tick, 1000);

  // --- acciones ------------------------------------------------------------------------------
  async function configure(changes) {
    try {
      state = { ...state, ...(await app.api.put("/api/intercept", changes)) };
      app.setInterceptState(state);
      renderLayout();
      return true;
    } catch (e) {
      toast("err", "No se pudo cambiar el intercept", e.message);
      syncToolbar();
      return false;
    }
  }

  async function forward() {
    const it = current && queue.get(current);
    if (!it) return;
    const body = {};
    if (editor.dirty) body.message = { text: editor.value, encoding: it.message.encoding };
    if (it.phase === "request" && interceptRes.checked !== it.intercept_response) body.intercept_response = interceptRes.checked;
    try {
      await app.api.post(`/api/intercept/${it.id}/forward`, body);
      resolved(it.id);
    } catch (e) {
      if (e.status === 404) resolved(it.id);
      toast("err", e.status === 404 ? "Ese flow ya no estaba retenido" : "No se pudo hacer forward", e.status === 404 ? "" : e.message);
    }
  }

  async function drop() {
    const it = current && queue.get(current);
    if (!it) return;
    try {
      await app.api.post(`/api/intercept/${it.id}/drop`);
    } catch (e) {
      if (e.status !== 404) toast("err", "No se pudo descartar", e.message);
    }
    resolved(it.id);
  }

  async function bulk(action) {
    try {
      await app.api.post(`/api/intercept/${action}`);
    } catch (e) {
      toast("err", "No se pudo completar", e.message);
    }
  }

  async function toRepeater() {
    const it = current && queue.get(current);
    if (!it || it.phase !== "request") {
      toast("info", "Solo se pueden mandar requests al Repeater");
      return;
    }
    await app.createRepeaterTab({
      host: it.host, port: it.port, tls: it.scheme === "https",
      message: { text: editor.value, encoding: it.message.encoding },
    });
  }

  function resolved(id) {
    if (!queue.has(id)) return;
    const ids = [...queue.keys()];
    const idx = ids.indexOf(id);
    queue.delete(id);
    edits.delete(id);
    if (current === id) current = ids[idx + 1] ?? ids[idx - 1] ?? null;
    renderLayout();
    if (current) show(current);
  }

  // --- eventos ----------------------------------------------------------------------------------
  app.on("intercept.pending", (item) => {
    const isFirst = queue.size === 0;
    queue.set(item.id, item);
    renderLayout();
    if (isFirst || !current) show(item.id);
    else renderList();
  });
  app.on("intercept.resolved", ({ id }) => resolved(id));
  app.on("intercept.state", (s) => {
    state = { ...state, ...s };
    syncToolbar();
    if (!queue.size) renderLayout();
  });

  async function load() {
    try {
      const data = await app.api.get("/api/intercept");
      const { queue: items, ...s } = data;
      state = { ...state, ...s };
      queue.clear();
      for (const it of items) queue.set(it.id, it);
      app.setInterceptState(state);
      renderLayout();
    } catch (e) {
      toast("err", "No se pudo leer el intercept", e.message);
    }
  }
  app.on("resync", load);
  load();

  return {
    id: "intercept",
    el,
    onShow() { if (current) editor.focus(); },
    toggle: () => configure({ enabled: !state.enabled }),
    get state() { return state; },
    shortcut(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "Delete" && current) { drop(); return true; }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "r" && current) { toRepeater(); return true; }
      return false;
    },
  };
}
