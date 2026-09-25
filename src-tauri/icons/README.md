# Iconos de la app (Tauri)

Faltan los binarios de icono que Tauri empaqueta por plataforma:
`32x32.png`, `128x128.png`, `icon.ico` (Windows), `icon.icns` (macOS).

Generalos a partir del logo con el CLI de Tauri, apuntando al mark:

    cargo tauri icon ../frontend/assets/janus-mark.svg

Eso llena esta carpeta con todos los tamaños/formatos que pide `tauri.conf.json`.
(El SVG del mark ya está en `frontend/assets/` y no depende de nada externo.)
