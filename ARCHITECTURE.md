# Janus — Diseño de arquitectura (base)

Interceptor de tráfico HTTP/HTTPS self-hosted. Alternativa open source a Burp Suite / Caido, orientada a pentesting y bug bounty.

**Stack:** backend Python + mitmproxy como motor · frontend web · empaquetado nativo con Tauri · multiplataforma (Linux + Windows).

---

## 1. Visión de procesos

Janus corre como **un solo binario de Python** (el *sidecar*) lanzado por el shell de Tauri. Dentro de ese proceso conviven, sobre un mismo event loop de asyncio:

- **El motor mitmproxy** — un `Master` embebido programáticamente (no `mitmdump` por línea de comandos), escuchando en el **puerto proxy** (default `8080`). Es el que el navegador/target usa como proxy.
- **El servidor de control** — una app **FastAPI** servida por uvicorn en `127.0.0.1:<puerto aleatorio>`. Expone REST + WebSocket para el frontend. Este es el **puerto de API**, distinto del puerto proxy.

```
┌─────────────────────────── Tauri (shell nativo, Rust) ───────────────────────────┐
│  · lanza el sidecar Python al iniciar                                             │
│  · lee puerto+token por handshake de stdout                                       │
│  · muestra el frontend en una webview                                             │
│                                                                                   │
│   ┌──────────── webview (frontend web) ────────────┐                              │
│   │  History · Intercept · Repeater · Scope · CA    │                             │
│   └───────────────┬────────────────────────────────┘                             │
│                   │ REST + WS (127.0.0.1:API, con token)                          │
└───────────────────┼───────────────────────────────────────────────────────────────┘
                    │
        ┌───────────▼──────────── sidecar Python (1 proceso, asyncio) ──────────┐
        │  FastAPI/uvicorn  ◄──►  servicios (history, scope, intercept, repeat) │
        │        ▲                          ▲                                    │
        │        │ in-process               │ in-process                         │
        │   JanusAddon (hooks)  ◄──────  mitmproxy Master  ──► puerto proxy 8080 │
        │                                                                        │
        │                         SQLite (persistencia)                          │
        └────────────────────────────────────────────────────────────────────────┘
```

Un solo proceso = un solo binario que congelar y empaquetar. Más simple que orquestar dos.

---

## 2. Comunicación

- **Frontend ↔ backend:**
  - **REST** para acciones puntuales (traer historial, mandar repeater, editar scope, bajar la CA).
  - **WebSocket** para el stream en vivo (flows nuevos, avisos de intercept). REST solo no alcanza para pausar/soltar requests en tiempo real.
- **Backend ↔ mitmproxy:** in-process. El addon llama directo a los servicios compartidos; no hay red de por medio.

### Eventos del WebSocket (`/ws`)
| Evento | Cuándo |
|---|---|
| `flow.new` | llega una request nueva |
| `flow.complete` | llegó la response, flow cerrado |
| `intercept.pending` | un flow quedó retenido esperando decisión |
| `intercept.resolved` | se resolvió (forward/drop) |
| `proxy.status` | cambió estado del proxy / intercept toggle |

---

## 3. El addon de mitmproxy (`JanusAddon`)

Toda la lógica cuelga de los hooks de mitmproxy:

- **`request(flow)`**
  1. Evaluar scope. Fuera de scope → se registra igual pero marcado `in_scope=false` (o se ignora antes, ver §6).
  2. Si *intercept* está activo y el flow está en scope → `flow.intercept()` para retenerlo, guardarlo en la cola por `flow.id`, emitir `intercept.pending`. El hook queda esperando hasta que la API resuelva con `flow.resume()` o mate el flow.
  3. Emitir `flow.new`.
- **`response(flow)`** — igual que arriba si el intercept de responses está activo.
- **Ambos** — persistir/actualizar en SQLite y emitir `flow.complete`.

> Clave del intercept: mitmproxy retiene el flow con `flow.intercept()` y lo libera con `flow.resume()`. El endpoint de la API muta `flow.request` (o `flow.response`) con los bytes editados y recién ahí hace `resume`.

---

## 4. Modelo de datos (SQLite)

