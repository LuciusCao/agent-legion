"""SSRF guard for user-influenced download URLs (single copy, #200).

``validate_download_url`` used to live as two "keep in sync" copies in
``server/app/security.py`` and ``workspace_libs/download.py``. It lives here
instead so both sides import one implementation: this package is the only
place both can reach — the server imports ``workspace_libs`` freely, while
the node SDK side can never import ``server.app`` and must stay
import-self-contained (the code bundle ships only the ``workspace_libs``
snapshot, so a ``shared/`` home would not resolve inside the sandbox or on
a Worker).

Standard library only (``ipaddress`` + ``urllib.parse``): same layering rule
as the rest of ``workspace_libs`` minus ``requests``.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def validate_download_url(url: str) -> None:
    """SSRF guard for user-influenced download URLs (scheme/host/IP checks).

    Known limitation: a plain hostname is resolved only at request time, so a
    DNS name that later resolves to an internal address (DNS rebinding) is
    not caught here; ``workspace_libs.download.download_file`` additionally
    refuses redirects so a guarded URL cannot hop to an internal target via
    3xx — callers on the server side must apply the same refusal.
    """
    if not url:
        raise ValueError("Invalid URL: empty")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Invalid URL scheme: {parsed.scheme}")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("Invalid URL: missing hostname")
    if hostname.lower() in {"localhost", "0.0.0.0"}:
        raise ValueError(f"Invalid URL: blocked host {hostname}")
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # Reject non-standard IP notations (octal/hex) that ipaddress doesn't
        # parse but underlying getaddrinfo may resolve to internal addresses.
        if all(c in "0123456789abcdefABCDEF.xX" for c in hostname):
            raise ValueError(f"Invalid URL: blocked IP-like host {hostname}") from None
        return
    if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
        raise ValueError(f"Invalid URL: blocked IP {hostname}")
