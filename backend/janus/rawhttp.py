"""Cliente HTTP crudo para el Repeater (ARCHITECTURE.md §7).

No usa httpx/requests a propósito: esos clientes normalizan el request y
mandar requests malformados es justamente la gracia del repeater. Acá se abre
un socket (con TLS si corresponde), se escriben los bytes tal cual y se lee la
respuesta respetando el framing (chunked, Content-Length o cierre).

TLS: no se verifica el certificado del servidor (herramienta de pentesting) y
se aceptan configuraciones viejas para poder hablar con targets legacy.
"""
from __future__ import annotations

import asyncio
import contextlib
import ssl
import time
from dataclasses import dataclass, field

from . import loopback
from .httpmsg import Headers, header_get, parse_header_lines

MAX_BODY = 64 * 1024 * 1024
HEAD_LIMIT = 1024 * 1024
_HEAD_END = (b"\r\n\r\n", b"\n\n")


@dataclass
class RawResponse:
    http_version: str = ""
    status_code: int = 0
    reason: str = ""
    headers: Headers = field(default_factory=list)
    body: bytes = b""  # sin framing; todavía con Content-Encoding
    complete: bool = False


@dataclass
class RawResult:
    ok: bool
    error: str | None = None
    raw: bytes = b""  # bytes tal cual llegaron
    response: RawResponse | None = None
    elapsed_ms: int = 0
    connect_ms: int = 0
    bytes_sent: int = 0
    tls_version: str | None = None
    cipher: str | None = None
    peer: str | None = None


def _tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_alpn_protocols(["http/1.1"])
    with contextlib.suppress(ssl.SSLError, ValueError):
        ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
    with contextlib.suppress(ssl.SSLError):
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
    return ctx


def _request_method(data: bytes) -> str:
    return data.lstrip(b"\r\n").split(b" ", 1)[0].decode("latin-1", "replace").upper()


