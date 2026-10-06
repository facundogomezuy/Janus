"""Mensajes HTTP crudos: parseo, armado y vistas de cuerpo.

Lo usan intercept (editar flows retenidos), repeater (envío byte a byte) e
history (detalle de un flow).

Transporte de texto con el frontend: un mensaje viaja como
``{"text": str, "encoding": "utf-8" | "latin-1"}``; los bytes reales son
``text.encode(encoding)``. Si el cuerpo no es UTF-8 válido se usa latin-1, que
es reversible byte a byte.

Fines de línea: el editor del frontend (un textarea) normaliza todo a ``\\n``.
Al volver a bytes, la cabecera siempre se rearma con ``\\r\\n``; el cuerpo se
deja como se escribió, salvo dos casos:
  - si es igual al cuerpo original (modulo CRLF) se reusan los bytes originales,
    así editar un header no altera un body binario o multipart;
  - si es multipart/*, se pasa a CRLF (los boundaries lo exigen).
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from mitmproxy.net import encoding as mitm_encoding

Headers = list[tuple[str, str]]

_BLANK_LINE = re.compile(rb"\r?\n\r?\n")
_LINE_SPLIT = re.compile(rb"\r?\n")
_LONE_LF = re.compile(rb"(?<!\r)\n")

TEXTUAL_HINTS = (
    "json", "xml", "javascript", "ecmascript", "x-www-form-urlencoded", "graphql",
    "csv", "yaml", "html", "svg", "text/", "x-ndjson", "event-stream", "jwt",
)


# --- texto <-> bytes --------------------------------------------------------

def bytes_to_text(data: bytes) -> tuple[str, str]:
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def text_to_bytes(text: str, encoding: str = "utf-8") -> bytes:
    if encoding == "latin-1":
        try:
            return text.encode("latin-1")
        except UnicodeEncodeError:
            pass  # el usuario tipeó algo fuera de latin-1: cae a UTF-8
    return text.encode("utf-8")


def message_payload(data: bytes) -> dict:
    text, enc = bytes_to_text(data)
    return {"text": text, "encoding": enc}


# --- headers ----------------------------------------------------------------

def header_get(headers: Headers, name: str) -> str | None:
    lname = name.lower()
    for k, v in headers:
        if k.lower() == lname:
            return v
    return None


def header_set(headers: Headers, name: str, value: str) -> Headers:
    """Reemplaza la primera aparición (manteniendo posición) y borra el resto."""
    lname = name.lower()
    out: Headers = []
    done = False
    for k, v in headers:
        if k.lower() == lname:
            if not done:
                out.append((k, value))
                done = True
        else:
            out.append((k, v))
    if not done:
        out.append((name, value))
    return out


def header_remove(headers: Headers, name: str) -> Headers:
    lname = name.lower()
    return [(k, v) for k, v in headers if k.lower() != lname]


def headers_from_mitm(fields) -> Headers:
    """``mitmproxy.http.Headers.fields`` (tuplas de bytes) -> pares de str."""
    return [(k.decode("latin-1"), v.decode("latin-1")) for k, v in fields]


def headers_to_mitm(headers: Headers) -> list[tuple[bytes, bytes]]:
    return [(k.encode("latin-1", "replace"), v.encode("latin-1", "replace")) for k, v in headers]


# --- parseo -----------------------------------------------------------------

def split_head_body(data: bytes) -> tuple[bytes, bytes]:
    data = data.lstrip(b"\r\n")
    m = _BLANK_LINE.search(data)
    if not m:
        return data.rstrip(b"\r\n"), b""
    return data[: m.start()], data[m.end():]


def parse_header_lines(lines: list[bytes]) -> Headers:
    headers: Headers = []
    for raw in lines:
        if not raw.strip():
            continue
        line = raw.decode("latin-1")
        if line[:1] in (" ", "\t") and headers:  # obs-fold
            k, v = headers[-1]
            headers[-1] = (k, v + " " + line.strip())
            continue
        name, sep, value = line.partition(":")
        if not sep:
            headers.append((line.strip(), ""))
        else:
            headers.append((name.strip(), value.strip()))
    return headers


@dataclass
class RequestParts:
    method: str
    target: str
    http_version: str
    headers: Headers = field(default_factory=list)
    body: bytes = b""


@dataclass
class ResponseParts:
    http_version: str
    status_code: int
    reason: str
    headers: Headers = field(default_factory=list)
    body: bytes = b""


class MessageError(ValueError):
    pass


def parse_request(data: bytes) -> RequestParts:
    head, body = split_head_body(data)
    lines = _LINE_SPLIT.split(head)
    first = lines[0].decode("latin-1").strip()
    parts = first.split()
    if len(parts) < 2:
        raise MessageError(f"línea de request inválida: {first!r}")
    method, target = parts[0], parts[1]
    version = parts[2] if len(parts) > 2 else "HTTP/1.1"
    return RequestParts(method, target, version, parse_header_lines(lines[1:]), body)


def parse_response(data: bytes) -> ResponseParts:
    head, body = split_head_body(data)
    lines = _LINE_SPLIT.split(head)
    first = lines[0].decode("latin-1").strip()
    parts = first.split(None, 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise MessageError(f"línea de estado inválida: {first!r}")
    reason = parts[2] if len(parts) > 2 else ""
    return ResponseParts(parts[0], int(parts[1]), reason, parse_header_lines(lines[1:]), body)


# --- armado -----------------------------------------------------------------

def assemble_head(first_line: str, headers: Headers) -> bytes:
    lines = [first_line] + [f"{k}: {v}" for k, v in headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace")


def assemble_request(method: str, target: str, version: str, headers: Headers, body: bytes) -> bytes:
    return assemble_head(f"{method} {target} {version}", headers) + (body or b"")


def assemble_response(version: str, status: int, reason: str, headers: Headers, body: bytes) -> bytes:
    first = f"{version} {status} {reason}".rstrip()
    return assemble_head(first, headers) + (body or b"")


def is_multipart(headers: Headers) -> bool:
    return (header_get(headers, "content-type") or "").lower().startswith("multipart/")


def restore_body(edited: bytes, original: bytes | None, headers: Headers) -> bytes:
    """Ver docstring del módulo: reusa los bytes originales o corrige CRLF."""
    if original is not None and edited == original.replace(b"\r\n", b"\n"):
        return original
    if is_multipart(headers):
        return _LONE_LF.sub(b"\r\n", edited)
    return edited


def prepare_request_for_wire(
    data: bytes,
    *,
    fix_content_length: bool = True,
    original_body: bytes | None = None,
) -> bytes:
    """Texto del editor -> bytes para el socket, tocando lo mínimo.

    Las líneas de header se mandan tal cual se escribieron (sin reformatear
    espacios ni mayúsculas); solo se reescribe Content-Length si se pidió.
    """
    head, body = split_head_body(data)
    lines = [ln for ln in _LINE_SPLIT.split(head)]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not lines[0].strip():
        raise MessageError("el request está vacío")
    headers = parse_header_lines(lines[1:])
    body = restore_body(body, original_body, headers)

    if fix_content_length and header_get(headers, "transfer-encoding") is None:
        cl_index = None
        for i, ln in enumerate(lines[1:], start=1):
            if ln.split(b":", 1)[0].strip().lower() == b"content-length":
                cl_index = i
                break
        if cl_index is not None:
            name = lines[cl_index].split(b":", 1)[0]
            lines[cl_index] = name + b": " + str(len(body)).encode()
        elif body:
            lines.append(b"Content-Length: " + str(len(body)).encode())

    return b"\r\n".join(lines) + b"\r\n\r\n" + body


# --- cuerpos ----------------------------------------------------------------

def decode_body(raw: bytes, headers: Headers) -> tuple[bytes | None, str | None]:
    """Deshace Content-Encoding (gzip, br, zstd, deflate, en cadena)."""
    ce = (header_get(headers, "content-encoding") or "").strip().lower()
    if not raw or not ce or ce == "identity":
        return raw, None
    data = raw
    try:
        for coding in reversed([c.strip() for c in ce.split(",") if c.strip()]):
            if coding == "identity":
                continue
            data = mitm_encoding.decode(data, coding)
        return bytes(data), None
    except Exception as exc:  # noqa: BLE001 - cualquier falla = no decodificable
        return None, f"no se pudo decodificar {ce}: {exc}"


def charset_of(content_type: str | None) -> str | None:
    if not content_type:
        return None
    m = re.search(r"charset\s*=\s*\"?([\w.:-]+)", content_type, re.I)
    return m.group(1) if m else None


def is_textual(content_type: str | None, data: bytes) -> bool:
    ct = (content_type or "").lower()
    if ct and any(h in ct for h in TEXTUAL_HINTS):
        return True
    base = ct.split(";")[0].strip()
    if base == "application/octet-stream" or base.startswith(("image/", "audio/", "video/", "font/")):
        return False
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        # un corte al final de la muestra no cuenta como binario
        return exc.start >= len(sample) - 4


def body_text(data: bytes, content_type: str | None) -> str:
    charset = charset_of(content_type)
    if charset:
        try:
            return data.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


PREVIEW_LIMIT = 4 * 1024 * 1024


def body_view(raw: bytes | None, headers: Headers) -> dict:
    """Todo lo que el visor necesita de un cuerpo: crudo (b64) + texto legible."""
    raw = raw or b""
    content_type = header_get(headers, "content-type")
    decoded, decode_error = decode_body(raw, headers)
    view: dict = {
        "size": len(raw),
        "b64": base64.b64encode(raw).decode("ascii"),
        "content_type": content_type,
        "content_encoding": header_get(headers, "content-encoding"),
        "decode_error": decode_error,
        "decoded_size": len(decoded) if decoded is not None else None,
        "text": None,
        "truncated": False,
    }
    source = decoded if decoded is not None else raw
    if source and is_textual(content_type, source):
        if len(source) > PREVIEW_LIMIT:
            view["truncated"] = True
            source = source[:PREVIEW_LIMIT]
        view["text"] = body_text(source, content_type)
    return view


def editable_message(head_first_line: str, headers: Headers, raw_body: bytes) -> tuple[dict, bool]:
    """Mensaje listo para el editor (intercept): cabecera + cuerpo legible.

    Si el cuerpo tiene Content-Encoding decodificable se muestra decodificado.
    Devuelve (payload, body_decodificado).
    """
    decoded, _ = decode_body(raw_body, headers)
    shown_decoded = decoded is not None and decoded != raw_body
    body = decoded if shown_decoded else raw_body
    data = assemble_head(head_first_line, headers) + (body or b"")
    return message_payload(data), shown_decoded
