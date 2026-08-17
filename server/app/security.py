import ipaddress
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def validate_download_url(url: str) -> None:
    # Keep in sync with workspace_libs/download.py::validate_download_url
    # (the SDK side cannot import server.app, so the guard is duplicated).
    if not url:
        raise ValueError("Invalid URL: empty")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Invalid URL scheme: {parsed.scheme}")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("Invalid URL: missing hostname")
    hostname_lower = hostname.lower()
    if hostname_lower in {"localhost", "0.0.0.0"}:
        raise ValueError(f"Invalid URL: blocked host {hostname}")
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # Reject non-standard IP notations (octal/hex) that ipaddress doesn't parse
        # but underlying getaddrinfo may resolve to internal addresses.
        if all(c in "0123456789abcdefABCDEF.xX" for c in hostname):
            raise ValueError(f"Invalid URL: blocked IP-like host {hostname}") from None
        return
    if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
        raise ValueError(f"Invalid URL: blocked IP {hostname}")


def validate_package_filename(filename: str) -> str:
    if not filename:
        raise ValueError("Invalid filename: empty")
    if filename.startswith(".") or filename.startswith("-"):
        raise ValueError("Invalid filename: leading dot or hyphen")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise ValueError("Invalid filename: path traversal characters")
    return filename
