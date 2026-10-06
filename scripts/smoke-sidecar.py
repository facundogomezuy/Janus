"""Prueba de humo del motor congelado (src-tauri/binaries/janus-backend-*).

Lo lanza como lo hace Tauri (``--sidecar``, stdin/stdout por pipes), espera el
handshake, consulta la API con el token, pasa un request por el proxy y lo
apaga cerrando stdin. Valida lo que los tests no pueden: que el ejecutable de
PyInstaller sin consola (Windows) arranca y habla por los pipes.

    python scripts/smoke-sidecar.py
"""
from __future__ import annotations

import http.client
import json
import os
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


def main() -> int:
    found = sorted((ROOT / "src-tauri" / "binaries").glob("janus-backend-*"))
    if not found:
        print("no hay sidecar en src-tauri/binaries (correr npm run backend:build)")
        return 1
    exe = found[0]
    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    proxy_port = free_port()

    with tempfile.TemporaryDirectory() as data:
        env = {**os.environ, "JANUS_DATA_DIR": data}
        t0 = time.monotonic()
        proc = subprocess.Popen(
            [str(exe), "--sidecar", "--proxy-port", str(proxy_port)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        box: dict = {}
        reader = threading.Thread(target=lambda: box.setdefault("line", proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout=90)
        line = box.get("line", b"").decode("utf-8", "replace").strip()
        if not line.startswith("JANUS_READY "):
            proc.kill()
            print(f"sin handshake: {line!r}\n{proc.stderr.read().decode('utf-8', 'replace')}")
            return 1
        hs = json.loads(line.split(" ", 1)[1])
        print(f"handshake en {time.monotonic() - t0:.1f}s: api :{hs['port']}, proxy {hs['proxy']}")
        assert hs["proxy"]["running"], "el proxy no está escuchando"

        api = http.client.HTTPConnection("127.0.0.1", hs["port"], timeout=10)
        api.request("GET", "/api/status", headers={"Authorization": f"Bearer {hs['token']}"})
        status = api.getresponse()
        assert status.status == 200, f"/api/status -> {status.status}"
        status.read()

        via = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=15)
        via.request("GET", f"http://127.0.0.1:{target.server_address[1]}/smoke")
        res = via.getresponse()
        assert res.status == 200 and json.loads(res.read()) == {"ok": True}, "el proxy no reenvió el request"

        api.request("GET", "/api/history", headers={"Authorization": f"Bearer {hs['token']}"})
        flows = json.loads(api.getresponse().read())["flows"]
        assert any(f["path"] == "/smoke" for f in flows), "el flow no quedó en el historial"
        print("proxy + historial OK")

        # apagado como Tauri: "shutdown" por stdin
        proc.stdin.write(b"shutdown\n")
        proc.stdin.flush()
        rc = proc.wait(timeout=30)
        assert rc == 0, f"salió con código {rc}: {proc.stderr.read().decode('utf-8', 'replace')}"
        print("apagado ordenado OK")
    target.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
