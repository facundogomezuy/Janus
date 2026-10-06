"""Repeater: pestañas persistidas + armado de la respuesta para el visor."""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from . import httpmsg
from .rawhttp import RawResult

TAB_COLS = "id, name, host, port, tls, raw_request, orig_body, encoding, response_meta, position, updated_at"

DEFAULT_REQUEST = (
    "GET / HTTP/1.1\r\n"
    "Host: example.com\r\n"
    "User-Agent: Janus\r\n"
    "Accept: */*\r\n"
    "Connection: close\r\n"
    "\r\n"
)


def tab_json(row) -> dict[str, Any]:
    raw = row["raw_request"] or b""
    encoding = row["encoding"] or "utf-8"
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "tls": bool(row["tls"]),
        "message": {"text": raw.decode(encoding, "replace"), "encoding": encoding},
        "response": json.loads(row["response_meta"]) if row["response_meta"] else None,
        "position": row["position"],
    }


def list_tabs(conn) -> list[dict]:
    rows = conn.execute(f"SELECT {TAB_COLS} FROM repeater_tabs WHERE project_id = 1 ORDER BY position, id").fetchall()
    return [tab_json(r) for r in rows]


def get_row(conn, tab_id: int):
    return conn.execute(f"SELECT {TAB_COLS} FROM repeater_tabs WHERE id = ?", (tab_id,)).fetchone()


def get_tab(conn, tab_id: int) -> dict | None:
    row = get_row(conn, tab_id)
    return tab_json(row) if row else None


def _next_name(conn) -> str:
    """Siguiente número libre (no se repite al cerrar pestañas del medio)."""
    names = conn.execute("SELECT name FROM repeater_tabs WHERE project_id = 1").fetchall()
    used = [int(r["name"]) for r in names if r["name"].isdigit()]
    return str(max(used, default=0) + 1)


def create_tab(conn, *, name: str | None, host: str, port: int, tls: bool,
               raw_request: bytes, encoding: str, orig_body: bytes | None) -> dict:
    pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM repeater_tabs").fetchone()["p"]
    cur = conn.execute(
        "INSERT INTO repeater_tabs (project_id, name, host, port, tls, raw_request, orig_body, encoding, position, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name or _next_name(conn), host, port, int(tls), raw_request, orig_body, encoding, pos, time.time()),
    )
    return get_tab(conn, cur.lastrowid)  # type: ignore[arg-type]


def update_tab(conn, tab_id: int, fields: dict[str, Any]) -> dict | None:
    allowed = {"name", "host", "port", "tls", "raw_request", "encoding", "response_meta", "position", "orig_body"}
    values = {k: v for k, v in fields.items() if k in allowed}
    if values:
        values["updated_at"] = time.time()
        sets = ", ".join(f"{k} = ?" for k in values)
        conn.execute(f"UPDATE repeater_tabs SET {sets} WHERE id = ?", (*values.values(), tab_id))
    return get_tab(conn, tab_id)


def delete_tab(conn, tab_id: int) -> None:
    conn.execute("DELETE FROM repeater_tabs WHERE id = ?", (tab_id,))


def result_json(result: RawResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": result.ok,
        "error": result.error,
        "elapsed_ms": result.elapsed_ms,
        "connect_ms": result.connect_ms,
        "bytes_sent": result.bytes_sent,
        "bytes_received": len(result.raw),
        "tls_version": result.tls_version,
        "cipher": result.cipher,
        "peer": result.peer,
        "raw_b64": base64.b64encode(result.raw).decode("ascii"),
        "response": None,
        "sent_at": time.time(),
    }
    resp = result.response
    if resp is not None:
        out["response"] = {
            "first_line": f"{resp.http_version} {resp.status_code} {resp.reason}".rstrip(),
            "http_version": resp.http_version,
            "status_code": resp.status_code,
            "reason": resp.reason,
            "headers": resp.headers,
            "body": httpmsg.body_view(resp.body, resp.headers),
            "complete": resp.complete,
        }
    return out
