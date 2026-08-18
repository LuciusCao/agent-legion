"""Secure file download for workflow code nodes (framework layer).

``validate_download_url`` is the SSRF guard for user-influenced URLs and
``download_file`` streams a guarded URL to disk with a content-type gate.
Split from ``http_client`` for the size budget; same layering rule: standard
library + ``requests`` only, never import ``server.app.*``.
"""

from __future__ import annotations

import ipaddress
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

_ALLOWED_SCHEMES = {"http", "https"}

# Hard ceiling on a single download: an unbounded stream from a malicious or
# broken endpoint could fill the disk hosting the job directory.
DEFAULT_MAX_BYTES = 10 * 1024**3


def validate_download_url(url: str) -> None:
    """SSRF guard for user-influenced download URLs (scheme/host/IP checks).

    Keep in sync with ``server/app/security.py::validate_download_url`` (this
    module must not import ``server.app.*``, so the guard is duplicated).
    Known limitation (inherited verbatim): a plain hostname is resolved only
    at request time, so a DNS name that later resolves to an internal address
    (DNS rebinding) is not caught here; ``download_file`` additionally refuses
    redirects so a guarded URL cannot hop to an internal target via 3xx.
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


def download_file(
    url: str,
    output_path: Path,
    *,
    allowed_content_type_prefixes: tuple[str, ...] = ("video/", "application/octet-stream"),
    expected: str = "video",
    timeout: int = 120,
    chunk_size: int = 1024 * 1024,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> None:
    """Stream *url* to *output_path* with an SSRF guard and content-type gate.

    An existing non-empty output short-circuits (retry-safe). The stream
    lands in a unique ``<name>.<uuid>.tmp`` sibling (concurrent downloads
    to the same output never share and truncate one tmp; the last rename
    wins the final path) and is atomically renamed into place only on
    completion, so even a SIGKILL mid-stream (executor timeout) leaves at
    most a ``.tmp`` file behind — a retry never mistakes a truncated
    artifact for a finished one. Streams larger than *max_bytes* fail and
    clean up instead of filling the job-dir disk.
    """
    validate_download_url(url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size > 0:
        return
    # Redirects are refused: following them would bypass validate_download_url
    # by hopping to an internal address.
    with requests.get(url, stream=True, timeout=timeout, allow_redirects=False) as response:
        if 300 <= response.status_code < 400:
            raise ValueError(
                f"Unexpected redirect (HTTP {response.status_code}) downloading {output_path.name}"
            )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if content_type and not content_type.startswith(allowed_content_type_prefixes):
            raise ValueError(f"Expected {expected} content, got {content_type}")
        tmp_path = output_path.with_name(f"{output_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            written = 0
            with tmp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValueError(f"download exceeds the {max_bytes}-byte limit")
                        handle.write(chunk)
            tmp_path.replace(output_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
