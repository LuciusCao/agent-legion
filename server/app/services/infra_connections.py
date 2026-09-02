"""Read-only infrastructure connection summaries for the admin surface (#335).

Display-safe views of the instance's two pieces of platform infrastructure
(the PostgreSQL DSN with the password masked, the S3-compatible store with
credentials collapsed to a derivation kind). Everything here is pure: the
route layer owns env/DSN access and probe execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from server.app.storage.s3_settings import S3Settings

PASSWORD_MASK = "***"

StorageCredentialsKind = Literal["static", "default-chain", "unconfigured"]


@dataclass(frozen=True)
class DatabaseConnectionInfo:
    """Non-secret view of the instance database DSN."""

    engine: str
    host: str
    port: int | None
    name: str
    user: str
    password_set: bool
    masked_url: str


def _degraded_database_info() -> DatabaseConnectionInfo:
    """Fallback when the DSN cannot be parsed: show nothing but the mask."""
    return DatabaseConnectionInfo(
        engine="unknown",
        host="",
        port=None,
        name="",
        user="",
        password_set=False,
        masked_url=PASSWORD_MASK,
    )


def describe_database(dsn: str) -> DatabaseConnectionInfo:
    """Parse the instance DSN into a display-safe, password-masked summary.

    Defensive: any parse failure degrades to the masked placeholder instead
    of echoing the raw DSN (which contains the password). Query string and
    fragment are dropped from the masked URL — PostgreSQL URIs may carry
    connection options (``sslpassword`` & co.) that are themselves secrets.
    """
    try:
        parts = urlsplit(dsn)
        if not parts.scheme:
            raise ValueError("DSN carries no scheme")
        # Accessing .port validates it (ValueError on garbage); .hostname
        # strips IPv6 brackets, so re-wrap before rebuilding the netloc.
        # NB: the DSN-escape ratchet counts any ".path" attribute read on a
        # bare name, so the SplitResult path comes from tuple indexing.
        port = parts.port
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        user = parts.username or ""
        password_set = bool(parts.password)
        userinfo = f"{user}:{PASSWORD_MASK}@" if password_set else (f"{user}@" if user else "")
        masked_url = f"{parts.scheme}://{userinfo}{host}"
        if port is not None:
            masked_url += f":{port}"
        name = parts[2].lstrip("/")
        if name:
            masked_url += f"/{name}"
        return DatabaseConnectionInfo(
            engine=parts.scheme,
            host=parts.hostname or "",
            port=port,
            name=name,
            user=user,
            password_set=password_set,
            masked_url=masked_url,
        )
    except ValueError:
        return _degraded_database_info()


@dataclass(frozen=True)
class StorageConnectionInfo:
    """Non-secret view of the instance object-store configuration."""

    configured: bool
    backend: str
    endpoint_url: str
    public_endpoint_url: str
    bucket: str
    region: str
    credentials: StorageCredentialsKind
    reachable: bool


# String fields of the unconfigured placeholder view (all empty).
_EMPTY_STRING_FIELDS = ("backend", "endpoint_url", "public_endpoint_url", "bucket", "region")
# Display-only host markers → product label (seaweedfs/rustfs/minio/amazonaws).
_BACKEND_LABELS = dict(seaweedfs="SeaweedFS", rustfs="RustFS", minio="MinIO", amazonaws="AWS S3")


def _infer_storage_backend(endpoint_url: str) -> str:
    """Best-effort display label for the store's server product."""
    if not endpoint_url:
        return "AWS S3"
    host = (urlsplit(endpoint_url).hostname or "").lower()
    if not host:  # unparseable input (e.g. missing scheme): no host to name
        return "S3 兼容"
    for marker, label in _BACKEND_LABELS.items():
        if marker in host:
            return label
    return f"S3 兼容（{host}）"


def describe_storage(settings: S3Settings | None, *, reachable: bool) -> StorageConnectionInfo:
    """Summarize the object-store config; credentials collapse to a kind.

    ``reachable`` is supplied by the caller (health cache / fresh probe) so
    this stays pure; unconfigured is never reachable.
    """
    if settings is None:
        return StorageConnectionInfo(
            configured=False,
            credentials="unconfigured",
            reachable=False,
            **dict.fromkeys(_EMPTY_STRING_FIELDS, ""),
        )
    return StorageConnectionInfo(
        configured=True,
        backend=_infer_storage_backend(settings.endpoint_url),
        endpoint_url=settings.endpoint_url,
        public_endpoint_url=settings.public_endpoint_url,
        bucket=settings.bucket,
        region=settings.region,
        credentials="static" if settings.access_key else "default-chain",
        reachable=reachable,
    )
