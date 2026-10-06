# Iconos de la app (Tauri)

`app-icon.svg` es la fuente: un cuadrado redondeado oscuro con la marca de Janus.
El resto de los archivos (`icon.ico`, `icon.icns`, `*.png`) se generan a partir de él:

    npm run icons

(equivale a `tauri icon src-tauri/icons/app-icon.svg`). Si regenerás, borrá las
carpetas `android/` e `ios/` que crea el CLI: Janus es solo de escritorio.
