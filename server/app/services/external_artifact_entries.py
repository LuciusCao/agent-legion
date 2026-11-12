"""Entry construction and presign policy for external artifact listings.

The unified manifest/local merge stays in ``external_artifact_access``; this
module only turns its already-merged rows and names into wire entries.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.services.job_artifact_gzip import is_gzip_key
from server.app.services.job_artifact_media import (
    attachment_disposition,
    raw_disposition_type,
    raw_media_type,
)


def presigned_headers(name: str, gzipped: bool) -> dict[str, str]:
    """Match the raw endpoint's media, disposition and gzip header policy."""
    headers = {"ResponseContentType": raw_media_type(name)}
    if raw_disposition_type(name) == "attachment":
        headers["ResponseContentDisposition"] = attachment_disposition(name)
    if gzipped:
        headers["ResponseContentEncoding"] = "gzip"
    return headers


def presigned_object_entry(row: dict[str, Any], store: Any, ttl: int) -> dict[str, Any]:
    """Build one object-backed entry and mint its fresh presigned GET URL."""
    name = str(row["name"])
    size = row.get("size_bytes")
    storage_key = str(row["storage_key"])
    gzipped = is_gzip_key(storage_key)
    download_url = store.storage.presign_get(
        storage_key, expires_seconds=ttl, response_headers=presigned_headers(name, gzipped)
    )
    # An upper bound, not a guarantee: signing credentials may expire first.
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
    return {
        "name": name,
        "storage": "object",
        "node_key": str(row.get("node_key") or ""),
        "size_bytes": int(size) if isinstance(size, int) else None,
        "content_hash": str(row.get("content_hash") or ""),
        "uploaded_at": row.get("uploaded_at"),
        "media_type": raw_media_type(name),
        "download_url": download_url,
        "expires_at": expires_at,
        "content_encoding": "gzip" if gzipped else "",
    }


def local_artifact_entry(name: str) -> dict[str, Any]:
    """Build a legacy local-only entry; there is no object to presign."""
    return {
        "name": name,
        "storage": "local",
        "node_key": "",
        "size_bytes": None,
        "content_hash": "",
        "uploaded_at": None,
        "media_type": raw_media_type(name),
        "download_url": None,
        "expires_at": None,
        "content_encoding": "",
    }
