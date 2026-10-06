"""Ajustes persistidos (tabla ``settings``, key/value JSON)."""
from __future__ import annotations

import ipaddress
import json
import re
from typing import Any

DEFAULTS: dict[str, Any] = {
    # proxy
    "proxy.listen_host": "127.0.0.1",
    "proxy.listen_port": 8080,
    "proxy.upstream": "",  # ej. http://10.0.0.1:3128
    "proxy.ssl_insecure": True,  # no verificar certificados de los targets
    "proxy.http2": True,
    "proxy.stream_large_bodies": "8m",
    # intercept (el on/off no se persiste: siempre arranca apagado)
    "intercept.requests": True,
    "intercept.responses": False,
    "intercept.only_in_scope": True,
    "intercept.filter": "",
    # history
    "history.hide_static": False,
}

_SIZE_RE = re.compile(r"^\d+(?:\.\d+)?\s*[kmg]?b?$", re.I)
_UPSTREAM_RE = re.compile(r"^(https?)://[^\s/:]+(?::\d{1,5})?/?$", re.I)


class SettingsError(ValueError):
    pass


def _validate(key: str, value: Any) -> Any:
    if key not in DEFAULTS:
        raise SettingsError(f"ajuste desconocido: {key}")
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingsError(f"{key} debe ser booleano")
        return value
    if isinstance(default, int):
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{key} debe ser un número") from exc
        if key == "proxy.listen_port" and not 1 <= value <= 65535:
            raise SettingsError("el puerto debe estar entre 1 y 65535")
        return value
    value = "" if value is None else str(value).strip()
    if key == "proxy.listen_host":
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise SettingsError("la dirección de escucha debe ser una IP (127.0.0.1 o 0.0.0.0)") from exc
    elif key == "proxy.upstream" and value and not _UPSTREAM_RE.match(value):
        raise SettingsError("proxy upstream inválido; formato: http://host:puerto")
    elif key == "proxy.stream_large_bodies" and value and not _SIZE_RE.match(value):
        raise SettingsError("tamaño inválido; ej. 8m, 512k")
    return value


class Settings:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = dict(DEFAULTS)
        for k, v in values.items():
            if k in DEFAULTS:
                try:
                    self._values[k] = _validate(k, v)
                except SettingsError:
                    pass  # valor viejo inválido: queda el default

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def validate_update(self, changes: dict[str, Any]) -> dict[str, Any]:
        return {k: _validate(k, v) for k, v in changes.items()}

    def apply(self, changes: dict[str, Any]) -> None:
        self._values.update(changes)


def load(conn) -> Settings:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    values = {}
    for r in rows:
        try:
            values[r["key"]] = json.loads(r["value"])
        except json.JSONDecodeError:
            continue
    return Settings(values)


def save(conn, changes: dict[str, Any]) -> None:
    for k, v in changes.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (k, json.dumps(v)),
        )
