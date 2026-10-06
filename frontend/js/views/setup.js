// Vista Setup: navegador Janus, CA, proxy del sistema y configuración manual.

import { confirmDialog, copyText, h, icon, saveBlob, toast } from "../dom.js";

export function createSetup(app) {
  let ca = null;
  let browsers = [];
  let chosen = localStorage.getItem("janus.browser") || null;
  let sys = { supported: false, enabled: false };

  // --- navegador ----------------------------------------------------------------------------
  const pick = h("div.browser-pick");
  const urlIn = h("input.input.mono", { placeholder: "URL inicial (opcional): https://target.com", spellcheck: false, style: { flex: "1", minWidth: "220px" } });
  const launchBtn = h("button.btn.primary", { onclick: () => launch() }, icon("globe"), "Abrir navegador Janus");
  urlIn.addEventListener("keydown", (e) => { if (e.key === "Enter") launch(); });
  const browserNote = h("p.hint");
  const browserCard = h("div.card.hero",
    h("div.card-head", h("div.ci", icon("globe")),
      h("div", h("h3", "Navegador Janus"), h("div.sub", "La forma más rápida de empezar"))),
    h("p", "Abre un perfil aislado de tu navegador con el proxy y el certificado de Janus ya configurados. ",
      "No toca tu navegador de siempre ni sus cookies; el tráfico de ", h("code", "localhost"), " también se captura."),
    pick, h("div.row", urlIn, launchBtn), browserNote);

  // --- CA -------------------------------------------------------------------------------------
  const caStatus = h("span.badge");
  const caKv = h("dl.kv");
  const caActions = h("div.row");
  const caHint = h("p.hint");
  const caCard = h("div.card",
    h("div.card-head", h("div.ci", icon("award")),
      h("div", h("h3", "Certificado de Janus (CA)"), h("div.sub", "Para descifrar HTTPS sin advertencias")), h("span.spacer"), caStatus),
    caKv, caActions, caHint);

  // --- proxy del sistema ------------------------------------------------------------------------
  const sysToggle = h("input", { type: "checkbox" });
  const sysState = h("p.hint");
  const sysCard = h("div.card",
    h("div.card-head", h("div.ci", icon("network")),
      h("div", h("h3", "Proxy del sistema"), h("div.sub", "Capturar todas las apps de Windows")), h("span.spacer"),
      h("label.switch.big", sysToggle)),
    h("p", "Hace que Windows (Edge, Chrome, apps .NET, instaladores…) mande su tráfico por Janus. ",
      "La configuración anterior se guarda y se restaura al apagarlo o al cerrar Janus."),
    sysState);
  sysToggle.addEventListener("change", () => setSysProxy(sysToggle.checked));

  // --- manual ---------------------------------------------------------------------------------------
  const addrBox = h("span");
  const manualCard = h("div.card",
    h("div.card-head", h("div.ci", icon("monitor")), h("div", h("h3", "Configuración manual"), h("div.sub", "Otras apps, Firefox y línea de comandos"))),
    h("div.copybox", addrBox, h("button.icon-btn", { title: "Copiar", onclick: () => copyText(app.proxyAddress()).then(() => toast("ok", "Dirección copiada")) }, icon("copy", "sm"))),
    h("ol.steps",
      h("li", "Apuntá la app al proxy HTTP de arriba (sirve para HTTP y HTTPS)."),
      h("li", "Instalá la CA de Janus para que confíe en los certificados que genera."),
      h("li", "Firefox: usá el navegador Janus o activá ", h("code", "security.enterprise_roots.enabled"), "."),
      h("li", "curl: ", h("code.cmd-curl", "")),
      h("li", "PowerShell: ", h("code.cmd-ps", "")),
    ));

  const mobileCard = h("div.card",
    h("div.card-head", h("div.ci", icon("phone")), h("div", h("h3", "Celulares y otros equipos"), h("div.sub", "Android, iOS, VMs"))),
    h("ol.steps",
      h("li", "En Ajustes → Proxy, elegí ", h("b", "Toda la red (0.0.0.0)"), " y permití a Janus en el firewall de Windows."),
      h("li", "En el dispositivo, configurá el proxy Wi-Fi con la IP de esta PC y el puerto de Janus."),
      h("li", "Abrí ", h("code", "http://mitm.it"), " desde el dispositivo para bajar e instalar el certificado.")),
    h("div.row", h("button.btn", { onclick: () => app.openSettings() }, icon("gear"), "Abrir ajustes del proxy")));

  const el = h("section.view", h("div.doc", h("div.doc-inner",
    h("h2", "Setup"),
    h("p.lead", "Todo lo necesario para que el tráfico pase por Janus y se pueda leer en claro."),
    h("div.cards", browserCard, caCard, sysCard, manualCard, mobileCard))));

  // --- render ------------------------------------------------------------------------------------------
  function renderBrowsers() {
    if (!browsers.length) {
      pick.replaceChildren(h("span.hint", "No se encontró Chrome, Edge, Brave ni Firefox."));
      launchBtn.disabled = true;
      return;
    }
    launchBtn.disabled = false;
    if (!browsers.some((b) => b.id === chosen)) chosen = (browsers.find((b) => b.engine === "chromium") || browsers[0]).id;
    pick.replaceChildren(...browsers.map((b) => h(`button${b.id === chosen ? ".on" : ""}`, {
      title: b.path,
      onclick: () => { chosen = b.id; localStorage.setItem("janus.browser", chosen); renderBrowsers(); },
    }, b.name)));
    const b = browsers.find((x) => x.id === chosen);
    browserNote.textContent = b?.engine === "firefox"
      ? "Firefox no acepta la CA por parámetro: confía en ella solo si la instalaste en Windows (tarjeta de al lado)."
      : "El perfil se guarda en la carpeta de datos de Janus: tus logins de prueba quedan entre sesiones.";
  }

  function renderCa() {
    if (!ca) return;
    const installed = ca.installed;
    caStatus.className = `badge ${installed ? "solid-ok" : "solid-err"}`;
    caStatus.textContent = installed ? "Instalada" : "No instalada";
    caStatus.hidden = installed == null; // fuera de Windows no se puede saber
    const until = new Date(ca.not_after);
    caKv.replaceChildren(
      h("dt", "Nombre"), h("dd", ca.cn || "—"),
      h("dt", "SHA-256"), h("dd", { title: ca.sha256 }, ca.sha256.slice(0, 47) + "…"),
      h("dt", "Válida hasta"), h("dd", until.toLocaleDateString("es", { year: "numeric", month: "long", day: "numeric" })),
    );
    const buttons = [];
    if (ca.store_supported) {
      buttons.push(installed
        ? h("button.btn", { onclick: () => uninstallCa() }, icon("x"), "Quitar de Windows")
        : h("button.btn.primary", { onclick: () => installCa() }, icon("shield"), "Instalar en Windows"));
    }
    buttons.push(
      h("button.btn", { onclick: () => download("cer") }, icon("download"), ".cer"),
      h("button.btn", { onclick: () => download("pem") }, icon("download"), ".pem"),
      h("button.btn.ghost", { onclick: () => app.openFolder("ca") }, icon("folder"), "Abrir carpeta"));
    caActions.replaceChildren(...buttons);
    caHint.textContent = ca.store_supported
      ? (installed
        ? "Chrome, Edge y las apps de Windows ya confían en Janus para tu usuario."
        : "Se instala solo para tu usuario (no requiere administrador). Windows te va a pedir confirmación: elegí «Sí».")
      : "Instalala en el almacén de certificados de tu sistema o del navegador.";
  }

  function renderSys() {
    sysCard.hidden = !sys.supported;
    sysToggle.checked = !!sys.enabled;
    if (sys.enabled) {
      sysState.className = "hint warn";
      sysState.textContent = `Activo: Windows usa ${app.proxyAddress()}. Si cerrás Janus se restaura solo.`;
    } else {
      sysState.className = "hint";
      sysState.textContent = sys.current
        ? `Ahora Windows usa otro proxy: ${sys.current}. Se va a restaurar al apagar.`
        : sys.pac ? "Windows usa un script PAC; se desactiva mientras esto esté prendido." : "Apagado.";
    }
  }

  function renderManual() {
    const addr = app.proxyAddress();
    addrBox.textContent = `http://${addr}`;
    el.querySelector(".cmd-curl").textContent = `curl -x http://${addr} -k https://target.com/`;
    el.querySelector(".cmd-ps").textContent = `Invoke-WebRequest https://target.com -Proxy http://${addr} -SkipCertificateCheck`;
  }

  // --- acciones --------------------------------------------------------------------------------------------
  async function launch() {
    await app.launchBrowser(urlIn.value.trim() || null, chosen);
  }

  async function installCa() {
    toast("info", "Confirmá el aviso de Windows", "Puede aparecer detrás de esta ventana.", 6000);
    try {
      ca = await app.api.post("/api/ca/install");
      renderCa();
      if (ca.installed) toast("ok", "CA instalada", "Reiniciá los navegadores que tengas abiertos.");
    } catch (e) {
      toast("err", "No se instaló la CA", e.message);
    }
  }

  async function uninstallCa() {
    const ok = await confirmDialog({
      title: "Quitar la CA",
      message: "Las apps dejarán de confiar en los certificados de Janus. Windows también te va a pedir confirmación.",
      confirmLabel: "Quitar", danger: true,
    });
    if (!ok) return;
    try {
      ca = await app.api.post("/api/ca/uninstall");
      renderCa();
      toast("ok", "CA quitada de Windows");
    } catch (e) {
      toast("err", "No se pudo quitar", e.message);
    }
  }

  async function download(fmt) {
    try {
      const blob = await app.api.get(`/api/ca/cert?format=${fmt}`);
      saveBlob(blob, `janus-ca.${fmt}`);
      toast("ok", `janus-ca.${fmt} guardado`, "Quedó en tu carpeta de Descargas.");
    } catch (e) {
      toast("err", "No se pudo descargar", e.message);
    }
  }

  async function setSysProxy(enabled) {
    if (enabled) {
      const ok = await confirmDialog({
        title: "Usar Janus como proxy del sistema",
        message: "Todo el tráfico web de Windows va a pasar por Janus mientras esté activo. Sin la CA instalada, muchas apps van a fallar en HTTPS.",
        confirmLabel: "Activar",
      });
      if (!ok) { sysToggle.checked = false; return; }
    }
    try {
      sys = await app.api.put("/api/sysproxy", { enabled });
      renderSys();
      app.setSysProxy(sys);
      toast("ok", enabled ? "Proxy del sistema activado" : "Proxy del sistema restaurado");
    } catch (e) {
      toast("err", "No se pudo cambiar el proxy del sistema", e.message);
      sysToggle.checked = !enabled;
    }
  }

  async function load() {
    const [caR, brR, sysR] = await Promise.allSettled([
      app.api.get("/api/ca"), app.api.get("/api/browsers"), app.api.get("/api/sysproxy"),
    ]);
    if (caR.status === "fulfilled") { ca = caR.value; app.setCa(ca); renderCa(); }
    if (brR.status === "fulfilled") { browsers = brR.value.browsers; app.browsers = browsers; renderBrowsers(); }
    if (sysR.status === "fulfilled") { sys = sysR.value; app.setSysProxy(sys); renderSys(); }
    renderManual();
  }

  app.on("ca.changed", (info) => { ca = info; renderCa(); app.setCa(ca); });
  app.on("sysproxy.changed", (s) => { sys = s; renderSys(); app.setSysProxy(s); });
  app.on("settings.changed", () => { renderManual(); renderSys(); });
  app.on("resync", load);
  load();

  return {
    id: "setup",
    el,
    onShow() { app.api.get("/api/ca").then((c) => { ca = c; renderCa(); app.setCa(ca); }).catch(() => {}); },
    get chosenBrowser() { return chosen; },
  };
}
