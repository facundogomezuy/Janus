// Janus — shell nativo (Tauri).
//
// Al iniciar:
//   1. Lanza el binario del backend Python como sidecar.
//   2. Lee de su stdout la línea de handshake:  JANUS_READY {"port":..,"token":..}
//   3. Inyecta el token en la webview y la apunta al frontend, que le pega
//      a la API local en 127.0.0.1:<port>.
//
// El frontend vive empaquetado (frontendDist = ../frontend). El token se pone
// en window.__JANUS_TOKEN__ vía script de init antes de que cargue la página.
//
// Nota: en M0 esto queda como scaffold. Requiere Rust + Tauri CLI en la máquina
// para compilar (`cargo tauri dev`). El backend se prueba solo con JANUS_DEV=1.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{Arc, Mutex};

use serde::Deserialize;
use tauri::{Emitter, Manager};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

#[derive(Debug, Deserialize, Clone)]
struct Handshake {
    port: u16,
    token: String,
    #[serde(default)]
    dev: bool,
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let handle = app.handle().clone();
            let hs_slot: Arc<Mutex<Option<Handshake>>> = Arc::new(Mutex::new(None));

            // Lanzar el sidecar del backend.
            let sidecar = app
                .shell()
                .sidecar("janus-backend")
                .expect("no se encontró el sidecar janus-backend");
            let (mut rx, _child) = sidecar.spawn().expect("no se pudo lanzar el backend");

            let hs_for_task = hs_slot.clone();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stdout(line) = event {
                        let text = String::from_utf8_lossy(&line);
                        let text = text.trim();
                        if let Some(payload) = text.strip_prefix("JANUS_READY ") {
                            if let Ok(hs) = serde_json::from_str::<Handshake>(payload) {
                                // Inyectar el token y apuntar la webview al backend.
                                if let Some(win) = handle.get_webview_window("main") {
                                    let js = format!(
                                        "window.__JANUS_TOKEN__ = {:?}; window.__JANUS_API__ = \"http://127.0.0.1:{}\";",
                                        hs.token, hs.port
                                    );
                                    let _ = win.eval(&js);
                                    let url = format!("http://127.0.0.1:{}/", hs.port);
                                    let _ = win.eval(&format!("window.location.replace({:?});", url));
                                }
                                *hs_for_task.lock().unwrap() = Some(hs.clone());
                                let _ = handle.emit("backend-ready", hs);
                            }
                        }
                    }
                }
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error al arrancar Janus");
}
