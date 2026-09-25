"""App FastAPI: la superficie de control que consume el frontend.

En M0 solo están /api/status y /api/history (con data falsa). El resto de la
API (intercept, repeater, scope, ca) queda esbozada en ARCHITECTURE.md §5 y se
va sumando por milestone. En dev, además, sirve el frontend estático para poder
abrir la app sin el shell de Tauri.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__, auth
from .fakes import FAKE_FLOWS

# Estado del proxy en memoria. Placeholder hasta que entre el motor mitmproxy (M1).
STATE = {
    "proxy_running": True,
    "proxy_port": 8080,
    "intercept_enabled": False,
    "intercept_scope": "request",
    "ca_path": None,
}


def create_app(frontend_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Janus", version=__version__)

    # El origin real de la webview de Tauri se ajusta en el empaquetado.
    # En dev abrimos para poder servir/consumir desde localhost.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if auth.DEV_MODE else ["tauri://localhost", "https://tauri.localhost"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/status")
    def status(_: None = Depends(auth.require_token)) -> dict:
        return {
            "app": "janus",
            "version": __version__,
            "proxy_running": STATE["proxy_running"],
            "proxy_port": STATE["proxy_port"],
            "intercept_enabled": STATE["intercept_enabled"],
            "intercept_scope": STATE["intercept_scope"],
            "flows": len(FAKE_FLOWS),
        }

    @app.get("/api/history")
    def history(
        scope_only: bool = False,
        q: str | None = None,
        _: None = Depends(auth.require_token),
    ) -> dict:
        rows = FAKE_FLOWS
        if scope_only:
            rows = [f for f in rows if f["in_scope"]]
        if q:
            needle = q.lower()
            rows = [
                f for f in rows
                if needle in f["host"].lower() or needle in f["path"].lower()
            ]
        # El listado no manda los cuerpos completos; van en el detalle.
        summary = [
            {k: f[k] for k in (
                "id", "ts", "source", "method", "scheme", "host", "port",
                "path", "status_code", "reason", "length", "duration_ms", "in_scope",
            )}
            for f in rows
        ]
        return {"flows": summary, "total": len(summary)}

    @app.get("/api/history/{flow_id}")
    def history_detail(flow_id: str, _: None = Depends(auth.require_token)) -> dict:
        for f in FAKE_FLOWS:
            if f["id"] == flow_id:
                return f
        raise HTTPException(status_code=404, detail="flow no encontrado")

    # --- frontend estático (solo dev / standalone) ---
    if frontend_dir and frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app
