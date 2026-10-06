// Vista History: tabla virtualizada + detalle request/response.

import {
  confirmDialog, copyText, debounce, emptyState, esc, fmtBytes, fmtClock, fmtInt, fmtMs, h, icon,
  methodClass, openMenu, persist, promptDialog, split, statusClass, store, toast,
} from "../dom.js";
import { contentKind } from "../http.js";
import { MessageViewer } from "../viewer.js";

const ROW_H = 28;
const COLORS = ["red", "orange", "yellow", "green", "teal", "blue", "purple", "gray"];
const STATIC_EXT = /\.(?:png|jpe?g|gif|webp|avif|svg|ico|bmp|css|woff2?|ttf|otf|eot|map|mp4|webm|mp3|m4a)(?:\?|$)/i;
const STATIC_KINDS = new Set(["img", "font", "css", "media"]);

const COLUMNS = [
  { key: "seq", label: "#", cls: "num", width: "60px" },
  { key: "method", label: "Método", width: "76px" },
  { key: "host", label: "Host", width: "minmax(140px, 1.15fr)" },
  { key: "path", label: "Path", width: "minmax(180px, 2.6fr)" },
  { key: "status_code", label: "Estado", cls: "num", width: "64px" },
  { key: "length", label: "Tamaño", cls: "num", width: "82px" },
  { key: "kind", label: "Tipo", width: "70px" },
  { key: "duration_ms", label: "Tiempo", cls: "num", width: "76px" },
  { key: "ts", label: "Hora", width: "84px" },
];
const GRID_COLS = COLUMNS.map((c) => c.width).join(" ");

const KEYMAP = { method: "method", m: "method", status: "status", s: "status", host: "host", h: "host",
  path: "path", p: "path", type: "type", t: "type", color: "color", c: "color" };

// Tráfico interno de los navegadores (updaters, hora de red, experimentos,
// SmartScreen…). Endpoints puntuales, nunca dominios enteros: google.com como
// target se sigue viendo.
const BROWSER_NOISE = [
  /^(?:update\.googleapis\.com|clients\d\.google\.com)\/service\/update2\//,
  /\/time\/1\/current\?cup2key=/,
  /^edge\.microsoft\.com\/(?:componentupdater|browsernetworktime|serviceexperimentation|abusiveadblocking)\//,
  /^accounts\.google\.com\/ListAccounts\?.*source=ChromiumBrowser/,
  /^www\.gstatic\.com\/ohttp_gateway\//,
  /^www\.google\.com\/async\/folae\?/,
  /^(?:safebrowsing|optimizationguide-pa|content-autofill|clientservices)\.googleapis\.com\//,
  /^[\w-]+\.smartscreen(?:-prod)?\.microsoft\.com\//,
  /^config\.edge\.skype\.com\//,
  /^(?:firefox\.settings\.services|content-signature-2\.cdn|aus5|shavar\.services|push\.services|incoming\.telemetry|tracking-protection\.cdn)\.mozilla\.(?:com|net|org)\//,
  /^detectportal\.firefox\.com\//,
];
export const isNoise = (f) => BROWSER_NOISE.some((re) => re.test(f.host + f.path));

const kindOf = (f) => contentKind(f.content_type);
const isStatic = (f) => STATIC_KINDS.has(kindOf(f)) || STATIC_EXT.test(f.path);
const defaultPort = (scheme) => (scheme === "https" ? 443 : 80);

function hostLabel(f) {
  const port = f.port !== defaultPort(f.scheme) ? `:${f.port}` : "";
  return `${f.host}${port}`;
}
export function flowUrl(f) {
  return `${f.scheme}://${hostLabel(f)}${f.path}`;
}

