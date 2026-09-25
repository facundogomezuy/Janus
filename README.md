# Janus

Interceptor de tráfico HTTP/HTTPS self-hosted. Alternativa open source a Burp Suite / Caido, para pentesting y bug bounty.

**Stack:** backend Python + mitmproxy · frontend web · empaquetado con Tauri · Linux + Windows.

Arquitectura completa y roadmap por milestones en [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Estado

**M0 — esqueleto.** Toda la tubería anda end-to-end: el shell de Tauri lanza el
backend Python, lee el handshake (puerto + token) por stdout y abre la webview
apuntada a la API local. El frontend muestra la pantalla History con data falsa.
El motor mitmproxy y la persistencia entran en M1.

```
janus/
├─ backend/          FastAPI + (M1) mitmproxy — el sidecar
│  └─ janus/
│     ├─ main.py     entrypoint + handshake por stdout
│     ├─ server.py   API: /api/status, /api/history
│     ├─ auth.py     token de sesión
│     ├─ fakes.py    data falsa (M0)
│     └─ proxy/addon.py   JanusAddon (stub, M1)
├─ frontend/         index.html autocontenido (pantalla History)
│  └─ assets/        janus-mark.svg, janus-logo.svg
├─ src-tauri/        shell nativo Rust
└─ .github/workflows/build.yml   instaladores por CI
```

## Probar la base ahora (sin Tauri)

Solo el backend + frontend, para trastear la UI:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
JANUS_DEV=1 python -m janus.main
```

Imprime una línea `JANUS_READY {...}` con el puerto elegido y abre la API +
el frontend en `http://127.0.0.1:<port>/`. `JANUS_DEV=1` saltea el token para
poder abrir la página directo.

> Sin backend, `frontend/index.html` también abre solo (doble clic): cae a la
> data falsa embebida. Sirve para ver el look, no para probar la API.

## Correr la app completa (Tauri)

Requiere Rust + [Tauri CLI](https://tauri.app) y el backend congelado como
binario en `src-tauri/binaries/janus-backend-<target-triple>` (ver
`ARCHITECTURE.md` §9). Después:

```bash
cargo tauri dev
```

## Seguridad

La API escucha solo en `127.0.0.1` con un token de sesión que Tauri genera al
arrancar y pasa por el handshake. Sin token → 401 (salvo `JANUS_DEV=1`). Detalle
en `ARCHITECTURE.md` §8.
