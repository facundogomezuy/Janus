"""Prueba de humo del motor congelado (src-tauri/binaries/janus-backend-*).

Lo lanza como lo hace Tauri (``--sidecar``, stdin/stdout por pipes), espera el
handshake, consulta la API con el token, pasa un request por el proxy y lo
apaga cerrando stdin. Valida lo que los tests no pueden: que el ejecutable de
PyInstaller sin consola (Windows) arranca y habla por los pipes.

    python scripts/smoke-sidecar.py
"""
from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Target(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def host_triple() -> str | None:
    """Mismo criterio que scripts/build-backend.mjs: $TAURI_TARGET_TRIPLE o el host de rustc."""
    if os.environ.get("TAURI_TARGET_TRIPLE"):
        return os.environ["TAURI_TARGET_TRIPLE"]
    try:
        out = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    m = re.search(r"^host: (\S+)$", out, re.M)
    return m.group(1) if m else None


def find_sidecar() -> Path | None:
    """El binario de esta plataforma, no el primero que aparezca (WSL + Windows, otra arquitectura)."""
    bins = ROOT / "src-tauri" / "binaries"
    ext = ".exe" if sys.platform == "win32" else ""
    triple = host_triple()
    if triple:
        exe = bins / f"janus-backend-{triple}{ext}"
        return exe if exe.is_file() else None
    found = [p for p in bins.glob("janus-backend-*") if p.suffix == ext]
    return found[0] if len(found) == 1 else None


def main() -> int:
    exe = find_sidecar()
    if exe is None:
        have = ", ".join(p.name for p in (ROOT / "src-tauri" / "binaries").glob("janus-backend-*")) or "ninguno"
        print(f"no hay sidecar para esta plataforma en src-tauri/binaries (hay: {have}); correr npm run backend:build")
        return 1
    print(f"probando {exe.name}")
    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    proxy_port = free_port()

    # ignore_cleanup_errors: en Windows un motor que quedó vivo bloquea janus.log
    # y el PermissionError del borrado taparía el error real.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as data:
        env = {**os.environ, "JANUS_DATA_DIR": data}
        t0 = time.monotonic()
        proc = subprocess.Popen(
            [str(exe), "--sidecar", "--proxy-port", str(proxy_port)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        stderr: list[bytes] = []  # drenado en un hilo: un pipe lleno frenaría al motor
        threading.Thread(target=lambda: stderr.extend(iter(proc.stderr.readline, b"")), daemon=True).start()
        try:
            check(proc, t0, proxy_port, target.server_address[1])
        except Exception:
            print("--- stderr del motor ---\n" + b"".join(stderr).decode("utf-8", "replace"))
            raise
        finally:
            if proc.poll() is None:
                # EOF en stdin: el motor se apaga solo (kill() solo mataría al cargador de PyInstaller)
                with contextlib.suppress(OSError):
                    proc.stdin.close()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
    target.shutdown()
    return 0


def check(proc: subprocess.Popen, t0: float, proxy_port: int, target_port: int) -> None:
    box: dict = {}
    reader = threading.Thread(target=lambda: box.setdefault("line", proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(timeout=90)
    line = box.get("line", b"").decode("utf-8", "replace").strip()
    assert line.startswith("JANUS_READY "), f"sin handshake: {line!r}"
    hs = json.loads(line.split(" ", 1)[1])
    print(f"handshake en {time.monotonic() - t0:.1f}s: api :{hs['port']}, proxy {hs['proxy']}")
    assert hs["proxy"]["running"], "el proxy no está escuchando"
    auth = {"Authorization": f"Bearer {hs['token']}"}

    def get(path: str):
        api = http.client.HTTPConnection("127.0.0.1", hs["port"], timeout=10)
        api.request("GET", path, headers=auth)
        r = api.getresponse()
        body = r.read()
        api.close()
        return r.status, body

    status, _ = get("/api/status")
    assert status == 200, f"/api/status -> {status}"

    via = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=15)
    via.request("GET", f"http://127.0.0.1:{target_port}/smoke")
    res = via.getresponse()
    assert res.status == 200 and json.loads(res.read()) == {"ok": True}, "el proxy no reenvió el request"

    # el historial se escribe en SQLite desde otro hilo: esperar a que aparezca
    deadline = time.monotonic() + 10
    while True:
        flows = json.loads(get("/api/history")[1])["flows"]
        if any(f["path"] == "/smoke" and f["state"] == "complete" for f in flows):
            break
        assert time.monotonic() < deadline, f"el flow no quedó en el historial: {flows}"
        time.sleep(0.1)
    print("proxy + historial OK")

    # apagado como Tauri: "shutdown" por stdin
    proc.stdin.write(b"shutdown\n")
    proc.stdin.flush()
    rc = proc.wait(timeout=30)
    assert rc == 0, f"el motor salió con código {rc}"
    print("apagado ordenado OK")

if __name__ == "__main__":
    sys.exit(main())
