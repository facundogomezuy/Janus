"""API de control (FastAPI): REST + WebSocket para el frontend (ARCHITECTURE.md §5).

Todo bajo /api con token. En modo standalone además sirve el frontend estático.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, auth, browsers, ca, history, httpmsg, paths, repeater
from .core import Core
from .intercept import InterceptError
from .scope import PatternError
from .settings import SettingsError

log = logging.getLogger("janus.server")

# Windows lee los MIME types del registro y a veces .js figura como text/plain:
# los <script type="module"> se niegan a cargar. Los fijamos a mano.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

TAURI_ORIGINS = ["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"]


# --- modelos ------------------------------------------------------------------

class Message(BaseModel):
    text: str
    encoding: Literal["utf-8", "latin-1"] = "utf-8"


class InterceptUpdate(BaseModel):
    enabled: bool | None = None
    requests: bool | None = None
    responses: bool | None = None
    only_in_scope: bool | None = None
    filter: str | None = None


class ForwardBody(BaseModel):
    message: Message | None = None
    intercept_response: bool | None = None


class ScopeRuleIn(BaseModel):
    kind: Literal["include", "exclude"]
    matcher: str = Field(min_length=1, max_length=500)
    enabled: bool = True


class ScopePut(BaseModel):
    rules: list[ScopeRuleIn]


class ScopeTest(BaseModel):
    url: str


class FlowMeta(BaseModel):
    color: str | None = None
    comment: str | None = Field(default=None, max_length=2000)


class FlowIds(BaseModel):
    ids: list[str]


class TabCreate(BaseModel):
    from_flow: str | None = None
    name: str | None = None
    host: str = "example.com"
    port: int = Field(default=443, ge=1, le=65535)
    tls: bool = True
    message: Message | None = None


class TabUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    tls: bool | None = None
    message: Message | None = None


class SendBody(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    tls: bool
    message: Message
    fix_content_length: bool = True
    timeout: float = Field(default=30.0, ge=1, le=300)


class SysProxyPut(BaseModel):
    enabled: bool


class BrowserLaunch(BaseModel):
    id: str | None = None
    url: str | None = None


class OpenTarget(BaseModel):
    target: Literal["data", "ca", "logs"]


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - abre el Explorador en esa carpeta
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607


# --- app ------------------------------------------------------------------------

def create_app(core: Core, *, frontend_dir: Path | None = None, on_shutdown=None) -> FastAPI:
    app = FastAPI(title="Janus", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if auth.DEV_MODE else TAURI_ORIGINS,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )
    guard = [Depends(auth.require_token)]

    # --- estado -----------------------------------------------------------------

    @app.get("/api/status", dependencies=guard)
    def status() -> dict:
        return {"version": __version__, **core.status()}

    @app.get("/api/events", dependencies=guard)
    def events() -> dict:
        return {"events": core.events.recent()}

    @app.delete("/api/events", dependencies=guard)
    def clear_events() -> dict:
        core.events.clear()
        return {"ok": True}

    @app.post("/api/shutdown", dependencies=guard)
    def shutdown() -> dict:
        if on_shutdown:
            asyncio.get_running_loop().call_soon(on_shutdown)
        return {"ok": True}

    # --- ajustes ------------------------------------------------------------------

    @app.get("/api/settings", dependencies=guard)
    def get_settings() -> dict:
        return core.settings.as_dict()

    @app.put("/api/settings", dependencies=guard)
    async def put_settings(changes: dict[str, Any]) -> dict:
        try:
            return await core.update_settings(changes)
        except SettingsError as exc:
            raise _bad_request(exc) from exc

    # --- history ------------------------------------------------------------------

    @app.get("/api/history", dependencies=guard)
    async def list_history(limit: int = Query(default=5000, ge=1, le=50000),
                           before_seq: int | None = None) -> dict:
        flows = await core.db.read(history.list_summaries, limit, before_seq)
        return {"flows": flows, "total": core.flow_count}

    @app.get("/api/history/search", dependencies=guard)
    async def search_history(q: str = Query(min_length=1, max_length=500)) -> dict:
        return {"ids": await core.db.read(history.search, q)}

    @app.get("/api/history/{flow_id}", dependencies=guard)
    async def history_detail(flow_id: str) -> dict:
        def load(conn):
            row = history.get_row(conn, flow_id)
            return history.detail(row) if row else None

        out = await core.db.read(load)
        if out is None:
            raise HTTPException(status_code=404, detail="flow no encontrado")
        return out

    @app.get("/api/history/{flow_id}/curl", dependencies=guard, response_class=PlainTextResponse)
    async def history_curl(flow_id: str) -> str:
        row = await core.db.read(history.get_row, flow_id)
        if row is None:
            raise HTTPException(status_code=404, detail="flow no encontrado")
        return history.as_curl(row)

    @app.patch("/api/history/{flow_id}", dependencies=guard)
    async def history_meta(flow_id: str, body: FlowMeta) -> dict:
        changes = body.model_dump(exclude_unset=True)
        if "color" in changes and changes["color"] not in (None, *history.COLORS):
            raise HTTPException(status_code=400, detail="color inválido")
        row = await core.db.write(history.set_meta, flow_id, changes)
        if row is None:
            raise HTTPException(status_code=404, detail="flow no encontrado")
        summary = history.summary(row)
        core.hub.emit("flow.update", summary)
        return summary

    @app.delete("/api/history", dependencies=guard)
    async def clear_history() -> dict:
        await core.clear_history()
        return {"ok": True}

    @app.post("/api/history/delete", dependencies=guard)
    async def delete_flows(body: FlowIds) -> dict:
        await core.delete_flows(body.ids)
        return {"ok": True}

    # --- intercept ----------------------------------------------------------------

    @app.get("/api/intercept", dependencies=guard)
    def get_intercept() -> dict:
        return {**core.interceptor.state(), "queue": core.interceptor.items()}

    @app.put("/api/intercept", dependencies=guard)
    async def put_intercept(body: InterceptUpdate) -> dict:
        try:
            return await core.configure_intercept(body.model_dump(exclude_none=True))
        except InterceptError as exc:
            raise _bad_request(exc) from exc

    @app.post("/api/intercept/forward-all", dependencies=guard)
    def forward_all() -> dict:
        core.interceptor.forward_all()
        return core.interceptor.state()

    @app.post("/api/intercept/drop-all", dependencies=guard)
    def drop_all() -> dict:
        core.interceptor.drop_all()
        return core.interceptor.state()

    @app.post("/api/intercept/{flow_id}/forward", dependencies=guard)
    def forward(flow_id: str, body: ForwardBody | None = None) -> dict:
        body = body or ForwardBody()
        try:
            core.interceptor.forward(
                flow_id,
                message=body.message.model_dump() if body.message else None,
                intercept_response=body.intercept_response,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ese flow ya no está retenido") from exc
        except InterceptError as exc:
            raise _bad_request(exc) from exc
        return {"ok": True}

    @app.post("/api/intercept/{flow_id}/drop", dependencies=guard)
    def drop(flow_id: str) -> dict:
        try:
            core.interceptor.drop(flow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ese flow ya no está retenido") from exc
        return {"ok": True}

    # --- scope ----------------------------------------------------------------------

    @app.get("/api/scope", dependencies=guard)
    def get_scope() -> dict:
        return {"rules": [r.to_json() for r in core.scope.rules]}

    @app.put("/api/scope", dependencies=guard)
    async def put_scope(body: ScopePut) -> dict:
        try:
            rules = await core.set_scope([r.model_dump() for r in body.rules])
        except PatternError as exc:
            raise _bad_request(exc) from exc
        return {"rules": rules}

    @app.post("/api/scope/test", dependencies=guard)
    def test_scope(body: ScopeTest) -> dict:
        from urllib.parse import urlsplit

        raw = body.url.strip()
        if "://" not in raw:
            raw = "https://" + raw
        parts = urlsplit(raw)
        if not parts.hostname:
            raise HTTPException(status_code=400, detail="URL inválida")
        scheme = parts.scheme.lower()
        port = parts.port or history.default_port(scheme)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        ok, rule = core.scope.evaluate(scheme, parts.hostname, port, path)
        return {"in_scope": ok, "rule": rule.to_json() if rule else None,
                "decrypted": scheme == "http" or not core.scope.passthrough(parts.hostname, port)}

    # --- repeater ---------------------------------------------------------------------

    @app.get("/api/repeater/tabs", dependencies=guard)
    async def list_tabs() -> dict:
        return {"tabs": await core.db.read(repeater.list_tabs)}

    @app.post("/api/repeater/tabs", dependencies=guard)
    async def create_tab(body: TabCreate) -> dict:
        if body.from_flow:
            row = await core.db.read(history.get_row, body.from_flow)
            if row is None:
                raise HTTPException(status_code=404, detail="flow no encontrado")
            data, orig_body = history.request_for_repeater(row)
            msg = httpmsg.message_payload(data)
            return await core.db.write(
                repeater.create_tab, name=body.name, host=row["host"], port=row["port"],
                tls=row["scheme"] == "https", raw_request=data, encoding=msg["encoding"],
                orig_body=orig_body or None,
            )
        if body.message:
            data = httpmsg.text_to_bytes(body.message.text, body.message.encoding)
            enc = body.message.encoding
        else:
            data, enc = repeater.DEFAULT_REQUEST.encode(), "utf-8"
        return await core.db.write(
            repeater.create_tab, name=body.name, host=body.host, port=body.port, tls=body.tls,
            raw_request=data, encoding=enc, orig_body=None,
        )

    @app.patch("/api/repeater/tabs/{tab_id}", dependencies=guard)
    async def update_tab(tab_id: int, body: TabUpdate) -> dict:
        fields: dict[str, Any] = body.model_dump(exclude_unset=True, exclude={"message"})
        if "tls" in fields:
            fields["tls"] = int(fields["tls"])
        if body.message is not None:
            fields["raw_request"] = httpmsg.text_to_bytes(body.message.text, body.message.encoding)
            fields["encoding"] = body.message.encoding
        tab = await core.db.write(repeater.update_tab, tab_id, fields)
        if tab is None:
            raise HTTPException(status_code=404, detail="pestaña no encontrada")
        return tab

    @app.delete("/api/repeater/tabs/{tab_id}", dependencies=guard)
    async def delete_tab(tab_id: int) -> dict:
        await core.db.write(repeater.delete_tab, tab_id)
        return {"ok": True}

    @app.post("/api/repeater/tabs/{tab_id}/send", dependencies=guard)
    async def send(tab_id: int, body: SendBody) -> dict:
        try:
            return await core.repeater_send(
                tab_id, host=body.host, port=body.port, tls=body.tls,
                message=body.message.model_dump(), fix_content_length=body.fix_content_length,
                timeout=body.timeout,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="pestaña no encontrada") from exc
        except httpmsg.MessageError as exc:
            raise _bad_request(exc) from exc

    # --- CA -----------------------------------------------------------------------------

    @app.get("/api/ca", dependencies=guard)
    async def ca_info() -> dict:
        return await asyncio.to_thread(core.ca_info)

    @app.get("/api/ca/cert", dependencies=guard)
    def ca_cert(format: Literal["pem", "der", "cer", "p12"] = "pem") -> Response:  # noqa: A002
        data, media, name = ca.export(core.confdir, format)
        return Response(data, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/ca/install", dependencies=guard)
    async def ca_install() -> dict:
        try:
            await asyncio.to_thread(ca.install, core.confdir)
        except ca.StoreError as exc:
            raise _bad_request(exc) from exc
        out = await asyncio.to_thread(core.ca_info)
        core.hub.emit("ca.changed", out)
        return out

    @app.post("/api/ca/uninstall", dependencies=guard)
    async def ca_uninstall() -> dict:
        try:
            await asyncio.to_thread(ca.uninstall, core.confdir)
        except ca.StoreError as exc:
            raise _bad_request(exc) from exc
        out = await asyncio.to_thread(core.ca_info)
        core.hub.emit("ca.changed", out)
        return out

    # --- navegador dedicado / proxy del sistema ---------------------------------------

    @app.get("/api/browsers", dependencies=guard)
    async def list_browsers() -> dict:
        found = await asyncio.to_thread(browsers.detect)
        return {"browsers": [b.to_json() for b in found]}

    @app.post("/api/browsers/launch", dependencies=guard)
    async def launch_browser(body: BrowserLaunch) -> dict:
        found = await asyncio.to_thread(browsers.detect)
        if not found:
            raise HTTPException(status_code=404, detail="no se encontró Chrome, Edge, Brave ni Firefox")
        chosen = next((b for b in found if b.id == body.id), None) if body.id else None
        chosen = chosen or next((b for b in found if b.engine == "chromium"), found[0])
        url = body.url.strip() if body.url else None
        if url and "://" not in url:
            url = "https://" + url
        s = core.settings
        spki = ca.spki_sha256_b64(ca.load_cert(core.confdir))
        try:
            pid = await asyncio.to_thread(
                browsers.launch, chosen, proxy_host=s["proxy.listen_host"],
                proxy_port=s["proxy.listen_port"], spki=spki, url=url,
            )
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"no se pudo abrir {chosen.name}: {exc}") from exc
        return {"ok": True, "pid": pid, "browser": chosen.to_json()}

    @app.get("/api/sysproxy", dependencies=guard)
    def get_sysproxy() -> dict:
        return core.sysproxy_status()

    @app.put("/api/sysproxy", dependencies=guard)
    async def put_sysproxy(body: SysProxyPut) -> dict:
        try:
            return await core.set_sysproxy(body.enabled)
        except (OSError, RuntimeError) as exc:
            raise _bad_request(exc) from exc

    @app.post("/api/app/open", dependencies=guard)
    def open_folder(body: OpenTarget) -> dict:
        target = {"data": paths.data_dir(), "ca": core.confdir, "logs": paths.logs_dir()}[body.target]
        try:
            _open_path(target)
        except OSError as exc:
            raise _bad_request(exc) from exc
        return {"ok": True, "path": str(target)}

    # --- WebSocket ---------------------------------------------------------------------

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket, token: str | None = None) -> None:
        origin = ws.headers.get("origin")
        if not auth.valid(token) or (origin and not auth.DEV_MODE and not _origin_ok(origin, ws)):
            await ws.close(code=4401)
            return
        await core.hub.connect(ws)
        try:
            while True:
                msg = await ws.receive_text()
                if msg == "ping":
                    await ws.send_text('[{"type":"pong"}]')
        except WebSocketDisconnect:
            pass
        finally:
            core.hub.disconnect(ws)

    @app.exception_handler(Exception)
    async def unhandled(_request, exc: Exception) -> JSONResponse:
        log.exception("error no manejado en la API", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": f"error interno: {exc}"})

    # --- frontend estático (modo standalone) -----------------------------------------
    if frontend_dir and frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


def _origin_ok(origin: str, ws: WebSocket) -> bool:
    if origin in TAURI_ORIGINS:
        return True
    host = ws.headers.get("host", "")
    return origin in (f"http://{host}", f"https://{host}")
