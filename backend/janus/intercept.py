"""Intercept: retener, editar, forward y drop (ARCHITECTURE.md §3).

mitmproxy retiene un flow con ``flow.intercept()`` y el hook queda esperando
hasta ``flow.resume()``. Para el drop se hace ``resume()`` + ``kill()`` en el
mismo tick del event loop: el hook se despierta, el layer HTTP ve el kill y
corta la conexión (``Flow.kill()`` solo no despierta al hook).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from mitmproxy import flowfilter
from mitmproxy.http import Headers as MitmHeaders

from . import httpmsg

if TYPE_CHECKING:
    from mitmproxy.http import HTTPFlow

    from .ws import Hub

log = logging.getLogger("janus.intercept")

META_SEQ = "janus.seq"
META_IN_SCOPE = "janus.in_scope"
META_INTERCEPT_RESPONSE = "janus.intercept_response"
META_EDITED = "janus.edited"


@dataclass
class Held:
    flow: HTTPFlow
    phase: str  # request | response
    held_at: float
    message: dict  # {"text", "encoding"} tal cual se mostró en el editor
    shown_body: bytes  # cuerpo que vio el editor (decodificado o crudo)
    body_decoded: bool


class InterceptError(ValueError):
    pass


class Interceptor:
    def __init__(self, hub: Hub, on_resolved: Callable[[HTTPFlow, str, bool], None]) -> None:
        self.hub = hub
        # (flow, fase, editado): el Core actualiza el historial
        self.on_resolved = on_resolved
        self.enabled = False
        self.requests = True
        self.responses = False
        self.only_in_scope = True
        self.filter_text = ""
        self._filter = None
        self.skip_hosts: set[str] = {"mitm.it"}
        self.queue: dict[str, Held] = {}

    # --- estado -------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "requests": self.requests,
            "responses": self.responses,
            "only_in_scope": self.only_in_scope,
            "filter": self.filter_text,
            "pending": len(self.queue),
        }

    def configure(self, *, enabled: bool | None = None, requests: bool | None = None,
                  responses: bool | None = None, only_in_scope: bool | None = None,
                  filter: str | None = None) -> None:  # noqa: A002 - nombre de la API
        if filter is not None:
            text = filter.strip()
            if text:
                try:
                    self._filter = flowfilter.parse(text)
                except ValueError as exc:
                    raise InterceptError(f"filtro inválido: {exc}") from exc
            else:
                self._filter = None
            self.filter_text = text
        if requests is not None:
            self.requests = requests
        if responses is not None:
            self.responses = responses
        if only_in_scope is not None:
            self.only_in_scope = only_in_scope
        if enabled is not None and enabled != self.enabled:
            self.enabled = enabled
            if not enabled:
                self.forward_all()  # como Burp: apagar libera todo
        self.hub.emit("intercept.state", self.state())

    # --- decisión -----------------------------------------------------------

    def matches(self, flow: HTTPFlow, phase: str) -> bool:
        if not self.enabled:
            return False
        if flow.request.pretty_host in self.skip_hosts:
            return False
        if phase == "request":
            if not self.requests or flow.response is not None:
                return False
        elif not (self.responses or flow.metadata.get(META_INTERCEPT_RESPONSE)):
            return False
        if self.only_in_scope and not flow.metadata.get(META_IN_SCOPE, True):
            return False
        if self._filter is not None:
            try:
                return bool(self._filter(flow))
            except Exception:  # noqa: BLE001 - un filtro raro no debe tumbar el proxy
                return False
        return True

    def hold(self, flow: HTTPFlow, phase: str) -> None:
        if phase == "request":
            req = flow.request
            headers = httpmsg.headers_from_mitm(req.headers.fields)
            if req.http_version.startswith("HTTP/2") and httpmsg.header_get(headers, "host") is None:
                headers = [("Host", req.authority or req.pretty_host)] + headers
            first = f"{req.method} {req.path} {req.http_version}"
            raw_body = req.raw_content or b""
        else:
            res = flow.response
            assert res is not None
            headers = httpmsg.headers_from_mitm(res.headers.fields)
            first = f"{res.http_version} {res.status_code} {res.reason}".rstrip()
            raw_body = res.raw_content or b""
        message, decoded = httpmsg.editable_message(first, headers, raw_body)
        shown = httpmsg.decode_body(raw_body, headers)[0] if decoded else raw_body
        flow.intercept()
        self.queue[flow.id] = Held(flow, phase, time.time(), message, shown or b"", decoded)
        self.hub.emit("intercept.pending", self.item_json(self.queue[flow.id]))
        self.hub.emit("intercept.state", self.state())

    def item_json(self, held: Held) -> dict[str, Any]:
        flow = held.flow
        req = flow.request
        res = flow.response
        return {
            "id": flow.id,
            "seq": flow.metadata.get(META_SEQ),
            "phase": held.phase,
            "held_at": held.held_at,
            "method": req.method,
            "scheme": req.scheme,
            "host": req.pretty_host,
            "port": req.port,
            "path": req.path,
            "url": req.pretty_url,
            "status_code": res.status_code if (res is not None and held.phase == "response") else None,
            "in_scope": bool(flow.metadata.get(META_IN_SCOPE, True)),
            "intercept_response": bool(flow.metadata.get(META_INTERCEPT_RESPONSE)),
            "body_decoded": held.body_decoded,
            "message": held.message,
            "request_line": f"{req.method} {req.pretty_url}",
        }

    def items(self) -> list[dict[str, Any]]:
        return [self.item_json(h) for h in self.queue.values()]

    # --- resolución ---------------------------------------------------------

    def _take(self, flow_id: str) -> Held:
        held = self.queue.pop(flow_id, None)
        if held is None:
            raise KeyError(flow_id)
        return held

    def forward(self, flow_id: str, message: dict | None = None,
                intercept_response: bool | None = None) -> None:
        held = self.queue.get(flow_id)
        if held is None:
            raise KeyError(flow_id)
        flow = held.flow
        if message is not None:
            data = httpmsg.text_to_bytes(message.get("text", ""), message.get("encoding", "utf-8"))
            try:
                if held.phase == "request":
                    self._apply_request(held, data)
                else:
                    self._apply_response(held, data)
            except ValueError as exc:  # MessageError o un valor que mitmproxy rechaza
                raise InterceptError(str(exc)) from exc
            flow.metadata[META_EDITED] = True
        if intercept_response is not None:
            flow.metadata[META_INTERCEPT_RESPONSE] = bool(intercept_response)
        self._take(flow_id)
        flow.resume()
        self.on_resolved(flow, held.phase, message is not None)
        self._resolved(flow_id, "forward")

    def drop(self, flow_id: str) -> None:
        held = self._take(flow_id)
        flow = held.flow
        flow.resume()
        if flow.killable:
            flow.kill()
        self.on_resolved(flow, held.phase, False)
        self._resolved(flow_id, "drop")

    def forward_all(self) -> None:
        for fid in list(self.queue):
            try:
                self.forward(fid)
            except KeyError:
                pass

    def drop_all(self) -> None:
        for fid in list(self.queue):
            try:
                self.drop(fid)
            except KeyError:
                pass

    def _resolved(self, flow_id: str, action: str) -> None:
        self.hub.emit("intercept.resolved", {"id": flow_id, "action": action})
        self.hub.emit("intercept.state", self.state())

    # --- aplicar ediciones ----------------------------------------------------

    def _apply_body(self, message, edited: bytes, held: Held, headers: httpmsg.Headers) -> None:
        unchanged = edited in (held.shown_body, held.shown_body.replace(b"\r\n", b"\n"))
        has_te = "transfer-encoding" in message.headers
        if unchanged:
            if held.body_decoded and "content-encoding" not in message.headers:
                # sacaron el Content-Encoding: mandar el cuerpo ya decodificado
                message.raw_content = held.shown_body
            else:
                return
        else:
            body = httpmsg.restore_body(edited, None, headers)
            if held.body_decoded and "content-encoding" in message.headers:
                message.content = body  # re-codifica y ajusta Content-Length
                return
            message.raw_content = body
        raw = message.raw_content or b""
        if not has_te and ("content-length" in message.headers or raw):
            message.headers["content-length"] = str(len(raw))

    def _apply_request(self, held: Held, data: bytes) -> None:
        parts = httpmsg.parse_request(data)
        req = held.flow.request
        req.method = parts.method
        if parts.target.lower().startswith(("http://", "https://")):
            req.url = parts.target
        else:
            req.path = parts.target
        req.http_version = parts.http_version
        headers = parts.headers
        if parts.http_version.upper().startswith("HTTP/2"):
            host = httpmsg.header_get(headers, "host")
            if host:
                req.authority = host
                headers = httpmsg.header_remove(headers, "host")
        req.headers = MitmHeaders(httpmsg.headers_to_mitm(headers))
        self._apply_body(req, parts.body, held, headers)

    def _apply_response(self, held: Held, data: bytes) -> None:
        parts = httpmsg.parse_response(data)
        res = held.flow.response
        assert res is not None
        res.http_version = parts.http_version
        res.status_code = parts.status_code
        res.reason = parts.reason
        res.headers = MitmHeaders(httpmsg.headers_to_mitm(parts.headers))
        self._apply_body(res, parts.body, held, parts.headers)
