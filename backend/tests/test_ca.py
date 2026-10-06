"""CA: generación con marca propia + almacén de Windows contra un store descartable.

El almacén Raíz real del usuario no se toca: install/uninstall se prueban
contra "JanusTest" (sin diálogos de seguridad) y la clave se borra al final.
"""
from __future__ import annotations

import sys

import pytest

from janus import ca


def test_ensure_ca_is_branded_and_stable(tmp_path):
    ca.ensure_ca(tmp_path)
    info = ca.info(tmp_path)
    assert info["cn"] == "Janus Interception CA" and info["org"] == "Janus"
    assert len(info["spki_sha256"]) == 44  # base64 de un SHA-256
    ca.ensure_ca(tmp_path)  # idempotente: no la regenera
    assert ca.info(tmp_path)["sha256"] == info["sha256"]
    data, media, name = ca.export(tmp_path, "der")
    assert media == "application/pkix-cert" and name == "janus-ca.cer" and data[0] == 0x30


@pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")
def test_windows_store_roundtrip_in_scratch_store(tmp_path):
    import winreg

    ca.ensure_ca(tmp_path)
    try:
        ca.install(tmp_path, store_name="JanusTest")
        ca.install(tmp_path, store_name="JanusTest")  # REPLACE_EXISTING: no duplica
        assert ca.uninstall(tmp_path, store_name="JanusTest") == 1
        assert ca.uninstall(tmp_path, store_name="JanusTest") == 0
        assert ca.is_installed(ca.der_bytes(tmp_path)) is False  # nunca tocó ROOT
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\SystemCertificates\JanusTest\Certificates")
        except OSError:
            pass
        for sub in ("CRLs", "CTLs", "Certificates", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\SystemCertificates\JanusTest" + ("\\" + sub if sub else ""))
            except OSError:
                pass
