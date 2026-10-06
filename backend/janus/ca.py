"""CA de interceptación (ARCHITECTURE.md §10).

mitmproxy busca su CA en ``<confdir>/mitmproxy-ca.pem``. Si no existe, la
generamos nosotros antes de arrancar el motor, con nombre propio ("Janus"), así
en el almacén de certificados aparece como Janus y no como "mitmproxy".

En Windows la CA se instala en el almacén "Raíz de confianza" del *usuario
actual* (no requiere admin). Windows muestra su propio diálogo de seguridad
pidiendo confirmación: eso es deliberado y no se puede (ni se debe) saltear.
Chrome, Edge y la mayoría de las apps de Windows confían en ese almacén;
Firefox lo hace con ``security.enterprise_roots.enabled``.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import ssl
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID
from mitmproxy import certs

log = logging.getLogger("janus.ca")

BASENAME = "mitmproxy"  # nombre de archivo que espera mitmproxy (CONF_BASENAME)
CA_ORG = "Janus"
CA_CN = "Janus Interception CA"


def ensure_ca(confdir: Path) -> None:
    if not (confdir / f"{BASENAME}-ca.pem").exists():
        log.info("generando CA nueva en %s", confdir)
        certs.CertStore.create_store(confdir, BASENAME, 2048, organization=CA_ORG, cn=CA_CN)


def cert_path(confdir: Path) -> Path:
    return confdir / f"{BASENAME}-ca-cert.pem"


def load_cert(confdir: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(cert_path(confdir).read_bytes())


def der_bytes(confdir: Path) -> bytes:
    return load_cert(confdir).public_bytes(serialization.Encoding.DER)


def spki_sha256_b64(cert: x509.Certificate) -> str:
    spki = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")


def _colon_hex(data: bytes) -> str:
    return ":".join(f"{b:02X}" for b in data)


def _name_attr(cert: x509.Certificate, oid) -> str | None:
    attrs = cert.subject.get_attributes_for_oid(oid)
    return str(attrs[0].value) if attrs else None


def info(confdir: Path) -> dict:
    cert = load_cert(confdir)
    der = cert.public_bytes(serialization.Encoding.DER)
    return {
        "cn": _name_attr(cert, NameOID.COMMON_NAME),
        "org": _name_attr(cert, NameOID.ORGANIZATION_NAME),
        "serial": f"{cert.serial_number:X}",
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "sha1": _colon_hex(hashlib.sha1(der).digest()),  # noqa: S324 - huella, no seguridad
        "sha256": _colon_hex(hashlib.sha256(der).digest()),
        "spki_sha256": spki_sha256_b64(cert),
        "dir": str(confdir),
        "pem_path": str(cert_path(confdir)),
        "platform": sys.platform,
        "store_supported": sys.platform == "win32",
        "installed": is_installed(der),
    }


def export(confdir: Path, fmt: str) -> tuple[bytes, str, str]:
    """(bytes, media_type, nombre_archivo)."""
    cert = load_cert(confdir)
    if fmt == "der" or fmt == "cer":
        return cert.public_bytes(serialization.Encoding.DER), "application/pkix-cert", "janus-ca.cer"
    if fmt == "p12":
        return (confdir / f"{BASENAME}-ca-cert.p12").read_bytes(), "application/x-pkcs12", "janus-ca.p12"
    return cert.public_bytes(serialization.Encoding.PEM), "application/x-pem-file", "janus-ca.pem"


# --- almacén de Windows -------------------------------------------------------

def is_installed(der: bytes) -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        return any(cert == der for cert, _enc, _trust in ssl.enum_certificates("ROOT"))
    except OSError:
        log.warning("no se pudo leer el almacén ROOT", exc_info=True)
        return None


class StoreError(RuntimeError):
    pass


def _crypt32():
    import ctypes
    from ctypes import wintypes

    lib = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    lib.CertOpenSystemStoreW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    lib.CertOpenSystemStoreW.restype = ctypes.c_void_p
    lib.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    lib.CertCloseStore.restype = wintypes.BOOL
    lib.CertAddEncodedCertificateToStore.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_char_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    lib.CertAddEncodedCertificateToStore.restype = wintypes.BOOL
    lib.CertCreateCertificateContext.argtypes = [wintypes.DWORD, ctypes.c_char_p, wintypes.DWORD]
    lib.CertCreateCertificateContext.restype = ctypes.c_void_p
    lib.CertFindCertificateInStore.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
    ]
    lib.CertFindCertificateInStore.restype = ctypes.c_void_p
    lib.CertDeleteCertificateFromStore.argtypes = [ctypes.c_void_p]
    lib.CertDeleteCertificateFromStore.restype = wintypes.BOOL
    lib.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]
    lib.CertFreeCertificateContext.restype = wintypes.BOOL
    return ctypes, lib


X509_ASN_ENCODING = 0x00000001
PKCS_7_ASN_ENCODING = 0x00010000
CERT_STORE_ADD_REPLACE_EXISTING = 3
CERT_FIND_EXISTING = 0x000D0000
ERROR_CANCELLED = 1223


def _error_text(code: int) -> str:
    if code in (ERROR_CANCELLED, 0x800704C7 & 0xFFFFFFFF):
        return "cancelado: no se confirmó el aviso de seguridad de Windows"
    return f"Windows devolvió el error {code:#x}"


def install(confdir: Path, store_name: str = "ROOT") -> None:
    """Instala la CA en Raíz del usuario actual. Bloquea hasta que el usuario
    responde el diálogo de Windows (correr en un hilo)."""
    if sys.platform != "win32":
        raise StoreError("la instalación automática solo está disponible en Windows")
    der = der_bytes(confdir)
    ctypes, lib = _crypt32()
    store = lib.CertOpenSystemStoreW(None, store_name)
    if not store:
        raise StoreError(_error_text(ctypes.get_last_error()))
    try:
        ok = lib.CertAddEncodedCertificateToStore(
            store, X509_ASN_ENCODING | PKCS_7_ASN_ENCODING, der, len(der),
            CERT_STORE_ADD_REPLACE_EXISTING, None,
        )
        if not ok:
            raise StoreError(_error_text(ctypes.get_last_error()))
    finally:
        lib.CertCloseStore(store, 0)


def uninstall(confdir: Path, store_name: str = "ROOT") -> int:
    """Quita la CA de Raíz del usuario actual. Devuelve cuántas copias borró."""
    if sys.platform != "win32":
        raise StoreError("la desinstalación automática solo está disponible en Windows")
    der = der_bytes(confdir)
    ctypes, lib = _crypt32()
    enc = X509_ASN_ENCODING | PKCS_7_ASN_ENCODING
    target = lib.CertCreateCertificateContext(enc, der, len(der))
    if not target:
        raise StoreError(_error_text(ctypes.get_last_error()))
    store = lib.CertOpenSystemStoreW(None, store_name)
    if not store:
        lib.CertFreeCertificateContext(target)
        raise StoreError(_error_text(ctypes.get_last_error()))
    removed = 0
    try:
        while True:
            found = lib.CertFindCertificateInStore(store, enc, 0, CERT_FIND_EXISTING, target, None)
            if not found:
                break
            # Delete libera el contexto encontrado, haya éxito o no.
            if not lib.CertDeleteCertificateFromStore(found):
                code = ctypes.get_last_error()
                if removed:
                    break
                raise StoreError(_error_text(code))
            removed += 1
    finally:
        lib.CertFreeCertificateContext(target)
        lib.CertCloseStore(store, 0)
    return removed
