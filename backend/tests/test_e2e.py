"""End-to-end: backend real (subproceso, modo sidecar) + targets HTTP/HTTPS locales.

Cubre la tubería completa: proxy -> historial -> WebSocket, intercept (forward
con edición, drop, responses), repeater crudo, scope, cambio de puerto en
caliente, CA y apagado ordenado por EOF de stdin (como lo hace Tauri).
"""
from __future__ import annotations

import base64
import datetime
import gzip
import http.client
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect as ws_connect

BACKEND = Path(__file__).resolve().parents[1]


# --- target local ---------------------------------------------------------------

class EchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _echo(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path.startswith("/chunked"):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            for part in (b"hola ", b"desde ", b"chunked"):
                self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        payload = json.dumps({
            "method": self.command,
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body.decode("latin-1"),
        }).encode()
        headers = {"Content-Type": "application/json"}
        if self.path.startswith("/gzip"):
            payload = gzip.compress(payload)
            headers["Content-Encoding"] = "gzip"
        headers["Content-Length"] = str(len(payload))
        self._send(200, payload, headers)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _echo

    def log_message(self, *args) -> None:  # silencio
        pass


def _self_signed(tmp: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp / "target.crt", tmp / "target.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
    ))
    return cert_file, key_file


def _start_target(tls_files: tuple[Path, Path] | None = None) -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
    srv.daemon_threads = True
    if tls_files:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*map(str, tls_files))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _start_raw_target() -> tuple[socket.socket, int]:
    """Servidor TCP que contesta bytes que no son HTTP (estilo 0.9) y cierra."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(5)
                try:
                    conn.recv(65536)
                    conn.sendall(b"<html>no soy HTTP</html>\n")
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- cliente de la API -----------------------------------------------------------------

class Api:
    def __init__(self, port: int, token: str) -> None:
        self.port, self.token = port, token

    def call(self, method: str, path: str, body=None, *, token: bool = True, host: str | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {self.token}"
        if host:
            headers["Host"] = host
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        ctype = r.getheader("content-type") or ""
        return r.status, (json.loads(raw) if "json" in ctype and raw else raw)

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, body if body is not None else {}, **kw)

    def put(self, path, body, **kw):
        return self.call("PUT", path, body, **kw)

    def wait_for(self, predicate, timeout: float = 10.0, interval: float = 0.05):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(interval)
        raise AssertionError("timeout esperando condición")

    def find_flow(self, path_fragment: str, state: str | None = "complete"):
        def pred():
            _, data = self.get("/api/history")
            for f in reversed(data["flows"]):
                if path_fragment in f["path"] and (state is None or f["state"] == state):
                    return f
            return None
        return self.wait_for(pred)


def via_proxy(proxy_port: int, method: str, host: str, port: int, path: str, *, body: bytes | None = None,
              headers: dict | None = None, tls: bool = False, cafile: Path | None = None, timeout: float = 15):
    if tls:
        ctx = ssl.create_default_context(cafile=str(cafile))
        conn = http.client.HTTPSConnection("127.0.0.1", proxy_port, context=ctx, timeout=timeout)
        conn.set_tunnel(host, port)
        target = path
    else:
        conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=timeout)
        target = f"http://{host}:{port}{path}"
    conn.request(method, target, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data


def _read_handshake(proc, timeout: float = 30.0) -> tuple[dict | None, list[str]]:
    """Lee stdout hasta JANUS_READY (con --sidecar, antes llega "JANUS_PID <pid>")."""
    box: dict = {"lines": []}

    def run():
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode().strip()
            box["lines"].append(line)
            if line.startswith("JANUS_READY "):
                box["hs"] = json.loads(line.split(" ", 1)[1])
                return

    reader = threading.Thread(target=run, daemon=True)
    reader.start()
    reader.join(timeout)
    return box.get("hs"), box["lines"]


# --- fixtures ----------------------------------------------------------------------------

class Janus:
    def __init__(self, proc, handshake, proxy_port, data_dir):
        self.proc, self.hs, self.proxy_port, self.data_dir = proc, handshake, proxy_port, data_dir
        self.api = Api(handshake["port"], handshake["token"])
        self.ca_pem = data_dir / "ca" / "mitmproxy-ca-cert.pem"
        self.stdout_lines: list[str] = []


@pytest.fixture(scope="module")
def targets(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("targets")
    http_srv, http_port = _start_target()
    https_srv, https_port = _start_target(_self_signed(tmp))
    raw_srv, raw_port = _start_raw_target()
    yield {"http": http_port, "https": https_port, "raw": raw_port}
    http_srv.shutdown()
    https_srv.shutdown()
    raw_srv.close()


@pytest.fixture(scope="module")
def janus(tmp_path_factory):
    data = tmp_path_factory.mktemp("janus-data")
    proxy_port = _free_port()
    env = {**os.environ, "JANUS_DATA_DIR": str(data), "PYTHONUTF8": "1"}
    env.pop("JANUS_DEV", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "janus", "--sidecar", "--proxy-port", str(proxy_port)],
        cwd=BACKEND, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stderr_lines: list[bytes] = []
    threading.Thread(target=lambda: stderr_lines.extend(iter(proc.stderr.readline, b"")), daemon=True).start()
    hs, lines = _read_handshake(proc)
    assert hs, f"sin handshake: {lines!r} {b''.join(stderr_lines)!r}"
    j = Janus(proc, hs, proxy_port, data)
    j.stdout_lines = lines
    yield j
    # apagado como Tauri: cerrar stdin -> EOF -> shutdown ordenado
    proc.stdin.close()
    rc = proc.wait(timeout=20)
    log_text = (data / "logs" / "janus.log").read_text(encoding="utf-8")
    assert rc == 0, b"".join(stderr_lines).decode(errors="replace")
    assert "apagando" in log_text


# --- tests -----------------------------------------------------------------------------------

def test_handshake_and_status(janus):
    # el PID del intérprete llega antes del handshake (Tauri lo usa si tiene que cortar el arranque)
    assert janus.stdout_lines[0] == f"JANUS_PID {janus.hs['pid']}"
    assert janus.hs["proxy"]["running"] is True
    assert janus.hs["proxy"]["listen_port"] == janus.proxy_port
    status, data = janus.api.get("/api/status")
    assert status == 200 and data["proxy"]["running"]


def test_auth_and_trusted_host(janus):
    assert janus.api.get("/api/status", token=False)[0] == 401
    bad = Api(janus.api.port, "x" * 43)
    assert bad.get("/api/status")[0] == 401
    assert janus.api.get(f"/api/status?token={janus.api.token}", token=False)[0] == 200
    assert janus.api.get("/api/status", host="evil.example.com")[0] == 400


def test_http_flow_recorded_with_detail(janus, targets):
    status, _, body = via_proxy(janus.proxy_port, "POST", "127.0.0.1", targets["http"], "/login?u=admin",
                                body=b'{"pass":"hunter2"}', headers={"Content-Type": "application/json"})
    assert status == 200 and json.loads(body)["body"] == '{"pass":"hunter2"}'
    flow = janus.api.find_flow("/login?u=admin")
    assert flow["method"] == "POST" and flow["status_code"] == 200 and flow["in_scope"]
    _, detail = janus.api.get(f"/api/history/{flow['id']}")
    assert detail["request"]["body"]["text"] == '{"pass":"hunter2"}'
    assert json.loads(detail["response"]["body"]["text"])["path"] == "/login?u=admin"
    _, curl = janus.api.get(f"/api/history/{flow['id']}/curl")
    assert b"curl" in curl and b"--data-raw" in curl


def test_https_with_janus_ca(janus, targets):
    status, _, body = via_proxy(janus.proxy_port, "GET", "127.0.0.1", targets["https"], "/secure",
                                tls=True, cafile=janus.ca_pem)
    assert status == 200 and json.loads(body)["path"] == "/secure"
    flow = janus.api.find_flow("/secure")
    assert flow["scheme"] == "https" and flow["port"] == targets["https"]


def test_gzip_response_is_decoded_in_detail(janus, targets):
    via_proxy(janus.proxy_port, "GET", "127.0.0.1", targets["http"], "/gzip-me")
    flow = janus.api.find_flow("/gzip-me")
    _, detail = janus.api.get(f"/api/history/{flow['id']}")
    body = detail["response"]["body"]
    assert body["content_encoding"] == "gzip"
    assert json.loads(body["text"])["path"] == "/gzip-me"


def test_websocket_events(janus, targets):
    url = f"ws://127.0.0.1:{janus.api.port}/ws?token={janus.api.token}"
    with ws_connect(url, open_timeout=10) as ws:
        via_proxy(janus.proxy_port, "GET", "127.0.0.1", targets["http"], "/ws-check")
        seen = set()
        deadline = time.time() + 10
        while time.time() < deadline and not {"flow.new", "flow.update"} <= seen:
            for ev in json.loads(ws.recv(timeout=10)):
                if ev["type"].startswith("flow.") and ev["data"]["path"] == "/ws-check":
                    seen.add(ev["type"])
        assert {"flow.new", "flow.update"} <= seen
    # sin token: se rechaza en el handshake
    with pytest.raises(InvalidStatus):
        ws_connect(f"ws://127.0.0.1:{janus.api.port}/ws", open_timeout=10)


def _background_request(*args, **kwargs):
    box: dict = {}

    def run():
        try:
            box["result"] = via_proxy(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, box


def _wait_held(api: Api, path: str, phase: str = "request"):
    def pred():
        _, data = api.get("/api/intercept")
        return next((i for i in data["queue"] if i["path"] == path and i["phase"] == phase), None)
    return api.wait_for(pred)


def test_intercept_forward_with_edit(janus, targets):
    api = janus.api
    assert api.put("/api/intercept", {"enabled": True, "requests": True, "responses": False,
                                      "only_in_scope": False, "filter": ""})[0] == 200
    t, box = _background_request(janus.proxy_port, "POST", "127.0.0.1", targets["http"], "/edit-me",
                                 body=b"original", headers={"Content-Type": "text/plain"})
    item = _wait_held(api, "/edit-me")
    text = item["message"]["text"].replace("\r\n", "\n")
    assert text.endswith("\n\noriginal")
    head, _ = text.split("\n\n", 1)
    edited = head + "\nX-Janus: 1\n\neditado por janus"
    assert api.post(f"/api/intercept/{item['id']}/forward",
                    {"message": {"text": edited, "encoding": "utf-8"}})[0] == 200
    t.join(15)
    status, _, body = box["result"]
    echo = json.loads(body)
    assert status == 200
    assert echo["body"] == "editado por janus"
    assert echo["headers"]["x-janus"] == "1"
    assert echo["headers"]["content-length"] == str(len("editado por janus"))
    flow = api.find_flow("/edit-me")
    assert flow["edited"] is True
    api.put("/api/intercept", {"enabled": False})


def test_intercept_drop(janus, targets):
    api = janus.api
    api.put("/api/intercept", {"enabled": True, "requests": True, "only_in_scope": False})
    t, box = _background_request(janus.proxy_port, "GET", "127.0.0.1", targets["http"], "/drop-me")
    item = _wait_held(api, "/drop-me")
    assert api.post(f"/api/intercept/{item['id']}/drop")[0] == 200
    t.join(15)
    assert "error" in box or box["result"][0] >= 500
    flow = api.find_flow("/drop-me", state="error")
    assert "drop" in flow["error"]
    api.put("/api/intercept", {"enabled": False})


def test_intercept_response_edit_and_disable_releases(janus, targets):
    api = janus.api
    api.put("/api/intercept", {"enabled": True, "requests": False, "responses": True, "only_in_scope": False})
    t, box = _background_request(janus.proxy_port, "GET", "127.0.0.1", targets["http"], "/gzip-resp")
    item = _wait_held(api, "/gzip-resp", phase="response")
    assert item["body_decoded"] is True  # gzip mostrado decodificado
    text = item["message"]["text"].replace("\r\n", "\n")
    head = text.split("\n\n", 1)[0].replace("200 OK", "203 Editada")
    api.post(f"/api/intercept/{item['id']}/forward", {"message": {"text": head + "\n\n{\"hacked\": true}"}})
    t.join(15)
    status, headers, body = box["result"]
    assert status == 203
    assert gzip.decompress(body) == b'{"hacked": true}'  # se re-codificó con gzip
    # apagar el intercept libera lo retenido
    api.put("/api/intercept", {"enabled": True, "requests": True, "responses": False})
    t, box = _background_request(janus.proxy_port, "GET", "127.0.0.1", targets["http"], "/release-me")
    _wait_held(api, "/release-me")
    api.put("/api/intercept", {"enabled": False})
    t.join(15)
    assert box["result"][0] == 200


def test_intercept_filter_validation(janus):
    status, data = janus.api.put("/api/intercept", {"filter": "~d ((("})
    assert status == 400 and "filtro" in data["detail"]
    assert janus.api.put("/api/intercept", {"filter": "~m POST"})[0] == 200
    janus.api.put("/api/intercept", {"filter": ""})


def test_repeater_from_flow_and_raw_send(janus, targets):
    api = janus.api
    via_proxy(janus.proxy_port, "PUT", "127.0.0.1", targets["http"], "/rep?x=1", body=b"line1\r\nline2",
              headers={"Content-Type": "text/plain"})
    flow = api.find_flow("/rep?x=1")
    status, tab = api.post("/api/repeater/tabs", {"from_flow": flow["id"]})
    assert status == 200 and tab["port"] == targets["http"] and tab["tls"] is False
    assert tab["message"]["text"].startswith("PUT /rep?x=1 HTTP/1.1")
    # el textarea devuelve \n: el backend debe restaurar el \r\n original del body
    text = tab["message"]["text"].replace("\r\n", "\n")
    status, res = api.post(f"/api/repeater/tabs/{tab['id']}/send", {
        "host": "127.0.0.1", "port": targets["http"], "tls": False,
        "message": {"text": text, "encoding": "utf-8"},
    })
    assert status == 200 and res["ok"], res
    echo = json.loads(res["response"]["body"]["text"])
    assert echo["body"] == "line1\r\nline2"
    assert res["flow_id"]
    # pestaña persistida con la última respuesta
    _, tabs = api.get("/api/repeater/tabs")
    saved = next(t for t in tabs["tabs"] if t["id"] == tab["id"])
    assert saved["response"]["response"]["status_code"] == 200


def test_repeater_chunked_tls_and_errors(janus, targets):
    api = janus.api
    _, tab = api.post("/api/repeater/tabs", {"host": "127.0.0.1", "port": targets["https"], "tls": True})
    raw = "GET /chunked HTTP/1.1\nHost: localhost\nConnection: close\n\n"
    _, res = api.post(f"/api/repeater/tabs/{tab['id']}/send", {
        "host": "127.0.0.1", "port": targets["https"], "tls": True, "message": {"text": raw},
    })
    assert res["ok"] and res["tls_version"]
    assert res["response"]["body"]["text"] == "hola desde chunked"
    assert b"Transfer-Encoding: chunked" in base64.b64decode(res["raw_b64"])
    # request malformado a propósito: método inexistente -> el target responde 501
    _, res = api.post(f"/api/repeater/tabs/{tab['id']}/send", {
        "host": "127.0.0.1", "port": targets["http"], "tls": False,
        "message": {"text": "BROKEN / HTTP/1.1\nHost: x\n\n"}, "fix_content_length": False,
    })
    assert res["response"]["status_code"] == 501
    # respuesta que no es HTTP (estilo 0.9): error legible y bytes crudos igual
    _, res = api.post(f"/api/repeater/tabs/{tab['id']}/send", {
        "host": "127.0.0.1", "port": targets["raw"], "tls": False, "message": {"text": raw},
    })
    assert res["ok"] is False and "no es HTTP" in res["error"] and res["raw_b64"]
    # puerto cerrado: error legible, no excepción
    _, res = api.post(f"/api/repeater/tabs/{tab['id']}/send", {
        "host": "127.0.0.1", "port": _free_port(), "tls": False, "message": {"text": raw}, "timeout": 5,
    })
    assert res["ok"] is False and "no se pudo conectar" in res["error"]
    assert api.call("DELETE", f"/api/repeater/tabs/{tab['id']}")[0] == 200


def test_scope_rules_recompute_and_test(janus, targets):
    api = janus.api
    via_proxy(janus.proxy_port, "GET", "127.0.0.1", targets["http"], "/scoped")
    flow = api.find_flow("/scoped")
    assert flow["in_scope"] is True
    status, data = api.put("/api/scope", {"rules": [
        {"kind": "include", "matcher": "*.target.com"},
        {"kind": "exclude", "matcher": "cdn.target.com"},
    ]})
    assert status == 200 and len(data["rules"]) == 2
    _, hist = api.get("/api/history")
    assert next(f for f in hist["flows"] if f["id"] == flow["id"])["in_scope"] is False
    _, t1 = api.post("/api/scope/test", {"url": "https://api.target.com/x"})
    _, t2 = api.post("/api/scope/test", {"url": "cdn.target.com"})
    assert t1["in_scope"] and t1["decrypted"]
    assert not t2["in_scope"] and not t2["decrypted"]
    assert api.put("/api/scope", {"rules": [{"kind": "include", "matcher": "https://"}]})[0] == 400
    api.put("/api/scope", {"rules": []})
    _, hist = api.get("/api/history")
    assert next(f for f in hist["flows"] if f["id"] == flow["id"])["in_scope"] is True


def test_hot_port_change(janus, targets):
    api = janus.api
    new_port = _free_port()
    status, _ = api.put("/api/settings", {"proxy.listen_port": new_port})
    assert status == 200

    def running_on_new():
        _, s = api.get("/api/status")
        p = s["proxy"]
        return p["running"] and p["listen_port"] == new_port and not p["updating"]
    api.wait_for(running_on_new)
    status, _, _ = via_proxy(new_port, "GET", "127.0.0.1", targets["http"], "/new-port")
    assert status == 200
    api.put("/api/settings", {"proxy.listen_port": janus.proxy_port})
    api.wait_for(lambda: api.get("/api/status")[1]["proxy"]["listen_port"] == janus.proxy_port
                 and api.get("/api/status")[1]["proxy"]["running"])
    assert api.put("/api/settings", {"proxy.listen_port": 0})[0] == 400


def test_port_in_use_is_reported(janus):
    api = janus.api
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    try:
        api.put("/api/settings", {"proxy.listen_port": busy})

        def errored():
            _, s = api.get("/api/status")
            return s["proxy"]["error"] if not s["proxy"]["updating"] else None
        error = api.wait_for(errored)
        assert str(busy) in error
    finally:
        blocker.close()
        api.put("/api/settings", {"proxy.listen_port": janus.proxy_port})
        api.wait_for(lambda: api.get("/api/status")[1]["proxy"]["running"])


def test_ca_info_and_export(janus):
    status, info = janus.api.get("/api/ca")
    assert status == 200 and info["cn"] == "Janus Interception CA" and info["org"] == "Janus"
    status, der = janus.api.get(f"/api/ca/cert?format=der&token={janus.api.token}", token=False)
    cert = x509.load_der_x509_certificate(der)
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Janus Interception CA"


def test_flow_meta_delete_and_search(janus, targets):
    api = janus.api
    via_proxy(janus.proxy_port, "POST", "127.0.0.1", targets["http"], "/meta", body=b"needle-xyz")
    flow = api.find_flow("/meta")
    status, row = api.call("PATCH", f"/api/history/{flow['id']}", {"color": "red", "comment": "ojo"})
    assert status == 200 and row["color"] == "red" and row["comment"] == "ojo"
    assert api.call("PATCH", f"/api/history/{flow['id']}", {"color": "chartreuse"})[0] == 400
    _, found = api.get("/api/history/search?q=needle-xyz")
    assert flow["id"] in found["ids"]
    api.post("/api/history/delete", {"ids": [flow["id"]]})
    assert api.get(f"/api/history/{flow['id']}")[0] == 404


def test_localhost_is_fast_on_windows(janus, targets):
    """El target escucha solo en IPv4: `localhost` no debe pagar los ~2 s de ::1."""
    t0 = time.perf_counter()
    status, _, _ = via_proxy(janus.proxy_port, "GET", "localhost", targets["http"], "/fast-localhost")
    assert status == 200
    assert time.perf_counter() - t0 < 1.0
    _, tab = janus.api.post("/api/repeater/tabs", {"host": "localhost", "port": targets["http"], "tls": False})
    _, res = janus.api.post(f"/api/repeater/tabs/{tab['id']}/send", {
        "host": "localhost", "port": targets["http"], "tls": False,
        "message": {"text": "GET /fast-rep HTTP/1.1\nHost: localhost\nConnection: close\n\n"},
    })
    assert res["ok"] and res["elapsed_ms"] < 1000, res


def test_client_rejecting_ca_creates_hint_event(janus, targets):
    """Un cliente que no confía en la CA de Janus -> evento con pista para la UI."""
    ctx = ssl.create_default_context()  # confianza del sistema, sin la CA de Janus
    conn = http.client.HTTPSConnection("127.0.0.1", janus.proxy_port, context=ctx, timeout=10)
    conn.set_tunnel("localhost", targets["https"])
    with pytest.raises(ssl.SSLError):
        conn.request("GET", "/untrusted")
    conn.close()

    def tls_event():
        _, data = janus.api.get("/api/events")
        return next((e for e in data["events"] if e["source"] == "tls"), None)
    ev = janus.api.wait_for(tls_event)
    assert "rechazó el certificado de Janus para localhost" in ev["message"]
    assert "CA" in ev["hint"]


def test_browser_launch_rejects_non_http_urls(janus):
    """La URL inicial va como argumento al navegador: nunca debe poder ser un flag."""
    for bad in ("--disable-web-security://x", "file:///etc/passwd", "javascript://alert(1)",
                "http://[::1", "https://[foo]/", "http://target.com:99999/",
                "https://x.com/\u0000", "x.com\n--flag"):
        status, data = janus.api.post("/api/browsers/launch", {"url": bad})
        assert status == 400 and "http" in data["detail"], (bad, status, data)


def test_scope_tester_accepts_urls_without_scheme(janus):
    """Sin esquema se asume https, aunque la query traiga otra URL; las inválidas son 400, no 500."""
    status, data = janus.api.post("/api/scope/test", {"url": "target.com/cb?redirect=https://target.com/home"})
    assert status == 200 and data["in_scope"] is True, data
    assert janus.api.post("/api/scope/test", {"url": "http://[::1"})[0] == 400


def test_api_shutdown_exits_cleanly(tmp_path):
    """/api/shutdown con el hilo de stdin vivo (modo sidecar) no debe abortar el intérprete."""
    env = {**os.environ, "JANUS_DATA_DIR": str(tmp_path), "PYTHONUTF8": "1"}
    env.pop("JANUS_DEV", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "janus", "--sidecar", "--proxy-port", str(_free_port())],
        cwd=BACKEND, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        hs, lines = _read_handshake(proc)
        assert hs, lines
        status, _ = Api(hs["port"], hs["token"]).post("/api/shutdown")
        assert status == 200
        rc = proc.wait(timeout=20)
        stderr = proc.stderr.read().decode(errors="replace")
        assert rc == 0 and "Fatal Python error" not in stderr, stderr
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
