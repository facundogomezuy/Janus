"""Historial de flows: persistencia en SQLite + conversión desde mitmproxy."""
from __future__ import annotations

import json
import shlex
import time
from typing import TYPE_CHECKING, Any

from . import httpmsg
from .httpmsg import Headers

if TYPE_CHECKING:
    from mitmproxy.http import HTTPFlow

    from .scope import Scope

SUMMARY_COLS = (
    "id, seq, ts, source, method, scheme, host, port, path, http_version, status_code, "
    "reason, res_length, content_type, duration_ms, in_scope, state, error, edited, color, comment"
)

MAX_STORED_BODY = 16 * 1024 * 1024

COLORS = ("red", "orange", "yellow", "green", "teal", "blue", "purple", "gray")


def _fmt_addr(addr) -> str | None:
    if not addr:
        return None
    host, port = addr[0], addr[1]
    return f"[{host}]:{port}" if ":" in str(host) else f"{host}:{port}"


def _clip(body: bytes | None) -> bytes | None:
    if body is None:
        return None
    return body[:MAX_STORED_BODY]


def default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def build_url(scheme: str, host: str, port: int, path: str) -> str:
    h = f"[{host}]" if ":" in host else host
    if port != default_port(scheme):
        h = f"{h}:{port}"
    return f"{scheme}://{h}{path}"


# --- mitmproxy -> filas -----------------------------------------------------

def request_record(flow: HTTPFlow) -> dict[str, Any]:
    req = flow.request
    return {
        "method": req.method,
        "scheme": req.scheme,
        "host": req.pretty_host,
        "port": req.port,
        "path": req.path or "/",
        "http_version": req.http_version,
        "req_headers": json.dumps(httpmsg.headers_from_mitm(req.headers.fields)),
        "req_body": _clip(req.raw_content),
        "client_addr": _fmt_addr(flow.client_conn.peername),
    }


def response_record(flow: HTTPFlow) -> dict[str, Any]:
    res = flow.response
    assert res is not None
    raw = res.raw_content
    length = len(raw) if raw is not None else None
    if length is None:
        cl = res.headers.get("content-length")
        length = int(cl) if cl and cl.isdigit() else None
    start = flow.request.timestamp_start
    end = res.timestamp_end or res.timestamp_start or time.time()
    return {
        "status_code": res.status_code,
        "reason": res.reason,
        "res_http_version": res.http_version,
        "res_headers": json.dumps(httpmsg.headers_from_mitm(res.headers.fields)),
        "res_body": _clip(raw),
        "res_length": length,
        "content_type": res.headers.get("content-type"),
        "duration_ms": int(max(0.0, end - start) * 1000) if start else None,
        "server_addr": _fmt_addr(flow.server_conn.peername),
        "state": "complete",
    }


# --- SQL --------------------------------------------------------------------

def max_seq(conn) -> int:
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM flows").fetchone()
    return int(row["m"])


def insert(conn, values: dict[str, Any]) -> None:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT OR REPLACE INTO flows ({cols}) VALUES ({marks})", tuple(values.values()))


def update(conn, flow_id: str, values: dict[str, Any]) -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE flows SET {sets} WHERE id = ?", (*values.values(), flow_id))


def summary(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "ts": row["ts"],
        "source": row["source"],
        "method": row["method"],
        "scheme": row["scheme"],
        "host": row["host"],
        "port": row["port"],
        "path": row["path"],
        "http_version": row["http_version"],
        "status_code": row["status_code"],
        "reason": row["reason"],
        "length": row["res_length"],
        "content_type": row["content_type"],
        "duration_ms": row["duration_ms"],
        "in_scope": bool(row["in_scope"]),
        "state": row["state"],
        "error": row["error"],
        "edited": bool(row["edited"]),
        "color": row["color"],
        "comment": row["comment"],
    }


def summary_from_values(fid: str, values: dict[str, Any]) -> dict[str, Any]:
    """Resumen sin pasar por la base (para emitir por WS al instante)."""
    row = {k: values.get(k) for k in (
        "seq", "ts", "source", "method", "scheme", "host", "port", "path", "http_version",
        "status_code", "reason", "res_length", "content_type", "duration_ms", "in_scope",
        "state", "error", "edited", "color", "comment",
    )}
    row["id"] = fid
    row["in_scope"] = row["in_scope"] if row["in_scope"] is not None else 1
    row["edited"] = row["edited"] or 0
    row["state"] = row["state"] or "pending"
    row["source"] = row["source"] or "proxy"
    return summary(row)


