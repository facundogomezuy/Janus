// Cliente de la API local: REST con token + WebSocket con reconexión.

import { sleep } from "./dom.js";

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
  }
}

/** Dónde está el backend y con qué token hablarle. */
export async function resolveBackend(onWait) {
  const T = window.__TAURI__;
  if (T?.core?.invoke) {
    const deadline = Date.now() + 60000;
    let tries = 0;
    while (Date.now() < deadline) {
      const info = await T.core.invoke("backend_info");
      if (info.state === "ready") {
        return { base: `http://127.0.0.1:${info.port}`, token: info.token, tauri: true };
      }
      if (info.state === "error") {
        const err = new Error(info.error || "el motor no arrancó");
        err.logs = info.logs;
        throw err;
      }
      onWait?.(++tries);
      await sleep(120);
    }
    throw new Error("El motor tardó demasiado en arrancar.");
  }
  if (location.protocol === "file:") {
    throw new Error("Abrí Janus desde la app de escritorio o con «python -m janus».");
  }
  // standalone: el backend sirve esta página; el token llega en el #hash
  let token = null;
  const m = location.hash.match(/token=([A-Za-z0-9_-]+)/);
  if (m) {
    token = m[1];
    try { sessionStorage.setItem("janus.token", token); } catch { /* */ }
    history.replaceState(null, "", location.pathname + location.search);
  } else {
    try { token = sessionStorage.getItem("janus.token"); } catch { /* */ }
  }
  return { base: "", token, tauri: false };
}

export class Api {
  constructor({ base, token }) {
    this.base = base;
    this.token = token;
  }

  url(path, params = {}) {
    const q = new URLSearchParams(params);
    if (this.token) q.set("token", this.token);
    const qs = q.toString();
    return `${this.base}${path}${qs ? "?" + qs : ""}`;
  }

  async req(method, path, body, { signal } = {}) {
    const headers = {};
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let r;
    try {
      r = await fetch(this.base + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new ApiError(0, "sin conexión con el motor de Janus");
    }
    const ctype = r.headers.get("content-type") || "";
    if (!r.ok) {
      let detail = r.statusText;
      try {
        const j = await r.json();
        detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch { /* */ }
      throw new ApiError(r.status, detail);
    }
    if (ctype.includes("json")) return r.json();
    if (ctype.startsWith("text/")) return r.text();
    return r.blob();
  }

  get(path, opts) { return this.req("GET", path, undefined, opts); }
  post(path, body = {}, opts) { return this.req("POST", path, body, opts); }
  put(path, body, opts) { return this.req("PUT", path, body, opts); }
  patch(path, body, opts) { return this.req("PATCH", path, body, opts); }
  del(path, opts) { return this.req("DELETE", path, undefined, opts); }

  /** Stream de eventos. onEvent(type, data); onState("open"|"closed"). */
  stream(onEvent, onState) {
    let ws = null;
    let stopped = false;
    let backoff = 400;
    let pingTimer = null;
    const wsBase = this.base ? this.base.replace(/^http/, "ws") : location.origin.replace(/^http/, "ws");
    const open = () => {
      if (stopped) return;
      const q = this.token ? `?token=${encodeURIComponent(this.token)}` : "";
      ws = new WebSocket(`${wsBase}/ws${q}`);
      ws.onopen = () => {
        backoff = 400;
        onState?.("open");
        pingTimer = setInterval(() => { try { ws.send("ping"); } catch { /* */ } }, 20000);
      };
      ws.onmessage = (msg) => {
        let batch;
        try { batch = JSON.parse(msg.data); } catch { return; }
        for (const ev of batch) if (ev.type !== "pong") onEvent(ev.type, ev.data);
      };
      ws.onclose = () => {
        clearInterval(pingTimer);
        if (stopped) return;
        onState?.("closed");
        setTimeout(open, backoff);
        backoff = Math.min(backoff * 1.7, 5000);
      };
      ws.onerror = () => { try { ws.close(); } catch { /* */ } };
    };
    open();
    return () => {
      stopped = true;
      clearInterval(pingTimer);
      ws?.close();
    };
  }
}
