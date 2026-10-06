"""Autenticación de la API local (ARCHITECTURE.md §8).

El backend genera un token al arrancar y lo entrega a Tauri por el handshake
de stdout. El frontend lo manda en cada request como ``Authorization: Bearer``
(o ``?token=`` en descargas y en el WebSocket, que no pueden llevar headers).
Sin token válido -> 401. Esto corta que cualquier web abierta en el navegador
del usuario le pegue a la API local (DNS-rebinding y similares).

``JANUS_DEV=1`` saltea el chequeo (solo para trabajar la UI suelta).
"""
from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, Query, status

API_TOKEN: str = secrets.token_urlsafe(32)

DEV_MODE: bool = os.environ.get("JANUS_DEV") == "1"


def valid(token: str | None) -> bool:
    if DEV_MODE:
        return True
    return bool(token) and secrets.compare_digest(token, API_TOKEN)  # type: ignore[arg-type]


def require_token(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Dependencia de FastAPI que valida el token."""
    supplied = None
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization[7:]
    elif token:
        supplied = token
    if not valid(supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token inválido o ausente",
        )
