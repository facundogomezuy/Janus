"""`localhost` rápido en Windows.

En Windows `localhost` resuelve primero a ``::1``. Si el servidor de prueba
escucha solo en IPv4 (lo más común), la conexión a ``::1`` es rechazada... pero
Windows tarda ~2 s en reportarlo (reintenta el SYN), así que cada request a
``localhost`` quedaba 2 s más lento. Acá se prueban ``127.0.0.1`` y ``::1`` en
paralelo y se usa el que contesta primero (con un caché corto por puerto).
"""
from __future__ import annotations

import asyncio
import contextlib
import time

CACHE_TTL = 15.0
PROBE_TIMEOUT = 1.0
_cache: dict[int, tuple[str, float]] = {}


def is_localhost(host: str | None) -> bool:
    return bool(host) and str(host).lower().rstrip(".") == "localhost"


async def _probe(ip: str, port: int) -> str:
    _reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), PROBE_TIMEOUT)
    writer.close()
    return ip


async def pick(port: int) -> str:
    hit = _cache.get(port)
    if hit and time.monotonic() - hit[1] < CACHE_TTL:
        return hit[0]
    tasks = [asyncio.ensure_future(_probe(ip, port)) for ip in ("127.0.0.1", "::1")]
    try:
        for fut in asyncio.as_completed(tasks):
            with contextlib.suppress(OSError, TimeoutError):
                chosen = await fut
                _cache[port] = (chosen, time.monotonic())
                return chosen
    finally:
        for t in tasks:
            t.cancel()
    return "127.0.0.1"  # nadie escucha: que falle rápido por IPv4 (sin cachear)