Persistencia desde el día uno: habilita "proyectos" guardables y el Intruder de v2 reusa el mismo modelo de request.

- **`projects`** — `id, name, created_at`. En v1 puede haber un proyecto implícito, pero el esquema ya lo contempla.
- **`flows`** — `id (uuid), project_id, ts, source (proxy|repeater), method, scheme, host, port, path, http_version, status_code, reason, req_headers (json), req_body (blob), res_headers (json), res_body (blob), duration_ms, in_scope`. Los cuerpos se guardan como **bytes crudos** (BLOB), opcionalmente comprimidos; se decodifican al mostrar.
- **`scope_rules`** — `id, project_id, kind (include|exclude), matcher, enabled`.
- **`repeater_tabs`** — `id, name, host, port, tls, raw_request (blob)` — estado de cada pestaña de repeater.
- **`settings`** — key/value (puerto proxy, intercept default, etc.).

---

## 5. Superficie de API (borrador)

Todo bajo `/api`, con header `Authorization: Bearer <token>`.

**Estado**
- `GET /api/status` — proxy activo, puertos, ruta de la CA, estado de intercept.

**Intercept**
- `GET /api/intercept` · `POST /api/intercept` `{enabled, scope: request|response|both}`
- `GET /api/intercept/queue` — flows retenidos
- `POST /api/intercept/{id}/forward` — body con los bytes editados → `resume`
- `POST /api/intercept/{id}/drop` — mata el flow

**History**
- `GET /api/history` — paginado, filtros (host, método, status, solo-scope, búsqueda)
- `GET /api/history/{id}` — detalle completo
- `DELETE /api/history` — limpiar

**Repeater**
- `POST /api/repeater/send` `{host, port, tls, raw_request}` → response (bytes crudos + parseada)
- CRUD de pestañas (opcional en v1)

**Scope**
- `GET /api/scope` · `PUT /api/scope` — lista de reglas

**CA**
- `GET /api/ca/cert` — baja la CA de mitmproxy (PEM/DER)
- `GET /api/ca/instructions` — pasos guiados por SO

---

## 6. Scope

Dos niveles, no uno:
1. **Filtro de historial** — esconder de la vista lo que no interesa.
2. **`ignore_hosts` de mitmproxy** — lo que queda fuera de scope **ni se descifra**. Esto evita romper apps con *certificate pinning* y ahorra ruido real, no solo visual.

El scope se traduce a ambos: reglas `exclude` alimentan `ignore_hosts`; el resto filtra la tabla.

---

## 7. Repeater — envío crudo

**No usar `httpx`/`requests`.** Esos clientes normalizan la request (reordenan headers, arreglan `Content-Length`, no dejan mandar cosas malformadas) — y mandar requests rotas a propósito es justo la gracia del repeater.

Módulo propio `rawhttp.py`: abre socket a `host:port`, lo envuelve en TLS (`ssl`) si es https, escribe los **bytes tal cual** y lee la respuesta. Sin agregar headers automáticamente. Maneja `chunked`, cierre de conexión y timeouts. Helper opcional para recalcular `Content-Length` solo si el usuario lo pide.

---

## 8. Seguridad de la app

Es una herramienta de seguridad: esto es lo primero que van a mirar en el repo.

- **API solo en `127.0.0.1`**, puerto alto aleatorio.
- **Token de auth** generado al arrancar, pasado a Tauri por el handshake de stdout. El frontend lo manda en cada request (y en el WS). Sin token → 401. Esto corta que cualquier web abierta en el navegador le pegue a la API local (DNS-rebinding y similares).
- **CORS** acotado al origin de la webview de Tauri.
- El **puerto proxy** (8080) es otra cosa: ahí sí escucha el proxy real; su superficie es la propia de un MITM.

---

## 9. Empaquetado y build

