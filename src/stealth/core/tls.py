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
