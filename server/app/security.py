import ipaddress
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def validate_download_url(url: str) -> None:
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