def list_summaries(conn, limit: int = 5000, before_seq: int | None = None) -> list[dict]:
    if before_seq:
        rows = conn.execute(
            f"SELECT {SUMMARY_COLS} FROM flows WHERE project_id = 1 AND seq < ? ORDER BY seq DESC LIMIT ?",
            (before_seq, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {SUMMARY_COLS} FROM flows WHERE project_id = 1 ORDER BY seq DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [summary(r) for r in reversed(rows)]


def count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) AS c FROM flows WHERE project_id = 1").fetchone()["c"])


def get_row(conn, flow_id: str):
    return conn.execute("SELECT * FROM flows WHERE id = ?", (flow_id,)).fetchone()


def delete_all(conn) -> None:
    conn.execute("DELETE FROM flows WHERE project_id = 1")


def delete_ids(conn, ids: list[str]) -> None:
    conn.executemany("DELETE FROM flows WHERE id = ?", [(i,) for i in ids])


def set_meta(conn, flow_id: str, changes: dict[str, Any]):
    allowed = {k: v for k, v in changes.items() if k in ("color", "comment")}
    if allowed:
        update(conn, flow_id, allowed)
    return conn.execute(f"SELECT {SUMMARY_COLS} FROM flows WHERE id = ?", (flow_id,)).fetchone()


def search(conn, needle: str, limit: int = 20000) -> list[str]:
    """Busca en host, path, headers y cuerpos (bytes crudos)."""
    raw = needle.encode("utf-8")
    rows = conn.execute(
        """
        SELECT id FROM flows WHERE project_id = 1 AND (
            instr(lower(host || path), lower(?)) > 0
            OR instr(lower(req_headers), lower(?)) > 0
            OR instr(lower(COALESCE(res_headers, '')), lower(?)) > 0
            OR instr(req_body, ?) > 0
            OR instr(res_body, ?) > 0
        ) ORDER BY seq DESC LIMIT ?
        """,
        (needle, needle, needle, raw, raw, limit),
    ).fetchall()
    return [r["id"] for r in rows]


def recompute_scope(conn, scope: Scope) -> list[tuple[str, bool]]:
    rows = conn.execute("SELECT id, scheme, host, port, path, in_scope FROM flows WHERE project_id = 1").fetchall()
    changed = []
    for r in rows:
        now = scope.in_scope(r["scheme"], r["host"], r["port"], r["path"])
        if now != bool(r["in_scope"]):
            changed.append((r["id"], now))
    conn.executemany("UPDATE flows SET in_scope = ? WHERE id = ?", [(int(v), i) for i, v in changed])
    return changed


# --- detalle para el visor ----------------------------------------------------

def _display_request_headers(row) -> Headers:
    headers: Headers = json.loads(row["req_headers"])
    if httpmsg.header_get(headers, "host") is None:
        authority = row["host"] if row["port"] == default_port(row["scheme"]) else f"{row['host']}:{row['port']}"
        headers = [("Host", authority)] + headers  # HTTP/2 viaja en :authority
    return headers


def detail(row) -> dict[str, Any]:
    out = summary(row)
    out["url"] = build_url(row["scheme"], row["host"], row["port"], row["path"])
    out["client_addr"] = row["client_addr"]
    out["server_addr"] = row["server_addr"]
    req_headers = _display_request_headers(row)
    out["request"] = {
        "first_line": f"{row['method']} {row['path']} {row['http_version'] or 'HTTP/1.1'}",
        "headers": req_headers,
        "body": httpmsg.body_view(row["req_body"], req_headers),
    }
    if row["status_code"] is not None:
        res_headers: Headers = json.loads(row["res_headers"] or "[]")
        first = f"{row['res_http_version'] or 'HTTP/1.1'} {row['status_code']} {row['reason'] or ''}".rstrip()
        out["response"] = {
            "first_line": first,
            "headers": res_headers,
            "body": httpmsg.body_view(row["res_body"], res_headers),
        }
    else:
        out["response"] = None
    return out


def request_for_repeater(row) -> tuple[bytes, bytes]:
    """Request listo para el repeater (HTTP/1.1) + cuerpo original."""
    headers = _display_request_headers(row)
    # headers propios de HTTP/2 que no tienen sentido en HTTP/1.1
    headers = [(k, v) for k, v in headers if not k.startswith(":")]
    version = row["http_version"] or "HTTP/1.1"
    if not version.startswith("HTTP/1"):
        version = "HTTP/1.1"
    body = row["req_body"] or b""
    if body and httpmsg.header_get(headers, "content-length") is None \
            and httpmsg.header_get(headers, "transfer-encoding") is None:
        headers.append(("Content-Length", str(len(body))))
    data = httpmsg.assemble_request(row["method"], row["path"], version, headers, body)
    return data, body


def as_curl(row) -> str:
    headers = _display_request_headers(row)
    url = build_url(row["scheme"], row["host"], row["port"], row["path"])
    parts = ["curl", "-i", "-s", "-k"]
    if row["method"] != "GET":
        parts += ["-X", row["method"]]
    parts.append(shlex.quote(url))
    compressed = False
    for k, v in headers:
        lk = k.lower()
        if lk in ("content-length", "connection") or lk.startswith(":"):
            continue
        if lk == "host" and v.split(":")[0] == row["host"]:
            continue
        if lk == "accept-encoding":
            compressed = True
        parts += ["-H", shlex.quote(f"{k}: {v}")]
    if compressed:
        parts.append("--compressed")
    body = row["req_body"] or b""
    if body:
        try:
            parts += ["--data-raw", shlex.quote(body.decode("utf-8"))]
        except UnicodeDecodeError:
            parts.append("--data-binary @body.bin  # cuerpo binario omitido")
    return " ".join(parts)
