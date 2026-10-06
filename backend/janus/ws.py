"""Hub de WebSocket: stream en vivo hacia el frontend (ARCHITECTURE.md §2).

Los eventos se agrupan en lotes cada ~50 ms: con mucho tráfico el frontend
recibe un mensaje (una lista) por tick en vez de cientos, y renderiza una vez.
Dentro de un lote, los ``flow.update`` repetidos del mismo flow se colapsan.

Eventos: flow.new, flow.update, flows.deleted, history.cleared,
intercept.pending, intercept.resolved, intercept.state, proxy.status,
proxy.event, scope.changed, settings.changed, ca.changed, sysproxy.changed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from starlette.websockets import WebSocket

log = logging.getLogger("janus.ws")

FLUSH_INTERVAL = 0.05


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._pending: list[dict] = []
        self._scheduled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_thread = threading.get_ident()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    def emit(self, type_: str, data: Any = None) -> None:
        """Encola un evento. Seguro de llamar desde cualquier hilo."""
        if self._loop is None:
            return
        if threading.get_ident() != self._loop_thread:
            self._loop.call_soon_threadsafe(self.emit, type_, data)
            return
        if type_ == "flow.update" and isinstance(data, dict):
            fid = data.get("id")
            self._pending = [
                e for e in self._pending
                if not (e["type"] == "flow.update" and e["data"].get("id") == fid)
            ]
        self._pending.append({"type": type_, "data": data})
        if not self._scheduled:
            self._scheduled = True
            self._loop.call_later(FLUSH_INTERVAL, self._spawn_flush)

    def _spawn_flush(self) -> None:
        task = asyncio.ensure_future(self._flush())
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def _flush(self) -> None:
        self._scheduled = False
        events, self._pending = self._pending, []
        if not events or not self._clients:
            return
        payload = json.dumps(events, default=str, ensure_ascii=False)
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 - cliente caído
                self._clients.discard(ws)

    async def close_all(self) -> None:
        for ws in list(self._clients):
            try:
                await ws.close(code=1001)
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
