"""Entrypoint del sidecar.

Flujo de arranque (ARCHITECTURE.md §1, §8):
  1. Elegir un puerto alto libre en 127.0.0.1.
  2. Imprimir por stdout una línea de handshake que Tauri parsea:
         JANUS_READY {"port": <int>, "token": "<str>"}
     Con eso el shell nativo sabe a dónde apuntar la webview y qué token usar.
  3. Levantar uvicorn (FastAPI) en ese puerto, solo en loopback.

Uso standalone (sin Tauri), para trastear la base:
    JANUS_DEV=1 python -m janus.main
  Saltea el token y sirve el frontend en http://127.0.0.1:<port>/
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import uvicorn

from . import auth
from .server import create_app

HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _frontend_dir() -> Path | None:
    # backend/janus/main.py -> repo/frontend
    candidate = Path(__file__).resolve().parents[2] / "frontend"
    return candidate if candidate.is_dir() else None


def main() -> None:
    port = _free_port()

    # Handshake: única línea que Tauri lee de stdout. Debe ir antes del server.
    handshake = {"port": port, "token": auth.API_TOKEN, "dev": auth.DEV_MODE}
    print("JANUS_READY " + json.dumps(handshake), flush=True)

    app = create_app(frontend_dir=_frontend_dir())
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
