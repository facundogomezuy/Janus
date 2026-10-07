// Janus — shell nativo (Tauri 2).
//
// Al iniciar lanza el backend Python (sidecar `janus-backend --sidecar`), lee
// su handshake por stdout —`JANUS_READY {"port":..,"token":..}`— y se lo da al
// frontend con el comando `backend_info`. El frontend viene empaquetado
// (frontendDist) y le habla directo a la API local en 127.0.0.1:<port>.
//
// Ciclo de vida del motor: vigila su stdin. Al cerrar Janus le mandamos
// "shutdown" y esperamos a que termine (restaura el proxy del sistema, libera
// el puerto); si no contesta, se lo mata. Si Janus muere de golpe, el pipe da
// EOF y el motor se apaga solo: nunca queda huérfano.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::VecDeque;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State};
use tauri_plugin_shell::process::{Command, CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const LOG_TAIL: usize = 60;
const STOP_TIMEOUT: Duration = Duration::from_secs(5);
const KILL_TIMEOUT: Duration = Duration::from_secs(3);

#[derive(Clone, Deserialize)]
struct Handshake {
    port: u16,
    token: String,
    /// PID del intérprete Python. Con PyInstaller onefile no es el del proceso
    /// que lanzamos: ese es el cargador que descomprime el motor en %TEMP%.
    #[serde(default)]
    pid: Option<u32>,
}

/// Se marca cuando el proceso del motor terminó (para esperar su apagado).
#[derive(Default)]
struct Exit {
    done: Mutex<bool>,
    cv: Condvar,
}

impl Exit {
    fn signal(&self) {
        *self.done.lock().unwrap() = true;
        self.cv.notify_all();
    }

    fn wait(&self, timeout: Duration) -> bool {
        let guard = self.done.lock().unwrap();
        let (guard, _) = self.cv.wait_timeout_while(guard, timeout, |done| !*done).unwrap();
        *guard
    }
}

#[derive(Default)]
struct Inner {
    child: Option<CommandChild>,
    exit: Option<Arc<Exit>>,
    ready: Option<Handshake>,
    /// PID del intérprete ("JANUS_PID" al arrancar o el del handshake).
    engine_pid: Option<u32>,
    error: Option<String>,
    logs: VecDeque<String>,
    generation: u64,
}

#[derive(Default)]
struct Backend(Mutex<Inner>);

#[derive(Serialize)]
struct BackendInfo {
    state: &'static str,
    port: Option<u16>,
    token: Option<String>,
    error: Option<String>,
    logs: Option<String>,
}

#[tauri::command]
fn backend_info(backend: State<'_, Backend>) -> BackendInfo {
    let inner = backend.0.lock().unwrap();
    if let Some(hs) = &inner.ready {
        return BackendInfo {
            state: "ready",
            port: Some(hs.port),
            token: Some(hs.token.clone()),
            error: None,
            logs: None,
        };
    }
    if let Some(err) = &inner.error {
        let logs: Vec<String> = inner.logs.iter().cloned().collect();
        return BackendInfo {
            state: "error",
            port: None,
            token: None,
            error: Some(err.clone()),
            logs: (!logs.is_empty()).then(|| logs.join("\n")),
        };
    }
    BackendInfo { state: "starting", port: None, token: None, error: None, logs: None }
}

#[tauri::command]
fn restart_backend(app: AppHandle) -> Result<(), String> {
    stop_backend(&app);
    spawn_backend(&app)
}

#[tauri::command]
fn open_logs(app: AppHandle) -> Result<(), String> {
    let dir = data_dir(&app).join("logs");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    open_folder(&dir)
}

/// Mismo directorio que backend/janus/paths.py (= `app_local_data_dir()`).
fn data_dir(app: &AppHandle) -> PathBuf {
    if let Some(dir) = std::env::var_os("JANUS_DATA_DIR") {
        return PathBuf::from(dir);
    }
    app.path()
        .app_local_data_dir()
        .unwrap_or_else(|_| std::env::temp_dir().join("io.janus.app"))
}

fn open_folder(dir: &PathBuf) -> Result<(), String> {
    #[cfg(windows)]
    let program = "explorer";
    #[cfg(target_os = "macos")]
    let program = "open";
    #[cfg(all(unix, not(target_os = "macos")))]
    let program = "xdg-open";
    std::process::Command::new(program).arg(dir).spawn().map(|_| ()).map_err(|e| e.to_string())
}

fn backend_command(app: &AppHandle) -> Result<Command, String> {
    // En desarrollo se puede correr el backend desde el código:
    //   JANUS_BACKEND_PYTHON=backend/.venv/Scripts/python.exe npm run dev
    #[cfg(debug_assertions)]
    if let Ok(python) = std::env::var("JANUS_BACKEND_PYTHON") {
        let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/../backend");
        return Ok(app.shell().command(python).args(["-m", "janus", "--sidecar"]).current_dir(dir));
    }
    app.shell()
        .sidecar("janus-backend")
        .map(|cmd| cmd.args(["--sidecar"]))
        .map_err(|e| format!("no se encontró el motor (janus-backend): {e}"))
}

fn push_log(inner: &mut Inner, line: &str) {
    if line.is_empty() {
        return;
    }
    if inner.logs.len() == LOG_TAIL {
        inner.logs.pop_front();
    }
    inner.logs.push_back(line.to_string());
}

fn spawn_backend(app: &AppHandle) -> Result<(), String> {
    let command = backend_command(app)?;
    let (mut rx, child) = command.spawn().map_err(|e| format!("no se pudo lanzar el motor: {e}"))?;
    let exit = Arc::new(Exit::default());
    let generation = {
        let backend = app.state::<Backend>();
        let mut inner = backend.0.lock().unwrap();
        inner.generation += 1;
        inner.child = Some(child);
        inner.exit = Some(exit.clone());
        inner.ready = None;
        inner.engine_pid = None;
        inner.error = None;
        inner.logs.clear();
        inner.generation
    };

    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            let backend = app.state::<Backend>();
            let mut inner = backend.0.lock().unwrap();
            let current = inner.generation == generation;
            match event {
                CommandEvent::Stdout(line) => {
                    let text = String::from_utf8_lossy(&line);
                    let text = text.trim();
                    if let Some(payload) = text.strip_prefix("JANUS_READY ") {
                        if current {
                            match serde_json::from_str::<Handshake>(payload) {
                                Ok(hs) => {
                                    inner.engine_pid = inner.engine_pid.or(hs.pid);
                                    inner.ready = Some(hs);
                                }
                                Err(e) => inner.error = Some(format!("handshake inválido del motor: {e}")),
                            }
                        }
                    } else if let Some(pid) = text.strip_prefix("JANUS_PID ") {
                        if current {
                            inner.engine_pid = pid.trim().parse().ok();
                        }
                    } else if current {
                        push_log(&mut inner, text);
                    }
                }
                CommandEvent::Stderr(line) => {
                    if current {
                        push_log(&mut inner, String::from_utf8_lossy(&line).trim_end());
                    }
                }
                CommandEvent::Error(err) => {
                    if current {
                        inner.error.get_or_insert(format!("error del motor: {err}"));
                    }
                }
                CommandEvent::Terminated(payload) => {
                    exit.signal();
                    if current {
                        inner.child = None;
                        let was_ready = inner.ready.take().is_some();
                        let code = payload.code.map(|c| c.to_string()).unwrap_or_else(|| "?".into());
                        inner.error = Some(if was_ready {
                            format!("El motor de Janus se detuvo inesperadamente (código {code}).")
                        } else {
                            format!("El motor de Janus terminó al arrancar (código {code}).")
                        });
                        drop(inner);
                        let _ = app.emit("backend://exited", ());
                    }
                    break;
                }
                _ => {}
            }
        }
    });
    Ok(())
}

