// Editor de mensajes HTTP: textarea transparente sobre una capa resaltada.

import { h } from "./dom.js";
import { highlightHttp } from "./http.js";

const HIGHLIGHT_LIMIT = 300000; // arriba de esto, texto plano (rendimiento)

export class HttpEditor {
  constructor({ placeholder = "", onChange, onSubmit, readOnly = false } = {}) {
    this.onChange = onChange;
    this.onSubmit = onSubmit;
    this.ta = h("textarea", {
      spellcheck: false,
      autocomplete: "off",
      autocapitalize: "off",
      wrap: "soft",
      placeholder,
      "aria-label": "Mensaje HTTP",
    });
    this.pre = h("pre", { "aria-hidden": "true" });
    this.el = h("div.editor", this.pre, this.ta);
    this._baseline = "";
    this._raf = 0;
    this.ta.readOnly = readOnly;
    this.ta.addEventListener("input", () => {
      this._schedule();
      this.onChange?.(this.value);
    });
    this.ta.addEventListener("scroll", () => this._syncScroll());
    this.ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        this.onSubmit?.();
      } else if (e.key === "Tab" && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        document.execCommand("insertText", false, "\t"); // conserva el undo
      }
    });
  }

  get value() { return this.ta.value; }

  /** Carga texto nuevo (y lo toma como base para `dirty`). */
  set value(text) {
    this.ta.value = text ?? "";
    this._baseline = this.ta.value;
    this.ta.scrollTop = 0;
    this._render();
  }

  /** Reemplaza el texto sin perder el historial de deshacer. */
  replace(text) {
    this.ta.focus();
    this.ta.select();
    document.execCommand("insertText", false, text);
    this._render();
  }

  get dirty() { return this.ta.value !== this._baseline; }
  markClean() { this._baseline = this.ta.value; }
  revert() { this.replace(this._baseline); }

  set readOnly(v) { this.ta.readOnly = !!v; }
  set disabled(v) { this.ta.disabled = !!v; }
  focus() { this.ta.focus(); }

  _schedule() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      this._render();
    });
  }

  _render() {
    const text = this.ta.value;
    const plain = text.length > HIGHLIGHT_LIMIT;
    this.el.classList.toggle("plain", plain);
    if (!plain) {
      // el "\n " final mantiene la misma altura cuando el texto termina en salto de línea
      this.pre.innerHTML = highlightHttp(text) + "\n ";
      this._syncScroll();
    }
  }

  _syncScroll() {
    this.pre.scrollTop = this.ta.scrollTop;
  }
}
