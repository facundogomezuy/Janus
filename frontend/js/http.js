// Mensajes HTTP en el frontend: resaltado, formateo, hex y bytes.
//
// Regla de oro del resaltado: el texto resultante (sin etiquetas) tiene que ser
// IDÉNTICO al de entrada, carácter por carácter. El editor superpone el HTML
// resaltado debajo de un textarea transparente; si cambia un solo carácter, el
// resaltado se desalinea.

import { esc } from "./dom.js";

// --- bytes ----------------------------------------------------------------------
export function b64ToBytes(b64) {
  const bin = atob(b64 || "");
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
export function latin1(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return s;
}
export const encodeText = (s) => new TextEncoder().encode(s);
export function concatBytes(...parts) {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) { out.set(p, off); off += p.length; }
  return out;
}
/** Para mostrar bytes crudos como texto: controles -> "·" (salvo \t \r \n). */
export function printable(str) {
  return str.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "·");
}

// --- headers ------------------------------------------------------------------------
export function getHeader(headers, name) {
  const l = name.toLowerCase();
  const hit = (headers || []).find(([k]) => k.toLowerCase() === l);
  return hit ? hit[1] : null;
}
export function headLines(msg) {
  return [msg.first_line, ...(msg.headers || []).map(([k, v]) => `${k}: ${v}`)];
}
export function headerFromText(text, name) {
  const re = new RegExp(`^${name}[ \\t]*:[ \\t]*(.*)$`, "im");
  const head = text.split(/\r?\n\r?\n/, 1)[0];
  const m = head.match(re);
  return m ? m[1].trim() : null;
}

export function contentKind(ct) {
  const c = (ct || "").toLowerCase();
  if (!c) return "";
  if (c.includes("json")) return "json";
  if (c.includes("html")) return "html";
  if (c.includes("javascript") || c.includes("ecmascript")) return "js";
  if (c.includes("css")) return "css";
  if (c.includes("xml")) return c.includes("svg") ? "svg" : "xml";
  if (c.startsWith("image/")) return "img";
  if (c.startsWith("font/") || c.includes("woff")) return "font";
  if (c.includes("x-www-form-urlencoded")) return "form";
  if (c.includes("multipart")) return "multipart";
  if (c.startsWith("text/")) return "text";
  if (c.startsWith("video/") || c.startsWith("audio/")) return "media";
  return "bin";
}

// --- JSON: formateo sin JSON.parse para los valores --------------------------------------
export function looksLikeJson(text) {
  const t = text.trimStart();
  return t.startsWith("{") || t.startsWith("[");
}

/** Reindenta JSON conservando cada token tal cual (no pierde precisión en números grandes). */
export function prettyJson(text) {
  const s = text.trim();
  if (!s || !looksLikeJson(s)) return null;
  try { JSON.parse(s); } catch { return null; }
  const IND = "  ";
  let out = "";
  let depth = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === '"') {
      let j = i + 1;
      while (j < s.length && s[j] !== '"') j += s[j] === "\\" ? 2 : 1;
      out += s.slice(i, j + 1);
      i = j;
      continue;
    }
    if (c === "{" || c === "[") {
      let j = i + 1;
      while (j < s.length && /\s/.test(s[j])) j++;
      if (s[j] === (c === "{" ? "}" : "]")) {
        out += c + s[j];
        i = j;
        continue;
      }
      depth++;
      out += c + "\n" + IND.repeat(depth);
    } else if (c === "}" || c === "]") {
      depth--;
      out += "\n" + IND.repeat(depth) + c;
    } else if (c === ",") {
      out += ",\n" + IND.repeat(depth);
    } else if (c === ":") {
      out += ": ";
    } else if (!/\s/.test(c)) {
      out += c;
    }
  }
  return out;
}

