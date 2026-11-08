"""The one place Stealth makes a TLS context.

Antivirus such as Norton's Web/Mail Shield, Avast and many work networks inspect HTTPS and email
by re-signing every certificate with their own root, which they add to the operating system's
trust store. A bundled list of public roots (certifi) doesn't include it, so connections fail.

`truststore` asks the operating system to verify each certificate the way browsers do (Windows
CryptoAPI, macOS Security framework, the system store on Linux), so anything the person's own
browser trusts, Stealth trusts too, and nothing else. Hostnames are still checked.
"""

from __future__ import annotations

import ssl


def context() -> ssl.SSLContext:
    """A client context that verifies certificates against the operating system's trust store."""
    try:
        import truststore
    except ImportError:  # pragma: no cover - a dependency; the stdlib default is the fallback
        return ssl.create_default_context()
    ctx: ssl.SSLContext = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def presented_issuer(host: str, port: int, timeout: float = 10) -> str | None:
    """Who signed the certificate this server presents, without trusting it. Diagnosis only.

    Used after verification has already failed, to tell the person *why*: if their antivirus
    signed it, the fix is in the antivirus. Nothing is sent over this connection.
    """
    import socket

    from cryptography import x509
    from cryptography.x509.oid import NameOID

    probe = ssl.create_default_context()
    probe.check_hostname = False
    probe.verify_mode = ssl.CERT_NONE
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as raw,
            probe.wrap_socket(raw, server_hostname=host) as sock,
        ):
            der = sock.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError):
        return None
    if not der:
        return None
    names = x509.load_der_x509_certificate(der).issuer
    parts = names.get_attributes_for_oid(NameOID.ORGANIZATION_NAME) or names.get_attributes_for_oid(
        NameOID.COMMON_NAME
    )
    return str(parts[0].value) if parts else None


# Names that appear in the issuer when antivirus re-signs a connection, and what to call it.
ANTIVIRUS = {
    "norton": "Norton",
    "avast": "Avast",
    "avg": "AVG",
    "kaspersky": "Kaspersky",
    "eset": "ESET",
    "bitdefender": "Bitdefender",
    "mcafee": "McAfee",
}


def antivirus_named(issuer: str | None) -> str | None:
    lowered = (issuer or "").lower()
    return next((name for key, name in ANTIVIRUS.items() if key in lowered), None)
