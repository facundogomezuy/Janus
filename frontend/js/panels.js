// Modal de ajustes y panel de eventos.

import { confirmDialog, fmtClock, h, icon, modal, toast } from "./dom.js";
import { getTheme, setTheme } from "./theme.js";

export function openSettings(app) {
  const s = { ...app.settings };
  const port = h("input.input.mono", { type: "number", min: 1, max: 65535, value: s["proxy.listen_port"] });
  const listen = h("select.input",
    h("option", { value: "127.0.0.1" }, "Solo este equipo (127.0.0.1)"),
    h("option", { value: "0.0.0.0" }, "Toda la red (0.0.0.0)"));
  listen.value = ["127.0.0.1", "0.0.0.0"].includes(s["proxy.listen_host"]) ? s["proxy.listen_host"] : "127.0.0.1";
  const listenWarn = h("div.hint.warn", { hidden: listen.value !== "0.0.0.0" },
    "Cualquier equipo de tu red podrá usar el proxy. Windows puede pedirte permiso en el firewall.");
  listen.addEventListener("change", () => { listenWarn.hidden = listen.value !== "0.0.0.0"; });
  const upstream = h("input.input.mono", { placeholder: "http://proxy-corporativo:3128 (opcional)", value: s["proxy.upstream"] || "" });
  const verify = h("input", { type: "checkbox", checked: !s["proxy.ssl_insecure"] });
  const http2 = h("input", { type: "checkbox", checked: s["proxy.http2"] });
  const stream = h("input.input.mono", { value: s["proxy.stream_large_bodies"] || "", placeholder: "8m", style: { width: "110px" } });

  const theme = h("div.segmented", ["dark", "light", "system"].map((t) =>
    h("button", { dataset: { t }, onclick: () => { setTheme(t); syncTheme(); } }, { dark: "Oscuro", light: "Claro", system: "Sistema" }[t])));
  const syncTheme = () => { for (const b of theme.children) b.classList.toggle("on", b.dataset.t === getTheme()); };
  syncTheme();

  const err = h("div.form-error");
  const field = (label, hint, control) => h("div.field", h("label", label, hint ? h("small", hint) : null), control);

  const body = h("div",
    h("div.form-sec", h("h4", "Proxy"),
      field("Puerto", "Donde escucha el proxy HTTP(S).", port),
      field("Escuchar en", null, h("div", listen, listenWarn)),
      field("Proxy upstream", "Encadenar con otro proxy (corporativo, Burp, Tor…).", upstream),
      field("Verificar certificados", "Rechazar servidores con TLS inválido. Apagado es lo habitual en pentesting.", h("label.switch", verify)),
      field("HTTP/2", "Negociar HTTP/2 con clientes y servidores.", h("label.switch", http2)),
      field("Streamear cuerpos de más de", "Las descargas grandes pasan sin guardarse (ej. 8m).", stream)),
    h("div.form-sec", h("h4", "Apariencia"), field("Tema", null, theme)),
    h("div.form-sec", h("h4", "Datos"),
      h("div.row", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
        h("button.btn", { onclick: () => app.openFolder("data") }, icon("folder"), "Carpeta de datos"),
        h("button.btn", { onclick: () => app.openFolder("logs") }, icon("file"), "Logs"),
        h("button.btn.danger", { onclick: async () => {
          if (await confirmDialog({ title: "Limpiar historial", message: "Se borran todos los flows guardados.", confirmLabel: "Borrar todo", danger: true })) {
            await app.api.del("/api/history").catch((e) => toast("err", "No se pudo", e.message));
          }
        } }, icon("trash"), "Limpiar historial")),
      h("p.hint", { style: { marginTop: "10px" } }, `Janus ${app.version} · motor mitmproxy · datos en ${app.status?.data_dir || "—"}`)),
    err);

  modal({
    title: "Ajustes",
    body,
    actions: [
      { label: "Cancelar" },
      {
        label: "Guardar",
        primary: true,
        onClick: async () => {
          const changes = {
            "proxy.listen_port": Number(port.value),
            "proxy.listen_host": listen.value,
            "proxy.upstream": upstream.value.trim(),
            "proxy.ssl_insecure": !verify.checked,
            "proxy.http2": http2.checked,
            "proxy.stream_large_bodies": stream.value.trim(),
          };
          for (const k of Object.keys(changes)) if (changes[k] === s[k]) delete changes[k];
          if (!Object.keys(changes).length) return true;
          try {
            app.settings = await app.api.put("/api/settings", changes);
            toast("ok", "Ajustes guardados", changes["proxy.listen_port"] ? `El proxy ahora escucha en el puerto ${changes["proxy.listen_port"]}.` : "");
            return true;
          } catch (e) {
            err.textContent = e.message;
            return false;
          }
        },
      },
    ],
  });
}

const LEVEL_ICON = { warn: "alert", error: "x-circle", info: "info" };

export function createEventsDrawer(app) {
  let el = null;
  let items = [];
  const list = h("div.drawer-body");

  function render() {
    if (!el) return;
    if (!items.length) {
      list.replaceChildren(h("div.empty", h("div.art", icon("bell")), h("h3", "Sin eventos"),
        h("p", "Acá aparecen los avisos del proxy: certificados rechazados, puertos ocupados, errores de conexión.")));
      return;
    }
    list.replaceChildren(...[...items].reverse().map((ev) => h(`div.event.${ev.level}`,
      icon(LEVEL_ICON[ev.level] || "info", "sm ei"),
      h("div", h("div.em", ev.message), ev.hint ? h("div.eh", ev.hint) : null,
        h("div.et", `${fmtClock(ev.ts)} · ${ev.source}`)))));
  }

  function open() {
    if (el) return close();
    el = h("aside.drawer",
      h("div.drawer-head", h("h3", "Eventos"),
        h("button.btn.sm.ghost", { onclick: async () => {
          await app.api.del("/api/events").catch(() => {});
          items = [];
          app.setEventCount(0);
          render();
        } }, "Limpiar"),
        h("button.icon-btn", { title: "Cerrar", onclick: () => close() }, icon("x"))),
      list);
    document.body.append(el);
    app.setEventCount(items.length, { seen: true });
    render();
  }

  function close() {
    el?.remove();
    el = null;
  }

  app.on("proxy.event", (ev) => {
    items.push(ev);
    if (items.length > 300) items.shift();
    app.setEventCount(items.length, { seen: !!el, latest: ev });
    render();
  });
  const load = async () => {
    try {
      items = (await app.api.get("/api/events")).events;
      app.setEventCount(items.length, { seen: true });
      render();
    } catch { /* */ }
  };
  app.on("resync", load);
  load();
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && el && !document.querySelector(".modal-backdrop")) close(); });

  return { open, close, get isOpen() { return !!el; } };
}
