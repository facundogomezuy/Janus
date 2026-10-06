// Corre el backend desde el código (sin Tauri) y abre la UI en una ventana de Edge.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const backend = join(dirname(fileURLToPath(import.meta.url)), "..", "backend");
const win = process.platform === "win32";
const venvPython = join(backend, ".venv", win ? "Scripts/python.exe" : "bin/python");
const python = process.env.JANUS_PYTHON || (existsSync(venvPython) ? venvPython : win ? "python" : "python3");

const child = spawn(python, ["-m", "janus", ...process.argv.slice(2)], { cwd: backend, stdio: "inherit" });
child.on("exit", (code) => process.exit(code ?? 0));