class _Reader:
    """Acumula todo lo leído para poder devolver parciales ante un timeout."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self.reader = reader
        self.raw = bytearray()

    async def until(self, sep) -> bytes:
        try:
            chunk = await self.reader.readuntil(sep)
        except asyncio.IncompleteReadError as exc:
            self.raw += exc.partial
            raise
        self.raw += chunk
        return chunk

    async def exactly(self, n: int) -> bytes:
        try:
            chunk = await self.reader.readexactly(n)
        except asyncio.IncompleteReadError as exc:
            self.raw += exc.partial
            raise
        self.raw += chunk
        return chunk

    async def rest(self, limit: int) -> bytes:
        out = bytearray()
        while len(out) < limit:
            chunk = await self.reader.read(min(65536, limit - len(out)))
            if not chunk:
                break
            out += chunk
            self.raw += chunk
        return bytes(out)


async def _read_response(r: _Reader, method: str, resp: RawResponse) -> None:
    while True:
        head = await r.until(_HEAD_END)
        lines = head.replace(b"\r\n", b"\n").split(b"\n")
        status_line = lines[0].decode("latin-1").strip()
        parts = status_line.split(None, 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise ValueError(f"respuesta no es HTTP: {status_line[:80]!r}")
        resp.http_version = parts[0]
        resp.status_code = int(parts[1])
        resp.reason = parts[2] if len(parts) > 2 else ""
        resp.headers = parse_header_lines(lines[1:])
        # 1xx informativos (100 Continue, 103 Early Hints): seguir leyendo
        if 100 <= resp.status_code < 200 and resp.status_code != 101:
            continue
        break

    status = resp.status_code
    if method == "HEAD" or status in (204, 304) or 100 <= status < 200:
        resp.complete = True
        return

    te = (header_get(resp.headers, "transfer-encoding") or "").lower()
    if "chunked" in te:
        body = bytearray()
        while True:
            size_line = await r.until(b"\n")
            size_txt = size_line.split(b";", 1)[0].strip()
            size = int(size_txt or b"0", 16)
            if size == 0:
                while True:  # trailers
                    trailer = await r.until(b"\n")
                    if trailer.strip() == b"":
                        break
                break
            body += await r.exactly(size)
            resp.body = bytes(body)
            await r.until(b"\n")
            if len(body) > MAX_BODY:
                raise ValueError("respuesta demasiado grande")
        resp.body = bytes(body)
        resp.complete = True
        return

    cl = header_get(resp.headers, "content-length")
    if cl is not None and cl.strip().isdigit():
        n = int(cl.strip())
        if n > MAX_BODY:
            raise ValueError("respuesta demasiado grande")
        resp.body = await r.exactly(n)
        resp.complete = True
        return

    # sin framing: hasta que el servidor cierre
    resp.body = await r.rest(MAX_BODY)
    resp.complete = True


async def send(
    host: str,
    port: int,
    tls: bool,
    data: bytes,
    *,
    sni: str | None = None,
    timeout: float = 30.0,
) -> RawResult:
    t0 = time.perf_counter()
    deadline = t0 + timeout

    def remaining() -> float:
        return max(0.05, deadline - time.perf_counter())

    def ms(since: float) -> int:
        return int((time.perf_counter() - since) * 1000)

    host = host.strip().strip("[]")
    connect_host = await loopback.pick(port) if loopback.is_localhost(host) else host
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                connect_host,
                port,
                ssl=_tls_context() if tls else None,
                server_hostname=(sni or host) if tls else None,
                limit=HEAD_LIMIT,
                happy_eyeballs_delay=0.25,  # IPv6 roto no debe costar 20 s
            ),
            timeout=remaining(),
        )
    except TimeoutError:
        return RawResult(False, f"timeout conectando a {host}:{port}", elapsed_ms=ms(t0))
    except ssl.SSLError as exc:
        return RawResult(False, f"error TLS: {exc.reason or exc}", elapsed_ms=ms(t0))
    except OSError as exc:
        return RawResult(False, f"no se pudo conectar a {host}:{port}: {exc.strerror or exc}", elapsed_ms=ms(t0))

    result = RawResult(True, connect_ms=ms(t0), bytes_sent=len(data))
    peer = writer.get_extra_info("peername")
    if peer:
        result.peer = f"{peer[0]}:{peer[1]}"
    ssl_obj = writer.get_extra_info("ssl_object")
    if ssl_obj is not None:
        result.tls_version = ssl_obj.version()
        cipher = ssl_obj.cipher()
        result.cipher = cipher[0] if cipher else None

    r = _Reader(reader)
    resp = RawResponse()
    try:
        writer.write(data)
        await asyncio.wait_for(writer.drain(), timeout=remaining())
        await asyncio.wait_for(_read_response(r, _request_method(data), resp), timeout=remaining())
    except TimeoutError:
        if resp.status_code:
            result.error = "timeout: la respuesta quedó incompleta"
        else:
            result.ok = False
            result.error = f"timeout esperando respuesta ({timeout:g}s)"
    except asyncio.IncompleteReadError:
        if resp.status_code:
            result.error = "el servidor cerró la conexión antes de terminar la respuesta"
        elif r.raw and not bytes(r.raw[:5]).upper().startswith(b"HTTP/"):
            result.ok = False
            result.error = f"la respuesta no es HTTP ({len(r.raw)} bytes crudos)"
        else:
            result.ok = False
            result.error = "el servidor cerró la conexión sin responder"
    except asyncio.LimitOverrunError:
        result.ok = False
        result.error = "cabecera de respuesta demasiado grande"
    except (ValueError, ssl.SSLError, OSError) as exc:
        result.ok = bool(resp.status_code)
        result.error = str(exc)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), timeout=1)

    result.raw = bytes(r.raw)
    result.response = resp if resp.status_code else None
    result.elapsed_ms = ms(t0)
    return result
