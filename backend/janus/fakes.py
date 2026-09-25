"""Data falsa para M0.

Todavía no está conectado el motor mitmproxy (eso entra en M1). Mientras tanto
el historial se sirve desde acá para poder armar y trastear la UI end-to-end.
La forma de cada flow ya calca el modelo de datos de ARCHITECTURE.md §4, así que
cuando entre el addon real solo cambia el origen, no el contrato con el frontend.
"""
from __future__ import annotations


def _flow(
    fid: str,
    ts: str,
    method: str,
    scheme: str,
    host: str,
    port: int,
    path: str,
    status_code: int,
    reason: str,
    req_headers: dict[str, str],
    req_body: str,
    res_headers: dict[str, str],
    res_body: str,
    duration_ms: int,
    in_scope: bool = True,
) -> dict:
    length = len(res_body.encode())
    return {
        "id": fid,
        "ts": ts,
        "source": "proxy",
        "method": method,
        "scheme": scheme,
        "host": host,
        "port": port,
        "path": path,
        "http_version": "HTTP/1.1",
        "status_code": status_code,
        "reason": reason,
        "length": length,
        "duration_ms": duration_ms,
        "in_scope": in_scope,
        "request": {"headers": req_headers, "body": req_body},
        "response": {"headers": res_headers, "body": res_body},
    }


FAKE_FLOWS: list[dict] = [
    _flow(
        "f-0011", "18:04:12", "POST", "https", "api.target.com", 443, "/token", 401,
        "Unauthorized",
        {"Host": "api.target.com", "Content-Type": "application/json", "Accept": "*/*"},
        '{"grant_type":"password","username":"admin","password":"hunter2"}',
        {"Content-Type": "application/json", "WWW-Authenticate": "Bearer"},
        '{"error":"invalid_grant","error_description":"bad credentials"}',
        142,
    ),
    _flow(
        "f-0012", "18:04:20", "GET", "https", "target.com", 443, "/dashboard", 302,
        "Found",
        {"Host": "target.com", "Cookie": "sid=abc123", "Accept": "text/html"},
        "",
        {"Location": "/login", "Set-Cookie": "sid=; Max-Age=0"},
        "",
        61,
    ),
    _flow(
        "f-0013", "18:04:33", "GET", "https", "api.target.com", 443, "/me", 200, "OK",
        {"Host": "api.target.com", "Authorization": "Bearer eyJhbGci...", "Accept": "application/json"},
        "",
        {"Content-Type": "application/json", "Cache-Control": "no-store"},
        '{"id":42,"user":"admin","roles":["staff","billing"],"mfa":false}',
        88,
    ),
    _flow(
        "f-0014", "18:04:41", "POST", "https", "api.target.com", 443, "/login", 200, "OK",
        {"Host": "api.target.com", "Content-Type": "application/json", "Origin": "https://target.com"},
        '{"user":"admin","pass":"correct-horse"}',
        {"Content-Type": "application/json", "Set-Cookie": "sid=9f2a...; HttpOnly; Secure"},
        '{"token":"eyJhbGciOiJIUzI1NiJ9...","expires_in":3600,"scope":"full"}',
        203,
    ),
    _flow(
        "f-0015", "18:04:52", "GET", "https", "cdn.assets.io", 443, "/app.4f2.js", 200, "OK",
        {"Host": "cdn.assets.io", "Accept": "*/*", "Referer": "https://target.com/"},
        "",
        {"Content-Type": "application/javascript", "Cache-Control": "max-age=31536000"},
        "// (bundle minificado, 214 KB)",
        512,
        in_scope=False,
    ),
    _flow(
        "f-0016", "18:05:03", "PUT", "https", "api.target.com", 443, "/users/42/email", 403,
        "Forbidden",
        {"Host": "api.target.com", "Authorization": "Bearer eyJhbGci...", "Content-Type": "application/json"},
        '{"email":"attacker@evil.com"}',
        {"Content-Type": "application/json"},
        '{"error":"forbidden","detail":"missing scope: users:write"}',
        97,
    ),
]