/// Apagado ordenado: "shutdown" por stdin, esperar, y recién ahí matar.
///
/// El sidecar es un onefile de PyInstaller: `child` es el cargador, que lanza
/// el intérprete como hijo y borra %TEMP%\_MEIxxxx cuando ese hijo termina.
/// `child.kill()` solo mata al cargador y el motor quedaría huérfano con los
/// puertos tomados. Si no contesta, se mata al intérprete y se deja que el
/// cargador limpie y salga solo; `child.kill()` queda como último recurso.
fn stop_backend(app: &AppHandle) {
    let (child, exit, engine_pid) = {
        let backend = app.state::<Backend>();
        let mut inner = backend.0.lock().unwrap();
        inner.generation += 1; // los eventos del proceso viejo ya no tocan el estado
        inner.ready = None;
        (inner.child.take(), inner.exit.take(), inner.engine_pid.take())
    };
    let Some(mut child) = child else { return };
    let _ = child.write(b"shutdown\n");
    let exited = |timeout| exit.as_ref().is_some_and(|e| e.wait(timeout));
    if exited(STOP_TIMEOUT) {
        return;
    }
    // en modo desarrollo no hay cargador: `child` es el intérprete mismo
    if engine_pid != Some(child.pid()) {
        kill_engine(child.pid(), engine_pid);
        if exited(KILL_TIMEOUT) {
            return;
        }
    }
    let _ = child.kill();
}

/// Mata al intérprete del motor sin tocar al cargador, y sin abrir consolas.
/// Nunca a un PID reusado por otro programa: en Unix se apunta a los hijos del
/// cargador (`pkill -P`); en Windows se filtra por PID *y* por nombre de imagen,
/// porque el cargador cierra el handle del intérprete cuando este sale y,
/// mientras limpia %TEMP%, ese PID puede volver a asignarse.
fn kill_engine(loader: u32, engine: Option<u32>) {
    #[cfg(windows)]
    let mut cmd = {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let taskkill = std::env::var_os("SystemRoot")
            .map(|root| PathBuf::from(root).join("System32").join("taskkill.exe"))
            .unwrap_or_else(|| PathBuf::from("taskkill.exe"));
        let mut cmd = std::process::Command::new(taskkill);
        match engine {
            Some(pid) => cmd.args(["/F", "/FI", &format!("PID eq {pid}"), "/FI", "IMAGENAME eq janus-backend.exe"]),
            // todavía descomprimiendo: no hay intérprete conocido, se baja el árbol
            None => cmd.args(["/F", "/T", "/PID", &loader.to_string()]),
        };
        cmd.creation_flags(CREATE_NO_WINDOW);
        cmd
    };
    #[cfg(unix)]
    let mut cmd = {
        let _ = engine;
        let mut cmd = std::process::Command::new("pkill");
        cmd.args(["-KILL", "-P", &loader.to_string()]);
        cmd
    };
    let _ = cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status();
}

fn focus_main(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

fn main() {
    let app = tauri::Builder::default()
        // Una sola instancia: abrir Janus de nuevo enfoca la ventana existente
        // (dos instancias pelearían por el puerto del proxy).
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| focus_main(app)))
        .plugin(tauri_plugin_shell::init())
        .manage(Backend::default())
        .invoke_handler(tauri::generate_handler![backend_info, restart_backend, open_logs])
        .setup(|app| {
            if let Err(err) = spawn_backend(app.handle()) {
                app.state::<Backend>().0.lock().unwrap().error = Some(err);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error al construir Janus");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_backend(handle);
        }
    });
}