function parseQuery(q) {
  const terms = [];
  for (const raw of q.trim().split(/\s+/).filter(Boolean)) {
    let tok = raw;
    let neg = false;
    if (tok.length > 1 && tok.startsWith("-")) { neg = true; tok = tok.slice(1); }
    const m = tok.match(/^([a-z]+):(.+)$/i);
    if (m && KEYMAP[m[1].toLowerCase()]) terms.push({ key: KEYMAP[m[1].toLowerCase()], value: m[2].toLowerCase(), neg });
    else terms.push({ key: "any", value: tok.toLowerCase(), neg });
  }
  return terms;
}

function matchTerm(f, t) {
  let ok;
  switch (t.key) {
    case "method": ok = f.method.toLowerCase() === t.value; break;
    case "status": {
      const s = String(f.status_code ?? "");
      ok = /^\dxx$/.test(t.value) ? s[0] === t.value[0] : s.startsWith(t.value);
      break;
    }
    case "host": ok = f.host.toLowerCase().includes(t.value); break;
    case "path": ok = f.path.toLowerCase().includes(t.value); break;
    case "type": ok = kindOf(f).includes(t.value); break;
    case "color": ok = (f.color || "") === t.value; break;
    default: ok = `${f.host}${f.path}`.toLowerCase().includes(t.value) || f.method.toLowerCase() === t.value;
  }
  return t.neg ? !ok : ok;
}

