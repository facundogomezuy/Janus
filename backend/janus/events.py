"""Registro de eventos del proxy (errores TLS, puertos ocupados, etc.).

Se muestran en el panel "Eventos" de la UI. Guarda los últimos N en memoria y
los emite por el hub. También captura los warnings/errores que loguea mitmproxy.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ws import Hub

MAX_EVENTS = 300


class EventLog:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self._items: collections.deque[dict] = collections.deque(maxlen=MAX_EVENTS)
        self._lock = threading.Lock()
        self._seq = 0
        # evita inundar la UI con el mismo aviso (ej. 200 fallas TLS del mismo host)
        self._last_by_key: dict[str, float] = {}

    def add(self, level: str, message: str, *, source: str = "janus", key: str | None = None,
            throttle: float = 0.0, hint: str | None = None) -> None:
        now = time.time()
        with self._lock:
            if key and throttle:
                last = self._last_by_key.get(key, 0.0)
                if now - last < throttle:
                    return
                self._last_by_key[key] = now
            self._seq += 1
            item = {"id": self._seq, "ts": now, "level": level, "source": source,
                    "message": message, "hint": hint}
            self._items.append(item)
        self.hub.emit("proxy.event", item)

    def recent(self) -> list[dict]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class MitmLogHandler(logging.Handler):
    """Manda al EventLog los warnings/errores de mitmproxy."""

    IGNORED = (
        "Unhandled asyncio error",  # ruido de conexiones cortadas por el cliente
    )

    def __init__(self, events: EventLog) -> None:
        super().__init__(level=logging.WARNING)
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        if any(msg.startswith(p) for p in self.IGNORED):
            return
        level = "error" if record.levelno >= logging.ERROR else "warn"
        first_line = msg.strip().splitlines()[0] if msg.strip() else msg
        self.events.add(level, first_line[:500], source="proxy",
                        key=f"log:{first_line[:120]}", throttle=5.0)
