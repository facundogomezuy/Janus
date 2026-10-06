// Visor de request/response: Pretty · Raw · Hex.

import { copyText, emptyState, esc, fmtBytes, h, icon, store, persist, toast } from "./dom.js";
import {
  b64ToBytes, concatBytes, contentKind, encodeText, headLines, hexDump, highlightBody,
  highlightHttp, latin1, prettyJson, printable,
} from "./http.js";

const MODES = [["pretty", "Pretty"], ["raw", "Raw"], ["hex", "Hex"]];
const RAW_LIMIT = 1024 * 1024;
const UTF8 = new TextDecoder("utf-8", { fatal: true });

export class MessageViewer {
  /**
   * @param {object} o
   * @param {string} o.title  "Request" | "Response"
   * @param {string} o.key    para recordar el modo elegido
   * @param {Node[]} [o.actions] botones extra en la cabecera
   */
  constructor({ title, key, actions = [] }) {
    this.key = key;
    this.mode = store(`viewer.${key}.mode`, "pretty");
    this.wrap = store("viewer.wrap", true);
    this.msg = null;
    this.hexAll = false;
    this.tabs = h("div.tabs-mini", MODES.map(([id, label]) =>
      h("button", { dataset: { mode: id }, onclick: () => this.setMode(id) }, label)));
    this.sub = h("span.pane-sub");
    this.wrapBtn = h("button.icon-btn", { title: "Ajuste de línea", onclick: () => this.toggleWrap() }, icon("wrap", "sm"));
    this.copyBtn = h("button.icon-btn", { title: "Copiar", onclick: () => this.copy() }, icon("copy", "sm"));
    this.head = h("div.pane-head", h("span.pane-title", title), this.tabs, this.sub, h("span.spacer"),
      ...actions, this.wrapBtn, this.copyBtn);
    this.code = h("pre.code.selectable");
    this.body = h("div", { style: { flex: "1", minHeight: "0", display: "flex", flexDirection: "column" } }, this.code);
    this.el = h("div.pane", this.head, this.body);
    this._syncTabs();
    this.setEmpty("—");
  }

  setMode(mode) {
    this.mode = mode;
    persist(`viewer.${this.key}.mode`, mode);
    this._syncTabs();
    this.render();
  }

  toggleWrap() {
    this.wrap = !this.wrap;
    persist("viewer.wrap", this.wrap);
    this.render();
  }

  _syncTabs() {
    for (const b of this.tabs.children) b.classList.toggle("on", b.dataset.mode === this.mode);
    this.wrapBtn.classList.toggle("active", this.wrap);
  }

  setEmpty(text, { icon: ic, title } = {}) {
    this.msg = null;
    this.sub.textContent = "";
    this.body.replaceChildren(ic
      ? emptyState({ icon: ic, title, text })
      : h("div.empty", h("p", text)));
  }

  setError(text) {
    this.msg = null;
    this.sub.textContent = "";
    this.body.replaceChildren(h("div.empty", h("div.art", icon("alert")), h("h3", "Sin respuesta"), h("p", text)));
  }

  /** msg: {first_line, headers:[[k,v]], body:{b64,text,size,content_type,content_encoding,decode_error,truncated}} */
  set(msg, { rawBytes } = {}) {
    this.msg = msg;
    this.rawBytes = rawBytes || null; // bytes exactos del wire (repeater)
    this.hexAll = false;
    const b = msg.body || {};
    const kind = contentKind(b.content_type);
    const parts = [fmtBytes(b.size ?? 0)];
    if (kind) parts.unshift(kind.toUpperCase());
    if (b.content_encoding) parts.push(b.content_encoding);
    this.sub.textContent = parts.join(" · ");
    this.body.replaceChildren(this.code);
    this.render();
  }

  _bodyBytes() {
    if (!this._bytesCache || this._bytesCache.msg !== this.msg) {
      this._bytesCache = { msg: this.msg, bytes: b64ToBytes(this.msg.body?.b64 || "") };
    }
    return this._bytesCache.bytes;
  }

  _wireBytes() {
    if (this.rawBytes) return this.rawBytes;
    const head = encodeText(headLines(this.msg).join("\r\n") + "\r\n\r\n");
    return concatBytes(head, this._bodyBytes());
  }

  /** Texto "pretty" (también es lo que copia el botón en ese modo). */
  prettyText() {
    const m = this.msg;
    const b = m.body || {};
    let body = b.text ?? "";
    const kind = contentKind(b.content_type);
    if (body && (kind === "json" || (!kind && /^\s*[[{]/.test(body)))) body = prettyJson(body) ?? body;
    return { head: headLines(m).join("\n"), body };
  }

  render() {
    const m = this.msg;
    this._syncTabs();
    if (!m) return;
    this.code.classList.toggle("nowrap", !this.wrap || this.mode === "hex");
    this.code.classList.toggle("hex", this.mode === "hex");
    const b = m.body || {};
    if (this.mode === "pretty") {
      const { head, body } = this.prettyText();
      let html = highlightHttp(head);
      if (b.size) {
        html += "\n\n";
        if (b.text != null) html += highlightBody(body, b.content_type);
        if (b.text == null) {
          html += `<span class="note">${b.decode_error ? esc(b.decode_error) + ". " : ""}Cuerpo binario de ${fmtBytes(b.size)}${
            b.content_type ? ` (${esc(b.content_type)})` : ""}. Mirá la pestaña Hex.</span>`;
        } else if (b.truncated) {
          html += `<span class="note">Vista recortada: el cuerpo pesa ${fmtBytes(b.decoded_size ?? b.size)}.</span>`;
        }
      }
      this.code.innerHTML = html;
    } else if (this.mode === "raw") {
      const bytes = this._wireBytes();
      const shown = bytes.length > RAW_LIMIT ? bytes.subarray(0, RAW_LIMIT) : bytes;
      let text;
      try { text = UTF8.decode(shown); } catch { text = latin1(shown); }
      let html = highlightHttp(printable(text.replace(/\r\n/g, "\n")), { maxBody: 200000 });
      if (bytes.length > RAW_LIMIT) html += `<span class="note">Mostrando 1 MB de ${fmtBytes(bytes.length)}.</span>`;
      this.code.innerHTML = html;
    } else {
      const bytes = this._wireBytes();
      const limit = this.hexAll ? Infinity : 64 * 1024;
      this.code.innerHTML = hexDump(bytes, limit);
      if (bytes.length > limit) {
        const more = h("button.btn.sm", { style: { marginTop: "10px" }, onclick: () => { this.hexAll = true; this.render(); } },
          `Mostrar los ${fmtBytes(bytes.length)} completos`);
        this.code.append("\n", more);
      }
    }
  }

  async copy() {
    if (!this.msg) return;
    let text;
    if (this.mode === "pretty") {
      const { head, body } = this.prettyText();
      text = this.msg.body?.size ? `${head}\n\n${body}` : head;
    } else {
      text = latin1(this._wireBytes());
    }
    await copyText(text);
    toast("ok", "Copiado al portapapeles");
  }
}