export function createHistory(app) {
  const flows = [];
  const byId = new Map();
  let view = [];
  let selected = null;
  const multi = new Set();
  let anchorIdx = null;
  let sort = store("history.sort", { key: "seq", dir: 1 });
  const filters = {
    text: "",
    terms: [],
    status: new Set(store("history.status", [])),
    onlyScope: store("history.onlyScope", false),
    hideStatic: store("history.hideStatic", false),
    hideNoise: store("history.hideNoise", true),
    source: store("history.source", "all"),
    bodyMode: false,
    bodyIds: null,
  };
  let dirty = true;
  let follow = true;
  let raf = 0;
  const fresh = new Set();
  let loaded = false;
  let total = 0;

  // --- toolbar --------------------------------------------------------------------
  const search = h("input.input", {
    type: "search", placeholder: "Filtrar  (method:POST  status:4xx  -cdn)", spellcheck: false,
  });
  const bodyBtn = h("button.icon-btn", { title: "Buscar también en headers y cuerpos (en el motor)" }, icon("file"));
  const chips = h("div.chips", ["2", "3", "4", "5"].map((c) =>
    h(`button.chip.c${c}`, { dataset: { c }, title: `Mostrar ${c}xx`, onclick: () => toggleStatus(c) }, `${c}xx`)));
  const onlyScope = h("input", { type: "checkbox", checked: filters.onlyScope });
  const viewLbl = h("span", "Vista");
  const viewBtn = h("button.btn.ghost", { title: "Qué mostrar", onclick: (e) => viewMenu(e.currentTarget) },
    icon("eye"), viewLbl, icon("chevron-down", "sm"));
  const countLbl = h("span.hint", { style: { fontVariantNumeric: "tabular-nums" } });
  const clearBtn = h("button.icon-btn", { title: "Limpiar historial", onclick: () => clearAll() }, icon("trash"));
  const toolbar = h("div.toolbar",
    h("div.search", icon("search"), search), bodyBtn,
    chips,
    h("label.check", onlyScope, "Solo scope"),
    viewBtn,
    h("span.spacer"), countLbl, h("span.sep"), clearBtn);

  // --- grilla ---------------------------------------------------------------------
  const head = h("div.grid-head", COLUMNS.map((c) =>
    h(`div${c.cls ? "." + c.cls : ""}`, { dataset: { key: c.key }, onclick: () => setSort(c.key) }, c.label)));
  const spacer = h("div.grid-spacer");
  const body = h("div.grid-body", { tabindex: "0" }, spacer);
  const emptyHost = h("div", { style: { position: "absolute", inset: "0", display: "flex" }, hidden: true });
  const gridWrap = h("div.grid-wrap", { style: { "--cols": GRID_COLS, position: "relative" } }, head, body, emptyHost);
  gridWrap.style.setProperty("--cols", GRID_COLS);

  // --- detalle ----------------------------------------------------------------------
  const metaUrl = h("span.url.selectable");
  const metaInfo = h("span", { style: { display: "inline-flex", gap: "12px", alignItems: "center" } });
  const metaActions = h("span", { style: { display: "inline-flex", gap: "2px", marginLeft: "auto" } },
    h("button.icon-btn", { title: "Enviar a Repeater (Ctrl+R)", onclick: () => selected && app.sendToRepeater(selected) }, icon("repeat")),
    h("button.icon-btn", { title: "Abrir en el navegador Janus", onclick: () => openInBrowser() }, icon("external")),
    h("button.icon-btn", { title: "Copiar como curl", onclick: () => copyCurl(selected) }, icon("terminal")),
    h("button.icon-btn", { title: "Más acciones", onclick: (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      if (selected) rowMenu(r.left, r.bottom + 4, [selected]);
    } }, icon("more")));
  const meta = h("div.meta-line", metaUrl, metaInfo, metaActions);
  const reqViewer = new MessageViewer({ title: "Request", key: "req" });
  const resViewer = new MessageViewer({ title: "Response", key: "res" });
  const panes = split("h", reqViewer.el, resViewer.el, { key: "history.detail", initial: 0.5, min: 220 });
  const detailEmpty = h("div.empty.subtle", h("p", "Seleccioná un flow para ver el request y la response."));
  const detail = h("div", { style: { display: "flex", flexDirection: "column", minHeight: "0", flex: "1" } }, meta, panes);
  const detailHost = h("div", { style: { display: "flex", flexDirection: "column", minHeight: "0" } }, detailEmpty);
  const main = split("v", gridWrap, detailHost, { key: "history.main", initial: 0.52, min: 120 });
  const el = h("section.view", toolbar, main);

  // --- filtros / orden ---------------------------------------------------------------
  function passes(f) {
    if (filters.onlyScope && !f.in_scope) return false;
    if (filters.hideStatic && isStatic(f)) return false;
    if (filters.hideNoise && isNoise(f)) return false;
    if (filters.source !== "all" && f.source !== filters.source) return false;
    if (filters.status.size) {
      const c = f.status_code ? String(f.status_code)[0] : "x";
      if (!filters.status.has(c)) return false;
    }
    if (filters.bodyIds) return filters.bodyIds.has(f.id);
    for (const t of filters.terms) if (!matchTerm(f, t)) return false;
    return true;
  }

  function sortValue(f, key) {
    if (key === "kind") return kindOf(f);
    return f[key];
  }

  function compare(a, b) {
    const va = sortValue(a, sort.key);
    const vb = sortValue(b, sort.key);
    let r;
    if (va == null && vb == null) r = 0;
    else if (va == null) r = 1;
    else if (vb == null) r = -1;
    else if (typeof va === "string") r = va.localeCompare(vb);
    else r = va - vb;
    return (r || a.seq - b.seq) * sort.dir;
  }

  function recompute() {
    view = flows.filter(passes);
    if (!(sort.key === "seq" && sort.dir === 1)) view.sort(compare);
    dirty = false;
  }

  function setSort(key) {
    sort = sort.key === key ? { key, dir: -sort.dir } : { key, dir: key === "seq" || key === "ts" ? 1 : -1 };
    persist("history.sort", sort);
    dirty = true;
    refresh();
  }

  function toggleStatus(c) {
    if (filters.status.has(c)) filters.status.delete(c);
    else filters.status.add(c);
    persist("history.status", [...filters.status]);
    dirty = true;
    refresh();
  }

  const runBodySearch = debounce(async () => {
    const q = search.value.trim();
    if (!filters.bodyMode || !q) {
      filters.bodyIds = null;
      dirty = true;
      refresh();
      return;
    }
    try {
      const { ids } = await app.api.get(`/api/history/search?q=${encodeURIComponent(q)}`);
      filters.bodyIds = new Set(ids);
    } catch (e) {
      toast("err", "No se pudo buscar", e.message);
      filters.bodyIds = null;
    }
    dirty = true;
    refresh();
  }, 260);

  search.addEventListener("input", () => {
    filters.text = search.value;
    filters.terms = parseQuery(search.value);
    if (filters.bodyMode) runBodySearch();
    dirty = true;
    refresh();
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); body.focus(); if (!selected && view.length) selectIndex(0); }
    if (e.key === "Escape") { search.value = ""; search.dispatchEvent(new Event("input")); }
  });
  bodyBtn.addEventListener("click", () => {
    filters.bodyMode = !filters.bodyMode;
    bodyBtn.classList.toggle("active", filters.bodyMode);
    search.placeholder = filters.bodyMode
      ? "Buscar en headers y cuerpos…"
      : "Filtrar  (method:POST  status:4xx  -cdn)";
    runBodySearch.flush();
  });
  onlyScope.addEventListener("change", () => { filters.onlyScope = onlyScope.checked; persist("history.onlyScope", filters.onlyScope); dirty = true; refresh(); });

  function setFilter(key, value) {
    filters[key] = value;
    persist(`history.${key}`, value);
    dirty = true;
    refresh();
  }

  function viewMenu(anchor) {
    const r = anchor.getBoundingClientRect();
    const src = (v, label) => ({ label, checked: filters.source === v, onClick: () => setFilter("source", v) });
    openMenu(r.left, r.bottom + 4, [
      { header: "Ocultar" },
      { label: "Estáticos (imágenes, CSS, fuentes)", checked: filters.hideStatic, onClick: () => setFilter("hideStatic", !filters.hideStatic) },
      { label: "Tráfico interno del navegador", checked: filters.hideNoise, onClick: () => setFilter("hideNoise", !filters.hideNoise) },
      "sep",
      { header: "Origen" },
      src("all", "Proxy y Repeater"), src("proxy", "Solo proxy"), src("repeater", "Solo Repeater"),
    ]);
  }

  // --- render -----------------------------------------------------------------------------
  function refresh() {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      if (dirty) recompute();
      render();
    });
  }

  function rowHtml(f, i) {
    const cls = ["grid-row"];
    if (f.id === selected || multi.has(f.id)) cls.push("selected");
    if (!f.in_scope) cls.push("oos");
    if (f.color) cls.push(`tint-${f.color}`);
    if (fresh.has(f.id)) cls.push("fresh");
    let st;
    if (f.state === "pending") st = '<span class="pending-dot" title="esperando respuesta"></span>';
    else if (!f.status_code) st = `<span class="s-x" title="${esc(f.error || "error")}">ERR</span>`;
    else st = `<span class="${statusClass(f.status_code)}">${f.status_code}</span>`;
    const scheme = f.scheme === "http" ? '<span class="t-dim">http://</span>' : "";
    const tag = f.color ? `<span class="tagdot color-${f.color}"></span>` : "";
    const title = f.comment ? `${f.path}\n— ${f.comment}` : f.path;
    return `<div class="${cls.join(" ")}" style="top:${i * ROW_H}px" data-i="${i}">` +
      `<div class="num seq">${tag}${f.seq}</div>` +
      `<div class="${methodClass(f.method)}">${esc(f.method)}</div>` +
      `<div class="host" title="${esc(flowUrl(f))}">${f.source === "repeater" ? '<span class="src-r">R</span>' : ""}${scheme}${esc(hostLabel(f))}</div>` +
      `<div class="path" title="${esc(title)}">${esc(f.path)}${f.edited ? '<span class="edited">EDIT</span>' : ""}${f.comment ? ' <span class="t-dim">✎</span>' : ""}</div>` +
      `<div class="num">${st}</div>` +
      `<div class="num meta">${f.length == null ? "—" : fmtBytes(f.length)}</div>` +
      `<div class="meta">${kindOf(f) || "—"}</div>` +
      `<div class="num meta">${f.state === "pending" ? "…" : fmtMs(f.duration_ms)}</div>` +
      `<div class="meta">${fmtClock(f.ts)}</div></div>`;
  }

  function render() {
    for (const c of head.children) {
      const on = c.dataset.key === sort.key;
      c.classList.toggle("sorted", on);
      c.textContent = COLUMNS.find((x) => x.key === c.dataset.key).label + (on ? (sort.dir > 0 ? " ↑" : " ↓") : "");
    }
    for (const c of chips.children) c.classList.toggle("on", filters.status.has(c.dataset.c));
    const custom = (filters.hideStatic ? 1 : 0) + (filters.source !== "all" ? 1 : 0) + (filters.hideNoise ? 0 : 1);
    viewLbl.textContent = custom ? `Vista · ${custom}` : "Vista";
    spacer.style.height = `${view.length * ROW_H}px`;
    if (follow) {
      body.scrollTop = sort.dir > 0 && (sort.key === "seq" || sort.key === "ts") ? body.scrollHeight : body.scrollTop;
    }
    const top = body.scrollTop;
    const height = body.clientHeight || 600;
    const start = Math.max(0, Math.floor(top / ROW_H) - 6);
    const end = Math.min(view.length, Math.ceil((top + height) / ROW_H) + 6);
    let html = "";
    for (let i = start; i < end; i++) html += rowHtml(view[i], i);
    // conservar el spacer y reemplazar solo las filas
    while (spacer.nextSibling) spacer.nextSibling.remove();
    spacer.insertAdjacentHTML("afterend", html);
    fresh.clear();
    const filtered = view.length !== flows.length;
    countLbl.textContent = filtered ? `${fmtInt(view.length)} de ${fmtInt(flows.length)}` : `${fmtInt(flows.length)} flows`;
    if (total > flows.length) countLbl.textContent += ` (últimos de ${fmtInt(total)})`;
    // vacíos
    if (!loaded) {
      emptyHost.hidden = true;
    } else if (!flows.length) {
      showEmpty(emptyState({
        icon: "globe",
        title: "Todavía no pasó tráfico por Janus",
        text: `Abrí el navegador Janus (ya viene configurado) o apuntá tu navegador o app al proxy ${app.proxyAddress()}.`,
        actions: [
          h("button.btn.primary", { onclick: () => app.launchBrowser() }, icon("globe"), "Abrir navegador Janus"),
          h("button.btn", { onclick: () => app.go("setup") }, icon("shield"), "Guía de configuración"),
        ],
      }));
    } else if (!view.length) {
      showEmpty(emptyState({
        icon: "search", title: "Ningún flow coincide",
        text: "Probá con otro filtro o mostrá todo.",
        actions: [h("button.btn", { onclick: resetFilters }, "Limpiar filtros")],
      }));
    } else {
      emptyHost.hidden = true;
    }
  }

  function showEmpty(node) {
    emptyHost.replaceChildren(node);
    emptyHost.style.top = "30px";
    emptyHost.hidden = false;
  }

  function resetFilters() {
    search.value = "";
    filters.text = "";
    filters.terms = [];
    filters.status.clear();
    filters.onlyScope = onlyScope.checked = false;
    filters.hideStatic = false;
    filters.source = "all";
    filters.bodyMode = false;
    filters.bodyIds = null;
    bodyBtn.classList.remove("active");
    persist("history.status", []);
    persist("history.onlyScope", false);
    persist("history.hideStatic", false);
    persist("history.source", "all");
    dirty = true;
    refresh();
  }

  body.addEventListener("scroll", () => {
    const atEnd = body.scrollTop + body.clientHeight >= body.scrollHeight - ROW_H * 1.5;
    follow = atEnd;
    refresh();
  }, { passive: true });
  new ResizeObserver(() => refresh()).observe(body);

  // --- selección ------------------------------------------------------------------------------
  function indexOfId(id) {
    return view.findIndex((f) => f.id === id);
  }

  function ensureVisible(i) {
    const top = i * ROW_H;
    if (top < body.scrollTop) body.scrollTop = top;
    else if (top + ROW_H > body.scrollTop + body.clientHeight) body.scrollTop = top + ROW_H - body.clientHeight;
  }

  function selectIndex(i, { additive = false, range = false } = {}) {
    if (i < 0 || i >= view.length) return;
    const f = view[i];
    if (range && anchorIdx != null) {
      multi.clear();
      const [a, b] = [Math.min(anchorIdx, i), Math.max(anchorIdx, i)];
      for (let k = a; k <= b; k++) multi.add(view[k].id);
    } else if (additive) {
      if (multi.has(f.id)) multi.delete(f.id);
      else multi.add(f.id);
      if (selected && !multi.size) multi.add(selected);
      anchorIdx = i;
    } else {
      multi.clear();
      anchorIdx = i;
    }
    const changed = selected !== f.id;
    selected = f.id;
    follow = false;
    ensureVisible(i);
    refresh();
    if (changed) loadDetail(f.id);
  }

  function selectionIds() {
    if (multi.size) return [...multi];
    return selected ? [selected] : [];
  }

  body.addEventListener("mousedown", (e) => {
    const row = e.target.closest(".grid-row");
    if (!row) return;
    const i = Number(row.dataset.i);
    if (e.button === 2) {
      const id = view[i].id;
      if (!multi.has(id) && selected !== id) selectIndex(i);
      return;
    }
    selectIndex(i, { additive: e.ctrlKey || e.metaKey, range: e.shiftKey });
  });
  body.addEventListener("dblclick", (e) => {
    const row = e.target.closest(".grid-row");
    if (row) app.sendToRepeater(view[Number(row.dataset.i)].id);
  });
  body.addEventListener("contextmenu", (e) => {
    const row = e.target.closest(".grid-row");
    if (!row) return;
    e.preventDefault();
    rowMenu(e.clientX, e.clientY, selectionIds());
  });
  body.addEventListener("keydown", (e) => {
    const i = selected ? indexOfId(selected) : -1;
    const page = Math.max(1, Math.floor(body.clientHeight / ROW_H) - 1);
    const go = (n) => { e.preventDefault(); selectIndex(Math.max(0, Math.min(view.length - 1, n)), { range: e.shiftKey }); };
    switch (e.key) {
      case "ArrowDown": return go(i + 1);
      case "ArrowUp": return go(i - 1);
      case "PageDown": return go(i + page);
      case "PageUp": return go(i - page);
      case "Home": return go(0);
      case "End": return go(view.length - 1);
      case "Delete": e.preventDefault(); return deleteFlows(selectionIds());
      default:
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
      e.preventDefault();
      multi.clear();
      view.forEach((f) => multi.add(f.id));
      refresh();
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && !window.getSelection()?.toString()) {
      e.preventDefault();
      const urls = selectionIds().map((id) => byId.get(id)).filter(Boolean).map(flowUrl);
      copyText(urls.join("\n")).then(() => toast("ok", urls.length > 1 ? `${urls.length} URLs copiadas` : "URL copiada"));
    }
  });

  // --- detalle -------------------------------------------------------------------------------
  let detailAbort = null;
  async function loadDetail(id) {
    detailAbort?.abort();
    const f = byId.get(id);
    if (!f) {
      detailHost.replaceChildren(detailEmpty);
      return;
    }
    if (detailHost.firstChild !== detail) detailHost.replaceChildren(detail);
    renderMeta(f);
    detailAbort = new AbortController();
    try {
      const d = await app.api.get(`/api/history/${id}`, { signal: detailAbort.signal });
      if (selected !== id) return;
      Object.assign(f, { client_addr: d.client_addr, server_addr: d.server_addr });
      renderMeta(f);
      reqViewer.set(d.request);
      if (d.response) resViewer.set(d.response);
      else if (f.state === "error") resViewer.setError(d.error || "el flow terminó con error");
      else resViewer.setEmpty("Esperando la respuesta del servidor…", { icon: "clock", title: "En curso" });
    } catch (e) {
      if (e.name === "AbortError") return;
      if (e.status === 404) {
        reqViewer.setEmpty("Este flow ya no existe.");
        resViewer.setEmpty("—");
      } else {
        toast("err", "No se pudo cargar el flow", e.message);
      }
    }
  }

  function renderMeta(f) {
    metaUrl.textContent = `${f.method} ${flowUrl(f)}`;
    metaUrl.title = flowUrl(f);
    const bits = [];
    if (f.status_code) bits.push(h(`b.${statusClass(f.status_code)}`, `${f.status_code} ${f.reason || ""}`.trim()));
    else if (f.state === "error") bits.push(h("b.s-x", f.error || "error"));
    else bits.push(h("span", "en curso…"));
    if (f.length != null) bits.push(h("span", fmtBytes(f.length)));
    if (f.duration_ms != null) bits.push(h("span", fmtMs(f.duration_ms)));
    if (f.server_addr) bits.push(h("span", { title: "servidor" }, `→ ${f.server_addr}`));
    if (f.source === "repeater") bits.push(h("span.badge.res", "repeater"));
    if (f.edited) bits.push(h("span.badge.warn", "editado"));
    if (!f.in_scope) bits.push(h("span.badge", "fuera de scope"));
    if (f.comment) bits.push(h("span", { title: f.comment, style: { color: "var(--text-2)" } }, icon("comment", "sm"), ` ${f.comment}`));
    metaInfo.replaceChildren(...bits);
  }

  // --- acciones ------------------------------------------------------------------------------------
  async function copyCurl(id) {
    if (!id) return;
    try {
      const text = await app.api.get(`/api/history/${id}/curl`);
      await copyText(text);
      toast("ok", "Copiado como curl");
    } catch (e) {
      toast("err", "No se pudo copiar", e.message);
    }
  }

  function openInBrowser() {
    const f = selected && byId.get(selected);
    if (f) app.launchBrowser(flowUrl(f));
  }

  async function setMeta(ids, changes) {
    for (const id of ids) {
      try {
        const row = await app.api.patch(`/api/history/${id}`, changes);
        mergeFlow(row);
      } catch (e) {
        toast("err", "No se pudo guardar", e.message);
        return;
      }
    }
  }

  async function deleteFlows(ids) {
    if (!ids.length) return;
    try {
      await app.api.post("/api/history/delete", { ids });
    } catch (e) {
      toast("err", "No se pudo borrar", e.message);
    }
  }

  async function clearAll() {
    if (!flows.length) return;
    const ok = await confirmDialog({
      title: "Limpiar historial",
      message: `Se van a borrar los ${fmtInt(total || flows.length)} flows guardados. Esto no se puede deshacer.`,
      confirmLabel: "Borrar todo",
      danger: true,
    });
    if (ok) {
      try { await app.api.del("/api/history"); } catch (e) { toast("err", "No se pudo limpiar", e.message); }
    }
  }

  function rowMenu(x, y, ids) {
    const f = byId.get(ids[0]);
    if (!f) return;
    const many = ids.length > 1;
    openMenu(x, y, [
      many ? { header: `${ids.length} flows seleccionados` } : null,
      { label: "Enviar a Repeater", icon: "repeat", kbd: "Ctrl+R", onClick: () => ids.forEach((id) => app.sendToRepeater(id, { quiet: many })) },
      { label: "Abrir en el navegador Janus", icon: "external", disabled: many, onClick: () => app.launchBrowser(flowUrl(f)) },
      "sep",
      { label: many ? "Copiar URLs" : "Copiar URL", icon: "link", kbd: "Ctrl+C", onClick: () => copyText(ids.map((id) => flowUrl(byId.get(id))).join("\n")).then(() => toast("ok", "Copiado")) },
      { label: "Copiar como curl", icon: "terminal", disabled: many, onClick: () => copyCurl(f.id) },
      "sep",
      { label: `Agregar ${f.host} al scope`, icon: "target", onClick: () => app.addScopeRule("include", f.host) },
      { label: `Excluir ${f.host} del scope`, icon: "ban", onClick: () => app.addScopeRule("exclude", f.host) },
      "sep",
      { header: "Color" },
      { colors: COLORS, onPick: (c) => setMeta(ids, { color: c }) },
      { label: f.comment ? "Editar comentario…" : "Agregar comentario…", icon: "comment", onClick: async () => {
        const text = await promptDialog({ title: "Comentario", value: f.comment || "", multiline: true });
        if (text !== null) setMeta(ids, { comment: text.trim() || null });
      } },
      "sep",
      { label: many ? `Borrar ${ids.length} flows` : "Borrar", icon: "trash", kbd: "Supr", danger: true, onClick: () => deleteFlows(ids) },
    ]);
  }

  // --- datos -------------------------------------------------------------------------------------------
  function mergeFlow(row) {
    const f = byId.get(row.id);
    if (f) Object.assign(f, row);
    dirty = true;
    refresh();
    if (row.id === selected) renderMeta(byId.get(row.id));
  }

  async function load() {
    try {
      const data = await app.api.get("/api/history?limit=10000");
      flows.length = 0;
      byId.clear();
      for (const f of data.flows) {
        flows.push(f);
        byId.set(f.id, f);
      }
      total = data.total;
      loaded = true;
      dirty = true;
      follow = true;
      refresh();
      if (selected && byId.has(selected)) loadDetail(selected);
    } catch (e) {
      toast("err", "No se pudo cargar el historial", e.message);
    }
  }

  app.on("flow.new", (f) => {
    if (byId.has(f.id)) return mergeFlow(f);
    flows.push(f);
    byId.set(f.id, f);
    total += 1;
    fresh.add(f.id);
    if (!dirty && sort.key === "seq" && sort.dir === 1) {
      if (passes(f)) view.push(f);
    } else {
      dirty = true;
    }
    refresh();
  });
  app.on("flow.update", (row) => {
    const f = byId.get(row.id);
    if (!f) return;
    const wasPending = f.state === "pending";
    Object.assign(f, row);
    dirty = true;
    refresh();
    if (row.id === selected) {
      renderMeta(f);
      if (wasPending && f.state !== "pending") loadDetail(f.id);
    }
  });
  app.on("flows.deleted", ({ ids }) => {
    const gone = new Set(ids);
    for (let i = flows.length - 1; i >= 0; i--) if (gone.has(flows[i].id)) flows.splice(i, 1);
    ids.forEach((id) => { byId.delete(id); multi.delete(id); });
    total = Math.max(0, total - ids.length);
    if (selected && gone.has(selected)) {
      selected = null;
      detailHost.replaceChildren(detailEmpty);
    }
    dirty = true;
    refresh();
  });
  app.on("history.cleared", () => {
    flows.length = 0;
    byId.clear();
    multi.clear();
    selected = null;
    total = 0;
    detailHost.replaceChildren(detailEmpty);
    dirty = true;
    refresh();
  });
  app.on("scope.changed", ({ changed }) => {
    for (const [id, inScope] of changed || []) {
      const f = byId.get(id);
      if (f) f.in_scope = inScope;
    }
    dirty = true;
    refresh();
  });
  app.on("resync", load);

  load();

  return {
    id: "history",
    el,
    onShow() { refresh(); },
    focusSearch() { search.focus(); search.select(); },
    shortcut(e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "r" && selected) {
        selectionIds().forEach((id) => app.sendToRepeater(id, { quiet: multi.size > 1 }));
        return true;
      }
      return false;
    },
    flowById: (id) => byId.get(id),
  };
}
