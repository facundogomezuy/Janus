"""SQLite: esquema y acceso concurrente (ARCHITECTURE.md §4).

Modelo de concurrencia:
  - Un único hilo escritor (FIFO): las escrituras de un mismo flow (request,
    después response) se aplican siempre en orden, y el event loop del proxy
    nunca se bloquea esperando al disco.
  - Un pool chico de lectores: WAL permite leer mientras se escribe.
Cada hilo tiene su propia conexión.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, TypeVar

log = logging.getLogger("janus.db")

T = TypeVar("T")

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS flows (
    id               TEXT PRIMARY KEY,
    seq              INTEGER NOT NULL,
    project_id       INTEGER NOT NULL DEFAULT 1 REFERENCES projects(id),
    ts               REAL NOT NULL,
    source           TEXT NOT NULL DEFAULT 'proxy',
    method           TEXT NOT NULL,
    scheme           TEXT NOT NULL,
    host             TEXT NOT NULL,
    port             INTEGER NOT NULL,
    path             TEXT NOT NULL,
    http_version     TEXT,
    req_headers      TEXT NOT NULL,
    req_body         BLOB,
    status_code      INTEGER,
    reason           TEXT,
    res_http_version TEXT,
    res_headers      TEXT,
    res_body         BLOB,
    res_length       INTEGER,
    content_type     TEXT,
    duration_ms      INTEGER,
    in_scope         INTEGER NOT NULL DEFAULT 1,
    state            TEXT NOT NULL DEFAULT 'pending',
    error            TEXT,
    edited           INTEGER NOT NULL DEFAULT 0,
    color            TEXT,
    comment          TEXT,
    client_addr      TEXT,
    server_addr      TEXT
);
CREATE INDEX IF NOT EXISTS flows_by_seq ON flows(project_id, seq);
CREATE INDEX IF NOT EXISTS flows_by_host ON flows(project_id, host);

CREATE TABLE IF NOT EXISTS scope_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL DEFAULT 1 REFERENCES projects(id),
    kind       TEXT NOT NULL CHECK (kind IN ('include', 'exclude')),
    matcher    TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    position   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS repeater_tabs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL DEFAULT 1 REFERENCES projects(id),
    name          TEXT NOT NULL,
    host          TEXT NOT NULL DEFAULT '',
    port          INTEGER NOT NULL DEFAULT 443,
    tls           INTEGER NOT NULL DEFAULT 1,
    raw_request   BLOB,
    orig_body     BLOB,
    encoding      TEXT NOT NULL DEFAULT 'utf-8',
    raw_response  BLOB,
    response_meta TEXT,
    position      INTEGER NOT NULL DEFAULT 0,
    updated_at    REAL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="janus-db-w")
        self._readers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="janus-db-r")
        self._init_schema()

    # --- conexiones -------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        with self._conns_lock:
            self._conns.append(conn)
        return conn

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = self._connect()
        return conn

    def _init_schema(self) -> None:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO projects (id, name, created_at) "
                    "VALUES (1, 'Default', strftime('%s','now'))"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
        finally:
            conn.close()

    # --- ejecución --------------------------------------------------------

    def _run_read(self, fn: Callable[..., T], args: tuple) -> T:
        return fn(self._conn(), *args)

    def _run_write(self, fn: Callable[..., T], args: tuple) -> T:
        conn = self._conn()
        with conn:  # commit / rollback
            return fn(conn, *args)

    async def read(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if kwargs:
            fn = functools.partial(fn, **kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._readers, self._run_read, fn, args)

    async def write(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if kwargs:
            fn = functools.partial(fn, **kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._writer, self._run_write, fn, args)

    def write_nowait(self, fn: Callable[..., Any], *args: Any) -> Future:
        """Encola una escritura sin esperar (lo usa el addon del proxy)."""
        fut = self._writer.submit(self._run_write, fn, args)
        fut.add_done_callback(_log_failure)
        return fut

    def write_sync(self, fn: Callable[..., T], *args: Any) -> T:
        """Escritura bloqueante, para el arranque (antes de tener event loop)."""
        return self._writer.submit(self._run_write, fn, args).result()

    def read_sync(self, fn: Callable[..., T], *args: Any) -> T:
        return self._readers.submit(self._run_read, fn, args).result()

    def close(self) -> None:
        try:
            self._writer.submit(
                lambda: self._conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
            ).result(timeout=5)
        except Exception:  # noqa: BLE001 - el cierre no debe fallar
            log.debug("checkpoint final falló", exc_info=True)
        self._writer.shutdown(wait=True)
        self._readers.shutdown(wait=True)
        with self._conns_lock:
            for conn in self._conns:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._conns.clear()


def _log_failure(fut: Future) -> None:
    exc = fut.exception()
    if exc is not None:
        log.error("escritura en la base falló", exc_info=exc)