- **Backend** congelado con **PyInstaller** → un binario por SO. mitmproxy freezea con algún cuidado (hidden imports, data files); hay recetas conocidas.
- **Tauri** registra ese binario como **sidecar** (`externalBin`) por *target triple* (`...-unknown-linux-gnu`, `...-pc-windows-msvc`). El `main.rs` lo lanza con `Command::new_sidecar`, lee puerto+token de stdout y recién ahí abre la ventana.
- **CI: GitHub Actions** con matriz `ubuntu-latest` + `windows-latest` → PyInstaller + build de Tauri → instaladores (`.deb`/`.AppImage`, `.msi`/`.exe`). Compilar en cada plataforma es obligatorio; no se cross-compila cómodo.

---

## 10. CA simplificada

Instalar la CA en cada almacén de certificados (Windows, Linux, y Firefox que usa el suyo propio) es fricción. Estrategia recomendada, como el navegador embebido de Burp:

- **Opción primaria:** lanzar un Chromium dedicado con el proxy y la CA ya seteados por flags. El usuario no toca ningún almacén.
- **Opción secundaria:** instalación manual guiada vía `GET /api/ca/instructions`, paso a paso por SO.

---

## 11. Layout del repo

```
janus/
├─ backend/
│  ├─ janus/
│  │  ├─ main.py            # entrypoint: args, arranca el loop
│  │  ├─ server.py          # FastAPI + uvicorn
│  │  ├─ proxy/
│  │  │  ├─ master.py       # setup del Master de mitmproxy
│  │  │  └─ addon.py        # JanusAddon (hooks)
│  │  ├─ intercept.py       # cola/manager de intercept
│  │  ├─ history.py         # servicio + queries de historial
│  │  ├─ scope.py           # matching de scope
│  │  ├─ repeater.py        # usa rawhttp
│  │  ├─ rawhttp.py         # cliente crudo socket/TLS
│  │  ├─ db.py              # SQLite + schema
│  │  ├─ ca.py              # export de CA + instrucciones
│  │  ├─ auth.py            # token
│  │  └─ ws.py              # hub de websockets
│  ├─ pyproject.toml
│  └─ janus.spec            # PyInstaller
├─ frontend/                # SPA (recomiendo Svelte + Vite, o React)
│  ├─ src/
│  ├─ package.json
│  └─ vite.config.*
├─ src-tauri/
│  ├─ src/main.rs
│  ├─ tauri.conf.json
│  └─ Cargo.toml
├─ .github/workflows/build.yml
├─ ARCHITECTURE.md
└─ README.md
```

---

## 12. Orden de construcción (por milestones)

Vertical slice primero, features después: se front-loadea la plomería riesgosa y se llega a algo usable temprano.

| Milestone | Qué entra | Por qué en este orden |
|---|---|---|
| **M0 — Esqueleto** | Backend arranca mitmproxy (8080) + FastAPI (puerto random) con `/status`. Tauri lanza el sidecar y muestra "conectado". | Prueba que toda la tubería (sidecar + handshake + webview) funciona end-to-end **antes** de escribir una sola feature. |
| **M1 — History (pasivo)** | El addon registra cada flow en SQLite + `flow.new`. Tabla de historial + visor request/response (raw/pretty/hex). Sin intercept todavía. | Ya es útil solo. Desriesga el modelo de datos y el streaming por WS. |
| **M2 — Scope** | Reglas de scope → `ignore_hosts` + filtro de historial. | Chico, y deja todo lo demás más limpio. |
| **M3 — Intercept** | Retener/forward/drop, editor de request. | La pieza async más delicada; se hace una vez que history + WS están sólidos. |
| **M4 — Repeater** | Módulo `rawhttp` + UI de repeater con pestañas. | Reusa el visor de M1 y el envío crudo. |
| **M5 — CA + packaging** | CA guiada/Chromium dedicado, pulido de PyInstaller, instaladores por CI. | Cierre para que sea instalable de verdad en Linux y Windows. |

**v2 — Intruder/fuzzing:** se apoya en `rawhttp` + un motor de payloads (wordlists) + reporte. El modelo de request ya está listo desde M4.

---

## 13. Fuera de alcance (v1)

Decoder, escáner de vulnerabilidades automático, extensiones/plugins de terceros, colaboración multiusuario, y **cualquier integración con IA**. La arquitectura queda abierta a sumar después un plugin de IA, pero no es parte de este alcance.
