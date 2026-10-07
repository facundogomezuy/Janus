"""Entrypoint del sidecar (ARCHITECTURE.md §1, §8).

Arranque:
  1. Abre la base, levanta el motor mitmproxy y la API (FastAPI/uvicorn) en el
     mismo event loop. La API escucha solo en 127.0.0.1, en un puerto libre.
  2. Cuando ambos están listos imprime por stdout la línea que Tauri parsea:
         JANUS_READY {"port": <int>, "token": "<str>", ...}
     (con --sidecar, antes va "JANUS_PID <pid>" para poder cortar un arranque lento)
  3. Con ``--sidecar`` (lo pasa Tauri) vigila stdin: cuando Tauri se cierra
     —o muere— el pipe da EOF y el backend se apaga ordenadamente (restaura el
     proxy del sistema, libera flows retenidos). Así nunca queda huérfano.

Uso standalone (sin Tauri):
    python -m janus            # abre la UI en una ventana de Edge (Windows)
    python -m janus --no-open  # solo imprime la URL
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import webbrowser

import uvicorn

from . import __version__, auth, paths, sysproxy

log = logging.getLogger("janus")

HOST = "127.0.0.1"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="janus-backend", description="Backend de Janus")
    p.add_argument("--sidecar", action="store_true", help="lanzado por Tauri: vigila stdin, no abre UI")
    p.add_argument("--no-open", action="store_true", help="no abrir la UI (modo standalone)")
    p.add_argument("--api-port", type=int, default=0, help="puerto de la API (0 = libre)")
    p.add_argument("--proxy-port", type=int, help="puerto del proxy para esta sesión")
    p.add_argument("--data-dir", help="directorio de datos (default: %%LOCALAPPDATA%%\\io.janus.app)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _fix_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def _setup_logging(verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(
        paths.logs_dir() / "janus.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.setLevel(logging.DEBUG if verbose else logging.WARNING)
        root.addHandler(sh)
    # mitmproxy loguea cada conexión en INFO: solo nos interesan los avisos
    logging.getLogger("mitmproxy").setLevel(logging.DEBUG if verbose else logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)


class _Server(uvicorn.Server):
    """uvicorn sin manejo propio de señales: el apagado lo coordina run()."""

    @contextlib.contextmanager
    def capture_signals(self):  # type: ignore[override]
        yield


def _watch_stdin(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    # os.read sobre el descriptor, no sys.stdin.buffer: un readline() bloqueado
    # retiene el lock del BufferedReader y, si el apagado llega por otro lado
    # (/api/shutdown), el intérprete aborta al cerrarlo ("could not acquire lock
    # for <stdin> at interpreter shutdown").
    def run() -> None:
        try:
            fd = sys.stdin.fileno() if sys.stdin is not None else None
        except (OSError, ValueError):
            fd = None
        if fd is None:
            return
        pending = b""
        try:
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break  # EOF: Tauri cerró el pipe o murió
                pending += chunk
                *lines, pending = pending.split(b"\n")
                if any(line.strip() == b"shutdown" for line in lines):
                    break
        except (OSError, ValueError):
            pass
        loop.call_soon_threadsafe(stop.set)

    threading.Thread(target=run, name="janus-stdin", daemon=True).start()


def _open_ui(url: str) -> None:
    """Standalone: ventana de app de Edge (siempre está en Windows 10/11)."""
    if sys.platform == "win32":
        from . import browsers

        edge = next((b for b in browsers.detect() if b.id == "edge"), None)
        if edge is not None:
            profile = paths.browser_profile("ui-profile")
            subprocess.Popen(  # noqa: S603
                [edge.path, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                 "--no-default-browser-check", "--window-size=1320,860"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS,
            )
            return
    webbrowser.open(url)


async def _run(args: argparse.Namespace) -> int:
    from .core import Core
    from .db import Database
    from .server import create_app

    loop = asyncio.get_running_loop()
    if sysproxy.recover():
        log.warning("se restauró el proxy del sistema que había quedado de una sesión anterior")

    db = Database(paths.db_path())
    core = Core(db)
    core.hub.bind_loop(loop)
    if args.proxy_port:
        core.settings.apply({"proxy.listen_port": args.proxy_port})
    master = core.build_master()

    stop = asyncio.Event()
    app = create_app(core, frontend_dir=paths.frontend_dir(), on_shutdown=stop.set)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        # que nadie más pueda bindear el mismo puerto de la API
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sock.bind((HOST, args.api_port))
    api_port = sock.getsockname()[1]

    config = uvicorn.Config(app, log_level="warning", access_log=False, lifespan="off")
    server = _Server(config)

    proxy_task = asyncio.create_task(master.run(), name="mitmproxy")
    api_task = asyncio.create_task(server.serve(sockets=[sock]), name="api")
    exit_code = 0
    try:
        while not server.started:
            if api_task.done():
                api_task.result()  # propaga el error de arranque
                raise RuntimeError("la API no arrancó")
            await asyncio.sleep(0.01)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(core.proxy_ready.wait(), timeout=8)

        handshake = {
            "port": api_port,
            "token": auth.API_TOKEN,
            "dev": auth.DEV_MODE,
            "version": __version__,
            "pid": os.getpid(),
            "proxy": core.proxy_status(),
        }
        if sys.stdout is not None:
            print("JANUS_READY " + json.dumps(handshake), flush=True)
        url = f"http://{HOST}:{api_port}/#token={auth.API_TOKEN}"
        log.info("API en http://%s:%s — proxy %s", HOST, api_port, core._mode_spec())

        if args.sidecar:
            _watch_stdin(loop, stop)
        elif not args.no_open and paths.frontend_dir():
            threading.Thread(target=_open_ui, args=(url,), daemon=True).start()
        if not args.sidecar and sys.stderr is not None:
            print(f"Janus listo: {url}", file=sys.stderr, flush=True)

        stop_task = asyncio.create_task(stop.wait(), name="stop")
        done, _ = await asyncio.wait({stop_task, proxy_task, api_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in (proxy_task, api_task):
            if task in done and not task.cancelled() and task.exception():
                log.error("%s terminó con error", task.get_name(), exc_info=task.exception())
                exit_code = 1
        stop_task.cancel()
    except asyncio.CancelledError:
        log.info("interrumpido (Ctrl+C)")
    finally:
        await _shutdown(core, server, proxy_task, api_task)
        await asyncio.to_thread(db.close)
    return exit_code


async def _shutdown(core, server: uvicorn.Server, *tasks: asyncio.Task) -> None:
    log.info("apagando…")
    core.shutdown_services()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(core.hub.close_all(), timeout=2)
    server.should_exit = True
    pending = [t for t in tasks if not t.done()]
    if pending:
        _, still = await asyncio.wait(pending, timeout=5)
        for t in still:
            t.cancel()
        if still:
            await asyncio.wait(still, timeout=2)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.data_dir:
        os.environ["JANUS_DATA_DIR"] = args.data_dir
    _fix_stdio()
    if args.sidecar and sys.stdout is not None:
        # Antes de lo pesado (mitmproxy): si Tauri tiene que cortar un arranque
        # lento, mata a este proceso y no al cargador de PyInstaller, que así
        # puede borrar su carpeta temporal (_MEI).
        print(f"JANUS_PID {os.getpid()}", flush=True)
    _setup_logging(args.verbose)
    log.info("Janus %s (Python %s, %s)", __version__, sys.version.split()[0], sys.platform)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except Exception:
        log.exception("error fatal")
        return 1


if __name__ == "__main__":
    sys.exit(main())
