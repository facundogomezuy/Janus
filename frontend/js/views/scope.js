// Vista Scope: reglas include/exclude + probador.

import { debounce, h, icon, toast } from "../dom.js";

export function createScope(app) {
  let rules = [];
  const list = h("div.rules");
  const errorLine = h("div.form-error");

  const kindSel = h("select.input", h("option", { value: "include" }, "Incluir"), h("option", { value: "exclude" }, "Excluir"));
  const patternIn = h("input.input.mono", { placeholder: "*.target.com · api.target.com:8443 · target.com/api/*", spellcheck: false });
  const addBtn = h("button.btn.primary", { onclick: () => add() }, icon("plus"), "Agregar");
  patternIn.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
  const paintKind = () => {
    kindSel.classList.toggle("kind-include", kindSel.value === "include");
    kindSel.classList.toggle("kind-exclude", kindSel.value === "exclude");
  };
  kindSel.addEventListener("change", paintKind);
  paintKind();

  const testIn = h("input.input.mono", { placeholder: "Probá una URL: https://api.target.com/v1/users", spellcheck: false });
  const testOut = h("span.result-chip", { hidden: true });
  const runTest = debounce(async () => {
    const url = testIn.value.trim();
    if (!url) {
      testOut.hidden = true;
      return;
    }
    testOut.hidden = false;
    try {
      const r = await app.api.post("/api/scope/test", { url });
      testOut.className = `result-chip ${r.in_scope ? "in" : "out"}`;
      const why = r.rule ? ` · ${r.rule.kind === "include" ? "por" : "excluida por"} ${r.rule.matcher}` : (r.in_scope ? " · sin reglas include" : "");
      testOut.replaceChildren(icon(r.in_scope ? "check-circle" : "x-circle", "sm"),
        `${r.in_scope ? "En scope" : "Fuera de scope"}${why}${r.decrypted ? "" : " · no se descifra"}`);
    } catch (e) {
      testOut.className = "result-chip out";
      testOut.replaceChildren(e.message);
    }
  }, 220);
  testIn.addEventListener("input", runTest);

  const el = h("section.view", h("div.doc", h("div.doc-inner",
    h("h2", "Scope"),
    h("p.lead",
      "Definí qué hosts te interesan. Sin reglas de inclusión todo está en scope. Lo que queda fuera se ve atenuado en History, ",
      "y las exclusiones de host además ", h("b", "no se descifran"),
      ": pasan por TLS sin tocar, ideal para apps con certificate pinning o ruido (telemetría, CDNs)."),
    list,
    h("div.rule-add", { style: { marginTop: "10px", borderRadius: "12px", border: "1px solid var(--border)" } }, kindSel, patternIn, addBtn),
    errorLine,
    h("div.section-title", h("h3", "Probar"), h("span.hint", "Se evalúa contra las reglas guardadas")),
    h("div.tester", testIn, testOut),
    h("div.section-title", h("h3", "Sintaxis")),
    h("div.cards",
      h("div.card", h("div.card-head", h("div.ci", icon("globe")), h("h3", "Host")),
        h("p", h("code", "target.com"), " — ese host exacto."),
        h("p", h("code", "*.target.com"), " — el dominio y todos sus subdominios."),
        h("p", h("code", "10.0.0.*"), " — comodines: ", h("code", "*"), " y ", h("code", "?"), ".")),
      h("div.card", h("div.card-head", h("div.ci", icon("link")), h("h3", "Puerto, esquema y path")),
        h("p", h("code", "api.target.com:8443"), " — solo ese puerto."),
        h("p", h("code", "https://target.com"), " — solo HTTPS."),
        h("p", h("code", "target.com/api/*"), " — solo ese path (filtra la vista; no evita el descifrado).")),
    ),
  )));

  function renderRules() {
    if (!rules.length) {
      list.replaceChildren(h("div", { style: { padding: "22px", textAlign: "center", color: "var(--text-3)" } },
        "Sin reglas: todo el tráfico está en scope."));
      return;
    }
    list.replaceChildren(...rules.map((r, i) => {
      const on = h("input", { type: "checkbox", checked: r.enabled });
      on.addEventListener("change", () => { r.enabled = on.checked; save(); });
      const kind = h("select.input", h("option", { value: "include" }, "Incluir"), h("option", { value: "exclude" }, "Excluir"));
      kind.value = r.kind;
      kind.classList.add(r.kind === "include" ? "kind-include" : "kind-exclude");
      kind.addEventListener("change", () => { r.kind = kind.value; save(); });
      const pat = h("input.input.mono", { value: r.matcher, spellcheck: false });
      pat.addEventListener("input", () => { r.matcher = pat.value; saveSoon(); });
      pat.addEventListener("blur", () => saveSoon.flush());
      const del = h("button.icon-btn", { title: "Borrar regla", onclick: () => { rules.splice(i, 1); save(); } }, icon("trash"));
      return h(`div.rule${r.enabled ? "" : ".off"}`, h("label.switch", on), kind, pat, del);
    }));
  }

  async function save() {
    const clean = rules.filter((r) => r.matcher.trim());
    try {
      const data = await app.api.put("/api/scope", { rules: clean.map(({ kind, matcher, enabled }) => ({ kind, matcher, enabled })) });
      rules = data.rules;
      errorLine.textContent = "";
      renderRules();
      runTest();
    } catch (e) {
      errorLine.textContent = e.message;
    }
  }
  const saveSoon = debounce(save, 600);

  async function add(kind = kindSel.value, matcher = patternIn.value.trim()) {
    if (!matcher) {
      patternIn.focus();
      return;
    }
    rules.push({ kind, matcher, enabled: true });
    await save();
    if (!errorLine.textContent) patternIn.value = "";
  }

  async function load() {
    try {
      rules = (await app.api.get("/api/scope")).rules;
      renderRules();
    } catch (e) {
      toast("err", "No se pudo leer el scope", e.message);
    }
  }
  app.on("scope.changed", ({ rules: r }) => {
    if (document.activeElement?.closest?.(".rules")) return; // no pisar lo que se está tipeando
    rules = r;
    renderRules();
  });
  app.on("resync", load);
  load();

  return {
    id: "scope",
    el,
    async addRule(kind, matcher) {
      if (rules.some((r) => r.kind === kind && r.matcher === matcher)) {
        toast("info", "Esa regla ya existe");
        return;
      }
      rules.push({ kind, matcher, enabled: true });
      await save();
      if (!errorLine.textContent) {
        toast("ok", kind === "include" ? "Agregado al scope" : "Excluido del scope", matcher);
      }
    },
  };
}
