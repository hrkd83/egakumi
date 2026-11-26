"""Create verified client TLS without changing process-wide SSL defaults.

Windows clients use the operating system's certificate chain validation and
trust store. Other hosts retain Python's default certificate validation.
"""
from __future__ import annotations

import ssl
import sys


ERROR = "Verified client TLS is unavailable"


def verified_tls_context():
    """Require server certificates and hostnames with the platform validator.

    Missing native support is an error rather than a switch to another trust
    policy. Neither certificate stores nor SSL verification flags are changed.
    """
    try:
        if sys.platform == "win32":
            import truststore

            context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        else:
            context = ssl.create_default_context()
        if context.check_hostname is not True or context.verify_mode != ssl.CERT_REQUIRED:
            raise RuntimeError(ERROR)
        return context
    except Exception:
        raise RuntimeError(ERROR) from None