const JSON_TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b|([{}[\],])/g;
export function highlightJson(text) {
  let out = "";
  let last = 0;
  for (const m of text.matchAll(JSON_TOKEN)) {
    out += esc(text.slice(last, m.index));
    if (m[1]) {
      out += m[2]
        ? `<span class="t-key">${esc(m[1])}</span><span class="t-punct">${esc(m[2])}</span>`
        : `<span class="t-str">${esc(m[1])}</span>`;
    } else if (m[3]) out += `<span class="t-num">${m[3]}</span>`;
    else if (m[4]) out += `<span class="t-bool">${m[4]}</span>`;
    else out += `<span class="t-punct">${m[5]}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

const MARKUP = /(<!--[\s\S]*?-->)|(<\/?|<[!?])([\w:.-]*)([^<>]*?)(\/?>)/g;
const ATTR = /([\w:.@-]+)(\s*=\s*)("[^"]*"|'[^']*'|[^\s>]+)?/g;
export function highlightMarkup(text) {
  let out = "";
  let last = 0;
  for (const m of text.matchAll(MARKUP)) {
    out += esc(text.slice(last, m.index));
    if (m[1]) {
      out += `<span class="t-dim">${esc(m[1])}</span>`;
    } else {
      let attrs = "";
      let al = 0;
      for (const a of m[4].matchAll(ATTR)) {
        attrs += esc(m[4].slice(al, a.index));
        attrs += `<span class="t-attr">${esc(a[1])}</span><span class="t-punct">${esc(a[2])}</span>`;
        if (a[3]) attrs += `<span class="t-str">${esc(a[3])}</span>`;
        al = a.index + a[0].length;
      }
      attrs += esc(m[4].slice(al));
      out += `<span class="t-punct">${esc(m[2])}</span><span class="t-tag">${esc(m[3])}</span>${attrs}<span class="t-punct">${esc(m[5])}</span>`;
    }
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

export function highlightForm(text) {
  return text.split(/(&)/).map((part) => {
    if (part === "&") return '<span class="t-punct">&amp;</span>';
    const i = part.indexOf("=");
    if (i < 0) return `<span class="t-key">${esc(part)}</span>`;
    return `<span class="t-key">${esc(part.slice(0, i))}</span><span class="t-punct">=</span><span class="t-str">${esc(part.slice(i + 1))}</span>`;
  }).join("");
}

export function highlightBody(body, contentType, max = 400000) {
  if (!body) return "";
  if (body.length > max) return esc(body);
  const kind = contentKind(contentType);
  const trimmed = body.trimStart();
  if (kind === "json" || (!kind && looksLikeJson(body))) return highlightJson(body);
  if (["html", "xml", "svg"].includes(kind) || (!kind && trimmed.startsWith("<"))) return highlightMarkup(body);
  if (kind === "form") return highlightForm(body);
  return esc(body);
}

// --- cabecera HTTP ----------------------------------------------------------------------------
function highlightFirstLine(line) {
  const status = line.match(/^(HTTP\/\S+)(\s+)(\d{3})(.*)$/i);
  if (status) {
    const cls = `t-status-${status[3][0]}`;
    return `<span class="t-ver">${esc(status[1])}</span>${status[2]}<span class="${cls}">${status[3]}</span><span class="${cls}">${esc(status[4])}</span>`;
  }
  const req = line.match(/^(\S+)(\s+)(\S+)(\s*)(.*)$/);
  if (req) {
    return `<span class="t-method">${esc(req[1])}</span>${req[2]}<span class="t-url">${esc(req[3])}</span>${req[4]}<span class="t-ver">${esc(req[5])}</span>`;
  }
  return esc(line);
}

function highlightHeaderLine(line) {
  const i = line.indexOf(":");
  if (i <= 0) return esc(line);
  return `<span class="t-hname">${esc(line.slice(0, i))}</span><span class="t-colon">:</span><span class="t-hval">${esc(line.slice(i + 1))}</span>`;
}

/** Resalta un mensaje HTTP completo (cabecera + cuerpo), preservando el texto exacto. */
export function highlightHttp(text, { maxBody } = {}) {
  const m = /\r?\n\r?\n/.exec(text);
  const head = m ? text.slice(0, m.index) : text;
  const sep = m ? m[0] : "";
  const body = m ? text.slice(m.index + sep.length) : "";
  const lines = head.split(/(\r?\n)/);
  let out = "";
  let lineNo = 0;
  for (const part of lines) {
    if (part === "\n" || part === "\r\n") { out += part; continue; }
    out += lineNo === 0 ? highlightFirstLine(part) : highlightHeaderLine(part);
    lineNo++;
  }
  const ct = headerFromText(head, "content-type");
  return out + sep + highlightBody(body, ct, maxBody);
}

// --- hex ---------------------------------------------------------------------------------------
export function hexDump(bytes, limit = 64 * 1024) {
  const n = Math.min(bytes.length, limit);
  const lines = [];
  for (let off = 0; off < n; off += 16) {
    let hex = "";
    let asc = "";
    for (let i = 0; i < 16; i++) {
      const idx = off + i;
      if (idx < n) {
        const b = bytes[idx];
        hex += b.toString(16).padStart(2, "0") + " ";
        asc += b >= 32 && b < 127 ? String.fromCharCode(b) : ".";
      } else {
        hex += "   ";
      }
      if (i === 7) hex += " ";
    }
    lines.push(`<span class="off">${off.toString(16).padStart(8, "0")}</span>  ${hex} <span class="asc">${esc(asc)}</span>`);
  }
  return lines.join("\n");
}
