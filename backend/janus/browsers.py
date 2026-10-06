"""Navegador dedicado (ARCHITECTURE.md §10, opción primaria).

Lanza Chrome/Edge/Brave/Chromium con un perfil propio de Janus, el proxy ya
configurado por flags y la CA aceptada por huella SPKI
(``--ignore-certificate-errors-spki-list``): el usuario no toca ningún almacén.
Firefox se lanza con un perfil propio y ``user.js``; como Firefox no acepta
esa flag, confía en la CA vía el almacén de Windows (enterprise roots).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger("janus.browsers")


@dataclass(frozen=True)
class Browser:
    id: str
    name: str
    path: str
    engine: str  # chromium | firefox

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "path": self.path, "engine": self.engine}


def _win_candidates() -> list[tuple[str, str, str, list[str]]]:
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    return [
        ("chrome", "Google Chrome", "chromium", [
            rf"{pf}\Google\Chrome\Application\chrome.exe",
            rf"{pf86}\Google\Chrome\Application\chrome.exe",
            rf"{local}\Google\Chrome\Application\chrome.exe",
        ]),
        ("edge", "Microsoft Edge", "chromium", [
            rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
            rf"{pf}\Microsoft\Edge\Application\msedge.exe",
        ]),
        ("brave", "Brave", "chromium", [
            rf"{pf}\BraveSoftware\Brave-Browser\Application\brave.exe",
            rf"{local}\BraveSoftware\Brave-Browser\Application\brave.exe",
        ]),
        ("chromium", "Chromium", "chromium", [
            rf"{local}\Chromium\Application\chrome.exe",
        ]),
        ("firefox", "Mozilla Firefox", "firefox", [
            rf"{pf}\Mozilla Firefox\firefox.exe",
            rf"{pf86}\Mozilla Firefox\firefox.exe",
        ]),
    ]


def _win_app_path(exe: str) -> str | None:
    import winreg

    sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, sub) as key:
                value, _ = winreg.QueryValueEx(key, None)
                if value and Path(value).is_file():
                    return value
        except OSError:
            continue
    return None


_POSIX = [
    ("chrome", "Google Chrome", "chromium", ["google-chrome", "google-chrome-stable"]),
    ("edge", "Microsoft Edge", "chromium", ["microsoft-edge", "microsoft-edge-stable"]),
    ("brave", "Brave", "chromium", ["brave-browser", "brave"]),
    ("chromium", "Chromium", "chromium", ["chromium", "chromium-browser"]),
    ("firefox", "Mozilla Firefox", "firefox", ["firefox"]),
]

_APP_PATH_EXE = {"chrome": "chrome.exe", "edge": "msedge.exe", "brave": "brave.exe", "firefox": "firefox.exe"}


def detect() -> list[Browser]:
    found: list[Browser] = []
    if sys.platform == "win32":
        for bid, name, engine, candidates in _win_candidates():
            path = next((c for c in candidates if c and Path(c).is_file()), None)
            if path is None and bid in _APP_PATH_EXE:
                path = _win_app_path(_APP_PATH_EXE[bid])
            if path:
                found.append(Browser(bid, name, path, engine))
    else:
        for bid, name, engine, names in _POSIX:
            path = next((p for n in names if (p := shutil.which(n))), None)
            if path:
                found.append(Browser(bid, name, path, engine))
    return found


# Features que generan tráfico de fondo (hora de red, updaters, experimentos,
# SmartScreen...). Van en UN solo --disable-features: si se repite el flag,
# Chromium se queda con el último.
_FEATURES_OFF = [
    "OptimizationHints", "OptimizationGuideModelDownloading", "OptimizationHintsFetching",
    "OptimizationTargetPrediction", "MediaRouter", "DialMediaRouteProvider", "Translate",
    "AutofillServerCommunication", "InterestFeedContentSuggestions",
    "CertificateTransparencyComponentUpdater", "NetworkTimeServiceQuerying", "PrivacySandboxSettings4",
    # Edge
    "msSmartScreenProtection", "msEdgeShoppingUI", "msImplicitSignin", "msEdgeSidebarV2",
    "msEdgeCollections", "msOnlineSearchSuggestions", "EdgeTrendingSearches",
]

# Flags que bajan el ruido: sin telemetría, sync ni updates en el historial.
_CHROMIUM_QUIET = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-search-engine-choice-screen",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
    "--disable-breakpad",
    "--disable-field-trial-config",
    "--safebrowsing-disable-auto-update",
    "--no-pings",
    "--metrics-recording-only",
    "--disable-features=" + ",".join(_FEATURES_OFF),
    "--test-type",  # oculta el aviso "flag no soportada" de la barra
]

_FIREFOX_PREFS = """\
// Generado por Janus. Se reescribe en cada lanzamiento.
user_pref("network.proxy.type", 1);
user_pref("network.proxy.http", "{host}");
user_pref("network.proxy.http_port", {port});
user_pref("network.proxy.ssl", "{host}");
user_pref("network.proxy.ssl_port", {port});
user_pref("network.proxy.share_proxy_settings", true);
user_pref("network.proxy.no_proxies_on", "");
user_pref("network.proxy.allow_hijacking_localhost", true);
user_pref("security.enterprise_roots.enabled", true);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("browser.safebrowsing.malware.enabled", false);
user_pref("browser.safebrowsing.phishing.enabled", false);
user_pref("extensions.pocket.enabled", false);
"""


def _proxy_host(listen_host: str) -> str:
    return "127.0.0.1" if listen_host in ("0.0.0.0", "", "::") else listen_host


def launch(browser: Browser, *, proxy_host: str, proxy_port: int, spki: str, url: str | None) -> int:
    host = _proxy_host(proxy_host)
    start_url = url or "about:blank"
    if browser.engine == "chromium":
        profile = paths.browser_profile(f"{browser.id}-profile")
        args = [
            browser.path,
            f"--user-data-dir={profile}",
            f"--proxy-server={host}:{proxy_port}",
            "--proxy-bypass-list=<-loopback>",  # interceptar también localhost
            f"--ignore-certificate-errors-spki-list={spki}",
            *_CHROMIUM_QUIET,
            "--new-window",
            start_url,
        ]
    else:
        profile = paths.browser_profile("firefox-profile")
        (profile / "user.js").write_text(_FIREFOX_PREFS.format(host=host, port=proxy_port), encoding="utf-8")
        args = [browser.path, "-no-remote", "-profile", str(profile), start_url]

    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        # proceso independiente: sobrevive a Janus y no hereda la consola
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True
    log.info("lanzando %s con proxy %s:%s", browser.name, host, proxy_port)
    try:
        proc = subprocess.Popen(args, **kwargs)  # noqa: S603 - ruta detectada, args propios
    except OSError:
        if sys.platform != "win32":
            raise
        # algunos entornos (jobs sin breakaway) rechazan CREATE_BREAKAWAY_FROM_JOB
        kwargs["creationflags"] &= ~subprocess.CREATE_BREAKAWAY_FROM_JOB
        proc = subprocess.Popen(args, **kwargs)  # noqa: S603
    return proc.pid
