"""Proxy del sistema: backup/restauración contra una clave de registro de prueba.

No toca la configuración real de Internet del usuario: KEY apunta a una clave
temporal bajo HKCU\\Software y la notificación a WinINet se anula.
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")


@pytest.fixture
def fake_key(monkeypatch, tmp_path):
    import winreg

    from janus import sysproxy

    key_path = r"Software\JanusTest\Internet Settings"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, "corp-proxy:3128")
        winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, "http://wpad/proxy.pac")
    monkeypatch.setenv("JANUS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sysproxy, "KEY", key_path)
    monkeypatch.setattr(sysproxy, "_notify", lambda: None)
    yield sysproxy
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\JanusTest")


def test_enable_disable_restores_previous(fake_key):
    sp = fake_key
    assert sp.status("127.0.0.1", 8080)["enabled"] is False
    sp.enable("127.0.0.1", 8080)
    st = sp._read()
    assert st["ProxyEnable"] == 1 and st["ProxyServer"] == "127.0.0.1:8080"
    assert st["AutoConfigURL"] is None  # el PAC se saca mientras Janus está activo
    assert sp.status("127.0.0.1", 8080)["enabled"] is True
    sp.disable()
    st = sp._read()
    assert st["ProxyServer"] == "corp-proxy:3128" and st["AutoConfigURL"] == "http://wpad/proxy.pac"
    assert not sp._backup_path().exists()


def test_recover_after_crash(fake_key):
    sp = fake_key
    sp.enable("0.0.0.0", 9000)  # escuchar en todas -> el sistema apunta a 127.0.0.1
    assert sp._read()["ProxyServer"] == "127.0.0.1:9000"
    # "crash": el backup queda en disco; el próximo arranque restaura
    assert sp.recover() is True
    assert sp._read()["ProxyServer"] == "corp-proxy:3128"
    assert sp.recover() is False
