"""Core: une el motor mitmproxy con los servicios de Janus.

Todo corre en un único event loop de asyncio (ARCHITECTURE.md §1): los hooks
del addon, la API y el WebSocket. Las escrituras a SQLite van a un hilo aparte
(db.Database) para no frenar el proxy.
"""
from __future__ import annotations

import asyncio
import base64
import errno
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from mitmproxy import addons, options
from mitmproxy.flow import Error as FlowError
from mitmproxy.master import Master

from . import ca, history, httpmsg, loopback, paths, rawhttp, repeater, sysproxy
from . import scope as scope_mod
from . import settings as settings_mod
from .db import Database
from .events import EventLog, MitmLogHandler
from .intercept import META_EDITED, META_IN_SCOPE, META_SEQ, Interceptor, InterceptError
from .proxy.addon import JanusAddon
from .scope import Scope
from .ws import Hub

if TYPE_CHECKING:
    from mitmproxy import tls
    from mitmproxy.http import HTTPFlow

log = logging.getLogger("janus.core")

# WSAEADDRINUSE / EADDRINUSE (Linux) / EADDRINUSE (macOS)
_ADDR_IN_USE = {10048, errno.EADDRINUSE, 48, 98}
# WSAEACCES: típico en Windows cuando Hyper-V/WSL reservaron el rango de puertos
_ADDR_FORBIDDEN = {10013, errno.EACCES}


