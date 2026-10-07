# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: congela el backend de Janus en un único ejecutable.

Ese binario es el sidecar que lanza Tauri (ARCHITECTURE.md §9). Incluye el
frontend, así `janus-backend.exe` solo también sirve la UI en modo standalone.

    pyinstaller janus.spec --noconfirm        (o: npm run backend:build)
"""
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)  # noqa: F821 - lo define PyInstaller
ROOT = HERE.parent
VERSION = re.search(r'__version__ = "([^"]+)"', (HERE / "janus" / "__init__.py").read_text(encoding="utf-8")).group(1)

hiddenimports = []
for pkg in ("mitmproxy.addons", "mitmproxy.proxy", "mitmproxy.net", "mitmproxy.utils", "uvicorn", "janus"):
    hiddenimports += collect_submodules(pkg)

datas = [(str(ROOT / "frontend"), "frontend")]
datas += collect_data_files("mitmproxy.addons.onboardingapp")  # http://mitm.it

a = Analysis(  # noqa: F821
    [str(HERE / "run_janus.py")],
    pathex=[str(HERE)],
    hiddenimports=hiddenimports,
    datas=datas,
    excludes=[
        "tkinter", "mitmproxy.tools", "urwid", "tornado",
        "pytest", "playwright", "IPython", "numpy", "matplotlib", "setuptools", "pip",
    ],
    noarchive=False,
)

# Sin WinDivert ni el redirector: Janus no usa los modos transparente/local de
# mitmproxy (pydivert carga la DLL recién al usarse). Achica el binario y evita
# falsos positivos de antivirus con el driver.
DROP = ("windivert", "windows-redirector")
a.binaries = [b for b in a.binaries if not any(d in b[0].lower() for d in DROP)]
a.datas = [d for d in a.datas if not any(d_ in d[0].lower() for d_ in DROP)]

pyz = PYZ(a.pure)  # noqa: F821

version_info = None
icon = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )

    nums = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Janus"),
                StringStruct("FileDescription", "Janus — motor del proxy"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "janus-backend"),
                StringStruct("OriginalFilename", "janus-backend.exe"),
                StringStruct("ProductName", "Janus"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
    ico = ROOT / "src-tauri" / "icons" / "icon.ico"
    icon = str(ico) if ico.exists() else None

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="janus-backend",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # sin ventana de consola: lo lanza Tauri con pipes
    icon=icon,
    version=version_info,
)
