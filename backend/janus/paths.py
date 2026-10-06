"""Rutas de datos de Janus, por sistema operativo.

Todo lo que Janus escribe en disco (base SQLite, CA, perfiles de navegador,
logs) vive bajo un único directorio de datos, nombrado con el identificador de
la app (el mismo criterio que `app_local_data_dir()` de Tauri):

  Windows  %LOCALAPPDATA%\\io.janus.app
  macOS    ~/Library/Application Support/io.janus.app
  Linux    $XDG_DATA_HOME/io.janus.app  (default ~/.local/share/io.janus.app)

En Windows no puede ser %LOCALAPPDATA%\\Janus: ahí instala el programa el
instalador por usuario. Además, el desinstalador borra esta carpeta si se marca
"borrar datos de la aplicación".

`JANUS_DATA_DIR` lo pisa (útil para tests y para correr varias instancias).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "io.janus.app"  # = identifier de tauri.conf.json


def data_dir() -> Path:
    override = os.environ.get("JANUS_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / APP_ID
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_ID
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        base = Path(xdg) / APP_ID
    base.mkdir(parents=True, exist_ok=True)
    return base


def sub(*parts: str) -> Path:
    """Subdirectorio del directorio de datos (se crea si falta)."""
    p = data_dir().joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "janus.sqlite3"


def confdir() -> Path:
    """confdir de mitmproxy: ahí vive la CA."""
    return sub("ca")


def logs_dir() -> Path:
    return sub("logs")


def browser_profile(name: str) -> Path:
    return sub("browser", name)


def bundle_dir() -> Path:
    """Raíz de recursos: el repo en desarrollo o el bundle de PyInstaller."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    # backend/janus/paths.py -> repo/
    return Path(__file__).resolve().parents[2]


def frontend_dir() -> Path | None:
    candidate = bundle_dir() / "frontend"
    return candidate if (candidate / "index.html").is_file() else None
