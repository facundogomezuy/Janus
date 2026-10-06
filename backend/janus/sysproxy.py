"""Proxy del sistema en Windows (WinINet), opcional.

Con esto activado, Edge, Chrome (perfil normal) y la mayoría de las apps de
Windows mandan su tráfico por Janus. Antes de tocar nada se guarda la
configuración previa en ``sysproxy-backup.json``; al desactivar (o al cerrar
Janus) se restaura. Si Janus muere de golpe, el backup queda en disco y se
restaura en el próximo arranque (``recover``).
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

from . import paths

log = logging.getLogger("janus.sysproxy")

KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
VALUES = ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL")

INTERNET_OPTION_REFRESH = 37
INTERNET_OPTION_SETTINGS_CHANGED = 39


def supported() -> bool:
    return sys.platform == "win32"


def _backup_path():
    return paths.data_dir() / "sysproxy-backup.json"


def _read() -> dict[str, Any]:
    import winreg

    out: dict[str, Any] = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
        for name in VALUES:
            try:
                out[name] = winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                out[name] = None
    return out


def _write(state: dict[str, Any]) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
        for name in VALUES:
            value = state.get(name)
            if value is None:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            elif name == "ProxyEnable":
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
            else:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))


def _notify() -> None:
    import ctypes

    wininet = ctypes.WinDLL("wininet.dll")
    wininet.InternetSetOptionW(None, INTERNET_OPTION_SETTINGS_CHANGED, None, 0)
    wininet.InternetSetOptionW(None, INTERNET_OPTION_REFRESH, None, 0)


def _address(host: str, port: int) -> str:
    target = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    return f"{target}:{port}"


def status(host: str, port: int) -> dict[str, Any]:
    if not supported():
        return {"supported": False, "enabled": False}
    try:
        cur = _read()
    except OSError:
        return {"supported": True, "enabled": False, "error": "no se pudo leer la configuración"}
    ours = bool(cur.get("ProxyEnable")) and cur.get("ProxyServer") == _address(host, port)
    return {
        "supported": True,
        "enabled": ours,
        "current": cur.get("ProxyServer") if cur.get("ProxyEnable") else None,
        "pac": cur.get("AutoConfigURL"),
    }


def enable(host: str, port: int) -> None:
    if not supported():
        raise RuntimeError("el proxy del sistema solo está soportado en Windows")
    cur = _read()
    backup = _backup_path()
    if not backup.exists():
        backup.write_text(json.dumps(cur), encoding="utf-8")
    new = dict(cur)
    new["ProxyEnable"] = 1
    new["ProxyServer"] = _address(host, port)
    new["AutoConfigURL"] = None  # un PAC tiene prioridad sobre el proxy manual
    if not new.get("ProxyOverride"):
        new["ProxyOverride"] = "<local>"
    _write(new)
    _notify()
    log.info("proxy del sistema -> %s", new["ProxyServer"])


def disable() -> None:
    if not supported():
        return
    backup = _backup_path()
    if backup.exists():
        try:
            prev = json.loads(backup.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {**_read(), "ProxyEnable": 0}
        _write(prev)
        backup.unlink(missing_ok=True)
    else:
        cur = _read()
        cur["ProxyEnable"] = 0
        _write(cur)
    _notify()
    log.info("proxy del sistema restaurado")


def recover() -> bool:
    """Restaura un backup huérfano (Janus se cerró sin restaurar)."""
    if supported() and _backup_path().exists():
        log.warning("restaurando proxy del sistema de una sesión anterior")
        try:
            disable()
            return True
        except OSError:
            log.exception("no se pudo restaurar el proxy del sistema")
    return False
