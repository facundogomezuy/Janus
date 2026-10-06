"""JanusAddon — hooks de mitmproxy (ARCHITECTURE.md §3).

El addon es fino a propósito: traduce eventos de mitmproxy a llamadas al Core,
que es quien sabe de historial, scope, intercept y WebSocket.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mitmproxy import tls
    from mitmproxy.http import HTTPFlow
    from mitmproxy.proxy.server_hooks import ServerConnectionHookData

    from ..core import Core


class JanusAddon:
    def __init__(self, core: Core) -> None:
        self.core = core

    def running(self) -> None:
        self.core.on_proxy_running()

    async def server_connect(self, data: ServerConnectionHookData) -> None:
        await self.core.on_server_connect(data)

    def request(self, flow: HTTPFlow) -> None:
        self.core.on_request(flow)

    def response(self, flow: HTTPFlow) -> None:
        self.core.on_response(flow)

    def error(self, flow: HTTPFlow) -> None:
        self.core.on_error(flow)

    def tls_failed_client(self, data: tls.TlsData) -> None:
        self.core.on_tls_failed(data, side="client")

    def tls_failed_server(self, data: tls.TlsData) -> None:
        self.core.on_tls_failed(data, side="server")
