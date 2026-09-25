"""Autenticación de la API local.

El backend genera un token al arrancar y lo entrega a Tauri por el handshake
de stdout. El frontend lo manda en cada request como `Authorization: Bearer <token>`.
Sin token válido → 401. Esto corta que cualquier web abierta en el navegador
del usuario le pegue a la API local (DNS-rebinding y similares).

En modo dev standalone (env JANUS_DEV=1) el chequeo se saltea, así se puede
abrir el frontend directo sin el shell de Tauri mientras se trabaja la base.
"""
from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, status

# Token de sesión. Se fija una vez por proceso.
API_TOKEN: str = secrets.token_urlsafe(32)

# Modo desarrollo: saltea el chequeo de token para poder abrir el front suelto.
DEV_MODE: bool = os.environ.get("JANUS_DEV") == "1"


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Dependencia de FastAPI que valida el Bearer token."""
    if DEV_MODE:
        return
    expected = f"Bearer {API_TOKEN}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token inválido o ausente",
        )
