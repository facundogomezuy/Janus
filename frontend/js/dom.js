// Helpers de UI: construcción de DOM, íconos, toasts, menús, modales, splitters.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** h("div.a.b#id", {attrs}, ...hijos) */
export function h(spec, attrs, ...kids) {
  const parts = spec.split(/(?=[.#])/);
  const tag = parts[0] && !/^[.#]/.test(parts[0]) ? parts.shift() : "div";
  const el = document.createElement(tag);
  for (const p of parts) {
    if (p[0] === ".") el.classList.add(p.slice(1));
    else if (p[0] === "#") el.id = p.slice(1);
  }
  if (attrs != null && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    kids.unshift(attrs);
    attrs = null;
  }
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (v === false) {
        // propiedades booleanas que el navegador trae en true (spellcheck, draggable…)
        if (typeof el[k] === "boolean") el[k] = false;
        continue;
      }
      if (k === "class") v.split(/\s+/).filter(Boolean).forEach((c) => el.classList.add(c));
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k in el && typeof v !== "string") el[k] = v;
      else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  append(el, kids);
  return el;
}

export function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

// --- íconos (trazos estilo Lucide, 24x24) ------------------------------------
const ICONS = {
  history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
  hand: '<path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2"/><path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2"/><path d="M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
  repeat: '<path d="M4 12a8 8 0 0 1 8-8 8 8 0 0 1 6.9 4"/><path d="M20 4v4h-4"/><path d="M20 12a8 8 0 0 1-8 8 8 8 0 0 1-6.9-4"/><path d="M4 20v-4h4"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  search: '<circle cx="11" cy="11" r="7.5"/><path d="m20.5 20.5-4.2-4.2"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><path d="M10 11v6M14 11v6"/>',
  send: '<path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"/><path d="m21.854 2.147-10.94 10.939"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  folder: '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  "x-circle": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
  "check-circle": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  "chevron-down": '<path d="m6 9 6 6 6-6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  play: '<path d="M6 3.5v17a.5.5 0 0 0 .77.42l13-8.5a.5.5 0 0 0 0-.84l-13-8.5A.5.5 0 0 0 6 3.5z"/>',
  forward: '<path d="M5 4.5v15a.5.5 0 0 0 .8.4l10-7.5a.5.5 0 0 0 0-.8l-10-7.5a.5.5 0 0 0-.8.4z"/><path d="M19 5v14"/>',
  ban: '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
  external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  unlock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
  monitor: '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8M12 17v4"/>',
  award: '<path d="m15.477 12.89 1.515 8.526a.5.5 0 0 1-.81.47l-3.58-2.687a1 1 0 0 0-1.197 0l-3.586 2.686a.5.5 0 0 1-.81-.469l1.514-8.526"/><circle cx="12" cy="8" r="6"/>',
  edit: '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>',
  more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
  terminal: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  bell: '<path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>',
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
  network: '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/><path d="M12 12V8"/>',
  phone: '<rect width="14" height="20" x="5" y="2" rx="2"/><path d="M12 18h.01"/>',
  wrap: '<path d="M3 6h18"/><path d="M3 12h15a3 3 0 1 1 0 6h-4"/><path d="m16 16-2 2 2 2"/><path d="M3 18h7"/>',
  eye: '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
  tag: '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5"/>',
  comment: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5 5.5 5.5 0 0 1-5.5 5.5H11"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
  layers: '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  "win-min": '<path d="M5 12h14"/>',
  "win-max": '<rect x="5.5" y="5.5" width="13" height="13" rx="1"/>',
  "win-restore": '<rect x="5.5" y="8.5" width="10" height="10" rx="1"/><path d="M8.5 8.5V6.5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-2"/>',
  "win-close": '<path d="M6 6l12 12M18 6 6 18"/>',
};

const SVG_NS = "http://www.w3.org/2000/svg";
export function icon(name, cls = "") {
  const s = document.createElementNS(SVG_NS, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", `i ${cls}`.trim());
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || ICONS.info;
  return s;
}

export function logoMark(size = 26) {
  const s = document.createElementNS(SVG_NS, "svg");
  s.setAttribute("viewBox", "0 0 64 64");
  s.setAttribute("width", size);
  s.setAttribute("height", size);
  s.setAttribute("fill", "none");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML =
    '<circle cx="32" cy="32" r="22" stroke="var(--teal)" stroke-width="4.2"/>' +
    '<path d="M27,23 L20,32 L27,41" stroke="var(--teal)" stroke-width="3.2"/>' +
    '<path d="M37,23 L44,32 L37,41" stroke="var(--teal)" stroke-width="3.2"/>' +
    '<path d="M33,25.5 L27.5,32 L33,38.5" stroke="var(--teal)" stroke-width="2.4" stroke-opacity="0.5"/>' +
    '<path d="M31,25.5 L36.5,32 L31,38.5" stroke="var(--teal)" stroke-width="2.4" stroke-opacity="0.5"/>';
  return s;
}

// --- formato ------------------------------------------------------------------------
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

export function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}
export function fmtMs(ms) {
  if (ms == null) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
}
export function fmtClock(ts) {
  const d = new Date(ts * 1000);
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
export function fmtAgo(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
export const fmtInt = (n) => (n ?? 0).toLocaleString("es");
export const fmtFlows = (n) => `${fmtInt(n)} ${n === 1 ? "flow" : "flows"}`;

export function debounce(fn, ms) {
  let t;
  const d = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  d.flush = (...args) => {
    clearTimeout(t);
    fn(...args);
  };
  d.cancel = () => clearTimeout(t);
  return d;
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function methodClass(m) {
  return ["GET", "POST", "PUT", "PATCH", "DELETE"].includes(m) ? `m-${m}` : "m-other";
}
export function statusClass(code) {
  if (!code) return "s-x";
  return `s-${String(code)[0]}`;
}

// --- portapapeles -------------------------------------------------------------------
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = h("textarea", { style: { position: "fixed", opacity: "0" } });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

export function saveBlob(data, filename, type = "application/octet-stream") {
  const blob = data instanceof Blob ? data : new Blob([data], { type });
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

// --- toasts ---------------------------------------------------------------------------
let toastHost;
const TOAST_ICONS = { ok: "check-circle", err: "x-circle", warn: "alert", info: "info" };
export function toast(kind, title, detail = "", ms = 3600) {
  toastHost ??= document.body.appendChild(h("div.toasts"));
  const el = h(`div.toast.${kind}`, icon(TOAST_ICONS[kind] || "info", "ti"),
    h("div", h("div.tt", title), detail ? h("div.td", detail) : null));
  toastHost.append(el);
  const close = () => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 200);
  };
  const t = setTimeout(close, ms);
  el.addEventListener("click", () => {
    clearTimeout(t);
    close();
  });
  while (toastHost.children.length > 4) toastHost.firstChild.remove();
}

// --- menú contextual ----------------------------------------------------------------------
let openMenuEl = null;
export function closeMenu() {
  if (openMenuEl) {
    openMenuEl.remove();
    openMenuEl = null;
  }
}
/** items: {label, icon, kbd, danger, disabled, onClick} | "sep" | {header} | {colors, value, onPick} */
export function openMenu(x, y, items) {
  closeMenu();
  const menu = h("div.menu", { role: "menu" });
  for (const it of items) {
    if (!it) continue;
    if (it === "sep") {
      menu.append(h("div.sep"));
    } else if (it.header) {
      menu.append(h("div.label", it.header));
    } else if (it.colors) {
      const row = h("div.colors");
      row.append(h("span.none", { title: "Sin color", onclick: () => { closeMenu(); it.onPick(null); } }));
      for (const c of it.colors) {
        row.append(h(`span.color-${c}`, { title: c, onclick: () => { closeMenu(); it.onPick(c); } }));
      }
      menu.append(row);
    } else {
      const lead = "checked" in it
        ? (it.checked ? icon("check", "sm") : h("span", { style: { width: "13px" } }))
        : (it.icon ? icon(it.icon, "sm") : h("span", { style: { width: "13px" } }));
      menu.append(h(`button${it.danger ? ".danger" : ""}`, {
        disabled: !!it.disabled,
        role: "checked" in it ? "menuitemcheckbox" : "menuitem",
        "aria-checked": "checked" in it ? String(!!it.checked) : null,
        onclick: () => { closeMenu(); it.onClick?.(); },
      }, lead, it.label, it.kbd ? h("span.k", it.kbd) : null));
    }
  }
  document.body.append(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(x, innerWidth - r.width - 6)}px`;
  menu.style.top = `${Math.min(y, innerHeight - r.height - 6)}px`;
  openMenuEl = menu;
}
document.addEventListener("mousedown", (e) => {
  if (openMenuEl && !openMenuEl.contains(e.target)) closeMenu();
}, true);
window.addEventListener("blur", closeMenu);
window.addEventListener("resize", closeMenu);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

// --- modales -------------------------------------------------------------------------------
export function modal({ title, body, actions = [], size = "", onClose } = {}) {
  const backdrop = h("div.modal-backdrop");
  const foot = h("div.modal-foot");
  const box = h(`div.modal${size ? "." + size : ""}`, { role: "dialog", "aria-modal": "true" },
    h("div.modal-head", h("h2", title), h("span.spacer"),
      h("button.icon-btn", { title: "Cerrar", onclick: () => close() }, icon("x"))),
    h("div.modal-body", body),
    actions.length ? foot : null);
  const close = (result) => {
    backdrop.remove();
    document.removeEventListener("keydown", onKey, true);
    onClose?.(result);
  };
  for (const a of actions) {
    const btn = h(`button.btn${a.primary ? ".primary" : ""}${a.danger ? ".danger" : ""}`, {
      onclick: async () => {
        if (a.onClick) {
          btn.disabled = true;
          try {
            if ((await a.onClick()) === false) return;
          } finally {
            btn.disabled = false;
          }
        }
        close(a.value);
      },
    }, a.label);
    foot.append(btn);
  }
  const onKey = (e) => {
    if (e.key === "Escape") { e.stopPropagation(); close(); }
  };
  document.addEventListener("keydown", onKey, true);
  // Solo un clic nuevo cierra: el 3.º de un triple clic (o el 2.º de un doble clic que
  // abrió el modal) cae sobre el fondo recién puesto y no debe cerrarlo.
  backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop && e.detail <= 1) close(); });
  backdrop.append(box);
  document.body.append(backdrop);
  // Foco inmediato (lo que se escriba enseguida no se pierde); el respaldo cubre a quien lo robe.
  const focusFirst = () => box.querySelector("input,textarea,select,button.primary")?.focus();
  focusFirst();
  setTimeout(() => { if (!box.contains(document.activeElement)) focusFirst(); }, 30);
  return { close, box };
}

export function confirmDialog({ title, message, confirmLabel = "Confirmar", danger = false }) {
  return new Promise((resolve) => {
    modal({
      title,
      size: "sm",
      body: h("p", message),
      actions: [
        { label: "Cancelar", value: false },
        { label: confirmLabel, primary: !danger, danger, value: true },
      ],
      onClose: (v) => resolve(v === true),
    });
  });
}

export function promptDialog({ title, label, value = "", placeholder = "", multiline = false, maxlength = null }) {
  return new Promise((resolve) => {
    const input = multiline
      ? h("textarea.input", { rows: 4, maxlength, style: { width: "100%", height: "96px", padding: "8px 10px", resize: "vertical" } })
      : h("input.input", { style: { width: "100%" }, placeholder, maxlength, spellcheck: false });
    input.value = value;
    let result = null;
    const m = modal({
      title,
      size: "sm",
      body: h("div", label ? h("p", label) : null, input),
      actions: [
        { label: "Cancelar" },
        { label: "Guardar", primary: true, onClick: () => { result = input.value; } },
      ],
      onClose: () => resolve(result),
    });
    // Una línea: escribir reemplaza (como al renombrar); multilínea: se sigue escribiendo al final.
    if (multiline) input.setSelectionRange(input.value.length, input.value.length);
    else input.select();
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (!multiline || e.ctrlKey)) {
        result = input.value;
        m.close();
      }
    });
  });
}

// --- splitter redimensionable ---------------------------------------------------------------
/** split(dir, a, b, {key, initial (0..1), min}) -> elemento contenedor */
export function split(dir, a, b, { key, initial = 0.5, min = 120 } = {}) {
  a.classList.add("pane-a");
  b.classList.add("pane-b");
  const gutter = h("div.gutter");
  const box = h(`div.split.${dir}`, a, gutter, b);
  const horizontal = dir === "h";
  let ratio = initial;
  try {
    const saved = key && parseFloat(localStorage.getItem(`janus.split.${key}`));
    if (saved > 0 && saved < 1) ratio = saved;
  } catch { /* sin storage */ }
  const apply = () => { a.style.flex = `0 0 ${(ratio * 100).toFixed(3)}%`; };
  apply();
  gutter.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    gutter.setPointerCapture(e.pointerId);
    gutter.classList.add("dragging");
    document.body.classList.add(horizontal ? "resizing-h" : "resizing-v");
    const rect = box.getBoundingClientRect();
    const total = horizontal ? rect.width : rect.height;
    const move = (ev) => {
      const pos = horizontal ? ev.clientX - rect.left : ev.clientY - rect.top;
      const clamped = Math.max(min, Math.min(total - min, pos));
      ratio = clamped / total;
      apply();
    };
    const up = () => {
      gutter.classList.remove("dragging");
      document.body.classList.remove("resizing-h", "resizing-v");
      gutter.removeEventListener("pointermove", move);
      gutter.removeEventListener("pointerup", up);
      try { if (key) localStorage.setItem(`janus.split.${key}`, ratio.toFixed(4)); } catch { /* */ }
    };
    gutter.addEventListener("pointermove", move);
    gutter.addEventListener("pointerup", up);
  });
  gutter.addEventListener("dblclick", () => { ratio = initial; apply(); });
  return box;
}

export function emptyState({ icon: ic, title, text, actions = [] }) {
  return h("div.empty",
    h("div.art", icon(ic)),
    h("h3", title),
    text ? h("p", text) : null,
    actions.length ? h("div.row", actions) : null);
}

export function store(key, fallback) {
  try {
    const v = localStorage.getItem(`janus.${key}`);
    return v == null ? fallback : JSON.parse(v);
  } catch {
    return fallback;
  }
}
export function persist(key, value) {
  try { localStorage.setItem(`janus.${key}`, JSON.stringify(value)); } catch { /* */ }
}