class Core:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.hub = Hub()
        self.events = EventLog(self.hub)
        self.settings = db.read_sync(settings_mod.load)
        self.scope = Scope(db.read_sync(scope_mod.load_rules))
        self.interceptor = Interceptor(self.hub, on_resolved=self._on_intercept_resolved)
        try:
            self.interceptor.configure(
                requests=self.settings["intercept.requests"],
                responses=self.settings["intercept.responses"],
                only_in_scope=self.settings["intercept.only_in_scope"],
                filter=self.settings["intercept.filter"],
            )
        except InterceptError:
            self.interceptor.configure(filter="")
        self.confdir = paths.confdir()
        ca.ensure_ca(self.confdir)
        self._seq = db.read_sync(history.max_seq)
        self.flow_count = db.read_sync(history.count)
        self._live: dict[str, dict[str, Any]] = {}  # resúmenes de flows en curso
        self.master: Master | None = None
        self.started_at = time.time()
        self.proxy_ready = asyncio.Event()

    # --- arranque del motor ---------------------------------------------------

    def _mode_spec(self) -> str:
        s = self.settings
        base = f"upstream:{s['proxy.upstream']}" if s["proxy.upstream"] else "regular"
        return f"{base}@{s['proxy.listen_host']}:{s['proxy.listen_port']}"

    def build_master(self) -> Master:
        s = self.settings
        opts = options.Options(
            confdir=str(self.confdir),
            mode=[self._mode_spec()],
            ssl_insecure=s["proxy.ssl_insecure"],
            http2=s["proxy.http2"],
            ignore_hosts=self.scope.ignore_hosts(),
        )
        master = Master(opts)
        master.addons.add(*addons.default_addons())
        master.addons.add(JanusAddon(self))
        master.options.update(stream_large_bodies=s["proxy.stream_large_bodies"] or None)
        proxyserver = master.addons.get("proxyserver")
        proxyserver.servers.changed.connect(self._on_servers_changed)
        logging.getLogger("mitmproxy").addHandler(MitmLogHandler(self.events))
        self.master = master
        return master

    def apply_proxy_options(self) -> None:
        if self.master is None:
            return
        s = self.settings
        self.master.options.update(
            mode=[self._mode_spec()],
            ssl_insecure=s["proxy.ssl_insecure"],
            http2=s["proxy.http2"],
            stream_large_bodies=s["proxy.stream_large_bodies"] or None,
            ignore_hosts=self.scope.ignore_hosts(),
        )

    def on_proxy_running(self) -> None:
        self.proxy_ready.set()
        self.hub.emit("proxy.status", self.proxy_status())

    def _on_servers_changed(self) -> None:
        status = self.proxy_status()
        if status["error"]:
            self.events.add("error", status["error"], source="proxy", key="listen", throttle=1.0,
                            hint=status.get("hint"))
        self.hub.emit("proxy.status", status)

    def proxy_status(self) -> dict[str, Any]:
        s = self.settings
        out: dict[str, Any] = {
            "running": False,
            "error": None,
            "hint": None,
            "listen_host": s["proxy.listen_host"],
            "listen_port": s["proxy.listen_port"],
            "upstream": s["proxy.upstream"] or None,
            "updating": False,
        }
        if self.master is None:
            return out
        ps = self.master.addons.get("proxyserver")
        out["updating"] = ps.servers.is_updating
        instances = list(ps.servers)
        if instances:
            inst = instances[0]
            out["running"] = bool(inst.is_running)
            exc = inst.last_exception
            if exc is not None:
                out["error"], out["hint"] = self._describe_listen_error(exc)
        return out

    def _describe_listen_error(self, exc: Exception) -> tuple[str, str | None]:
        port = self.settings["proxy.listen_port"]
        code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
        if code in _ADDR_IN_USE:
            return (f"El puerto {port} ya está en uso por otra aplicación.",
                    "Cerrá la otra app (¿Burp, Fiddler, otro mitmproxy?) o elegí otro puerto en Ajustes.")
        if code in _ADDR_FORBIDDEN:
            return (f"Windows no permite escuchar en el puerto {port}.",
                    "Suele pasar cuando Hyper-V/WSL reservan ese rango "
                    "(netsh int ipv4 show excludedportrange protocol=tcp). Elegí otro puerto en Ajustes.")
        return f"El proxy no pudo escuchar en el puerto {port}: {exc}", None

    # --- hooks del proxy --------------------------------------------------------

    async def on_server_connect(self, data) -> None:
        addr = data.server.address
        if addr and loopback.is_localhost(addr[0]):
            data.server.address = (await loopback.pick(addr[1]), addr[1])

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def on_request(self, flow: HTTPFlow) -> None:
        seq = self._next_seq()
        rec = history.request_record(flow)
        in_scope = self.scope.in_scope(rec["scheme"], rec["host"], rec["port"], rec["path"])
        flow.metadata[META_SEQ] = seq
        flow.metadata[META_IN_SCOPE] = in_scope
        values = {
            "id": flow.id,
            "seq": seq,
            "ts": flow.request.timestamp_start or time.time(),
            "source": "proxy",
            **rec,
            "in_scope": int(in_scope),
            "state": "pending",
        }
        self.db.write_nowait(history.insert, values)
        self.flow_count += 1
        summary = history.summary_from_values(flow.id, values)
        self._live[flow.id] = summary
        self.hub.emit("flow.new", summary)
        if self.interceptor.matches(flow, "request"):
            self.interceptor.hold(flow, "request")

    def on_response(self, flow: HTTPFlow) -> None:
        rec = history.response_record(flow)
        if flow.metadata.get(META_EDITED):
            rec["edited"] = 1
        self.db.write_nowait(history.update, flow.id, rec)
        summary = self._live.get(flow.id)
        if summary is not None:
            summary.update(
                status_code=rec["status_code"], reason=rec["reason"], length=rec["res_length"],
                content_type=rec["content_type"], duration_ms=rec["duration_ms"], state="complete",
                edited=bool(rec.get("edited", summary.get("edited"))),
            )
            self.hub.emit("flow.update", dict(summary))
        if self.interceptor.matches(flow, "response"):
            self.interceptor.hold(flow, "response")
        else:
            self._live.pop(flow.id, None)

    def on_error(self, flow: HTTPFlow) -> None:
        msg = flow.error.msg if flow.error else "error"
        if msg == FlowError.KILLED_MESSAGE:
            msg = "descartado (drop)"
        values: dict[str, Any] = {"error": msg}
        if flow.response is None:
            values["state"] = "error"
        self.db.write_nowait(history.update, flow.id, values)
        summary = self._live.pop(flow.id, None)
        if summary is not None:
            summary.update(error=msg, state=values.get("state", summary["state"]))
            self.hub.emit("flow.update", summary)

    def _on_intercept_resolved(self, flow: HTTPFlow, phase: str, edited: bool) -> None:
        summary = self._live.get(flow.id)
        if edited:
            if phase == "request":
                rec = history.request_record(flow)
                in_scope = self.scope.in_scope(rec["scheme"], rec["host"], rec["port"], rec["path"])
                rec.update(edited=1, in_scope=int(in_scope))
                if summary is not None:
                    summary.update(method=rec["method"], scheme=rec["scheme"], host=rec["host"],
                                   port=rec["port"], path=rec["path"], in_scope=in_scope, edited=True)
            else:
                rec = history.response_record(flow)
                rec["edited"] = 1
                if summary is not None:
                    summary.update(status_code=rec["status_code"], reason=rec["reason"],
                                   length=rec["res_length"], content_type=rec["content_type"], edited=True)
            self.db.write_nowait(history.update, flow.id, rec)
            if summary is not None:
                self.hub.emit("flow.update", dict(summary))
        if phase == "response":
            self._live.pop(flow.id, None)

    def on_tls_failed(self, data: tls.TlsData, side: str) -> None:
        conn = data.conn
        server_addr = data.context.server.address
        name = conn.sni or (server_addr[0] if server_addr else "?")
        if side == "client":
            self.events.add(
                "warn",
                f"El cliente rechazó el certificado de Janus para {name}",
                source="tls",
                key=f"tls-client:{name}",
                throttle=30.0,
                hint="Instalá la CA de Janus (pestaña Setup) o usá el navegador Janus. "
                     "Si es una app con certificate pinning, excluí el host del scope.",
            )
        else:
            err = conn.error or "error desconocido"
            self.events.add("warn", f"Falló el TLS con el servidor {name}: {err}", source="tls",
                            key=f"tls-server:{name}", throttle=30.0)

    # --- API de servicio (la usa server.py) -------------------------------------

    def status(self) -> dict[str, Any]:
        s = self.settings
        return {
            "app": "janus",
            "proxy": self.proxy_status(),
            "intercept": self.interceptor.state(),
            "flows": self.flow_count,
            "scope": {"rules": len(self.scope.rules), "has_includes": self.scope.has_includes},
            "sysproxy": sysproxy.status(s["proxy.listen_host"], s["proxy.listen_port"]),
            "events": len(self.events.recent()),
            "data_dir": str(paths.data_dir()),
            "started_at": self.started_at,
        }

    async def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        valid = self.settings.validate_update(changes)
        before = self.settings.as_dict()
        await self.db.write(settings_mod.save, valid)
        self.settings.apply(valid)
        if any(k.startswith("proxy.") for k in valid):
            self.apply_proxy_options()
            sp_before = sysproxy.status(before["proxy.listen_host"], before["proxy.listen_port"])
            if sp_before.get("enabled"):  # seguir apuntando al puerto nuevo
                s = self.settings
                await asyncio.to_thread(sysproxy.enable, s["proxy.listen_host"], s["proxy.listen_port"])
                self.hub.emit("sysproxy.changed", self.sysproxy_status())
        self.hub.emit("settings.changed", self.settings.as_dict())
        return self.settings.as_dict()

    async def configure_intercept(self, changes: dict[str, Any]) -> dict[str, Any]:
        self.interceptor.configure(**changes)
        persist = {f"intercept.{k}": v for k, v in changes.items()
                   if k in ("requests", "responses", "only_in_scope", "filter")}
        if persist:
            await self.db.write(settings_mod.save, persist)
            self.settings.apply(persist)
        return self.interceptor.state()

    async def set_scope(self, rules: list[dict]) -> list[dict]:
        candidate = [scope_mod.Rule(0, r["kind"], r["matcher"], r.get("enabled", True)) for r in rules]
        Scope(candidate)  # valida antes de guardar (PatternError -> 400)
        saved = await self.db.write(scope_mod.save_rules, rules)
        self.scope.set_rules(saved)
        self.apply_proxy_options()
        changed = await self.db.write(history.recompute_scope, self.scope)
        for fid, now in changed:
            if fid in self._live:
                self._live[fid]["in_scope"] = now
        self.hub.emit("scope.changed", {"rules": [r.to_json() for r in saved],
                                        "changed": [[i, v] for i, v in changed]})
        return [r.to_json() for r in saved]

    async def clear_history(self) -> None:
        await self.db.write(history.delete_all)
        self.flow_count = 0
        self.hub.emit("history.cleared")

    async def delete_flows(self, ids: list[str]) -> None:
        await self.db.write(history.delete_ids, ids)
        self.flow_count = max(0, self.flow_count - len(ids))
        self.hub.emit("flows.deleted", {"ids": ids})

    # --- repeater ---------------------------------------------------------------

    async def repeater_send(self, tab_id: int, *, host: str, port: int, tls: bool, message: dict,
                            fix_content_length: bool = True, timeout: float = 30.0) -> dict[str, Any]:
        row = await self.db.read(repeater.get_row, tab_id)
        if row is None:
            raise KeyError(tab_id)
        encoding = message.get("encoding", "utf-8")
        data = httpmsg.text_to_bytes(message.get("text", ""), encoding)
        wire = httpmsg.prepare_request_for_wire(
            data, fix_content_length=fix_content_length, original_body=row["orig_body"]
        )
        result = await rawhttp.send(host, port, tls, wire, timeout=timeout)
        out = repeater.result_json(result)
        out["request_b64"] = base64.b64encode(wire).decode("ascii")
        out["flow_id"] = self._record_repeater_flow(host, port, tls, wire, result)
        await self.db.write(repeater.update_tab, tab_id, {
            "host": host, "port": port, "tls": int(tls), "raw_request": data, "encoding": encoding,
            "response_meta": json.dumps(out),
        })
        return out

    def _record_repeater_flow(self, host: str, port: int, tls: bool, wire: bytes,
                              result: rawhttp.RawResult) -> str | None:
        try:
            req = httpmsg.parse_request(wire)
        except httpmsg.MessageError:
            return None
        fid = str(uuid.uuid4())
        scheme = "https" if tls else "http"
        path = req.target
        if path.lower().startswith(("http://", "https://")):  # absolute-form
            parts = urlsplit(path)
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        values: dict[str, Any] = {
            "id": fid,
            "seq": self._next_seq(),
            "ts": time.time() - result.elapsed_ms / 1000,
            "source": "repeater",
            "method": req.method,
            "scheme": scheme,
            "host": host,
            "port": port,
            "path": path,
            "http_version": req.http_version,
            "req_headers": json.dumps(req.headers),
            "req_body": req.body,
            "in_scope": int(self.scope.in_scope(scheme, host, port, path)),
            "duration_ms": result.elapsed_ms,
            "state": "complete" if result.response else "error",
            "error": result.error,
        }
        resp = result.response
        if resp is not None:
            values.update(
                status_code=resp.status_code,
                reason=resp.reason,
                res_http_version=resp.http_version,
                res_headers=json.dumps(resp.headers),
                res_body=resp.body,
                res_length=len(resp.body),
                content_type=httpmsg.header_get(resp.headers, "content-type"),
                server_addr=result.peer,
            )
        self.db.write_nowait(history.insert, values)
        self.flow_count += 1
        self.hub.emit("flow.new", history.summary_from_values(fid, values))
        return fid

    # --- CA / proxy del sistema -------------------------------------------------

    def ca_info(self) -> dict[str, Any]:
        return ca.info(self.confdir)

    def sysproxy_status(self) -> dict[str, Any]:
        s = self.settings
        return sysproxy.status(s["proxy.listen_host"], s["proxy.listen_port"])

    async def set_sysproxy(self, enabled: bool) -> dict[str, Any]:
        s = self.settings
        if enabled:
            await asyncio.to_thread(sysproxy.enable, s["proxy.listen_host"], s["proxy.listen_port"])
        else:
            await asyncio.to_thread(sysproxy.disable)
        status = self.sysproxy_status()
        self.hub.emit("sysproxy.changed", status)
        return status

    # --- apagado ----------------------------------------------------------------

    def shutdown_services(self) -> None:
        """Lo que hay que deshacer sí o sí al salir (sincrónico, idempotente)."""
        try:
            self.interceptor.forward_all()
        except Exception:  # noqa: BLE001
            log.exception("forward_all al salir")
        try:
            s = self.settings
            if sysproxy.status(s["proxy.listen_host"], s["proxy.listen_port"]).get("enabled"):
                sysproxy.disable()
        except Exception:  # noqa: BLE001
            log.exception("restaurar proxy del sistema al salir")
        if self.master is not None:
            self.master.shutdown()
