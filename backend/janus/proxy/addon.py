"""JanusAddon — hooks de mitmproxy. STUB para M1.

Todavía no está cableado al Master (eso es lo primero de M1). Queda acá el
esqueleto con los hooks y las decisiones ya documentadas en ARCHITECTURE.md §3,
para que cuando se conecte solo haya que rellenar los cuerpos, no rediseñar.

Modelo de intercept: mitmproxy retiene un flow con `flow.intercept()` y lo libera
con `flow.resume()`. El endpoint de la API muta `flow.request`/`flow.response`
con los bytes editados y recién ahí resume.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # evita importar mitmproxy hasta que M1 lo agregue como dep
    from mitmproxy.http import HTTPFlow


class JanusAddon:
    def __init__(self) -> None:
        # En M1: referencias a los servicios (history, scope, intercept, ws hub).
        self.intercept_enabled: bool = False
        self.intercept_scope: str = "request"  # request | response | both

    def request(self, flow: "HTTPFlow") -> None:
        # TODO(M1):
        #   1. evaluar scope (in_scope) — ver scope.py
        #   2. si intercept on y en scope: flow.intercept(); encolar; emitir intercept.pending
        #   3. emitir flow.new + persistir en SQLite
        raise NotImplementedError

    def response(self, flow: "HTTPFlow") -> None:
        # TODO(M1): idem para responses si intercept_scope incluye 'response';
        #           actualizar el flow en SQLite + emitir flow.complete
        raise NotImplementedError
