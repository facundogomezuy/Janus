// Congela el backend con PyInstaller y lo deja como sidecar de Tauri:
//   src-tauri/binaries/janus-backend-<target-triple>[.exe]
// Usa el Python del venv de backend/ si existe (o $JANUS_PYTHON).
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const backend = join(root, "backend");
const win = process.platform === "win32";

const venvPython = join(backend, ".venv", win ? "Scripts/python.exe" : "bin/python");
const python = process.env.JANUS_PYTHON || (existsSync(venvPython) ? venvPython : win ? "python" : "python3");

const triple = process.env.TAURI_TARGET_TRIPLE ||
  execFileSync("rustc", ["-vV"], { encoding: "utf8" }).match(/^host: (\S+)$/m)[1];

console.log(`» PyInstaller con ${python} (target ${triple})`);
execFileSync(python, [
  "-m", "PyInstaller", "janus.spec", "--noconfirm", "--clean",
  "--distpath", join(backend, "dist"), "--workpath", join(backend, "build"),
], { cwd: backend, stdio: "inherit" });

const ext = win ? ".exe" : "";
const built = join(backend, "dist", `janus-backend${ext}`);
const outDir = join(root, "src-tauri", "binaries");
mkdirSync(outDir, { recursive: true });
const dest = join(outDir, `janus-backend-${triple}${ext}`);
copyFileSync(built, dest);
console.log(`» sidecar listo: ${dest} (${(statSync(dest).size / 1048576).toFixed(1)} MB)`);
