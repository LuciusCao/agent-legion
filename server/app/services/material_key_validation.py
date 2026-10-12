"""Validation for the request-controlled ingredients of a material object key (#1119, #1267).

The material storage key is ``{workspace_id}/{content_hash|material_id}/{filename}``;
the workspace segment is safe by contract (schema v62 id rules), while
``filename`` and ``content_hash`` come from the presign request and are pinned
here before any row write or presign.
"""

from __future__ import annotations

import re

from server.app.services.job_errors import JobServiceError


class InvalidMaterialFilenameError(JobServiceError):
    """Filename cannot be used in an object key (routes map to 422, #1119)."""


class InvalidMaterialContentHashError(JobServiceError):
    """Declared content_hash is not a lowercase sha256 hex digest (422, #1267)."""


def invalid_material_filename_reason(filename: str) -> str | None:
    """Why ``filename`` cannot round-trip as an object key; None when valid (#1119).

    A key ending in ``/`` is stored by SeaweedFS as a directory (its bytes are
    dropped) and ``aws s3 sync`` cannot place it on a filesystem; ``.``/``..``
    and empty segments normalize away on download (``a//b`` and ``a/b`` land on
    the same file and clobber each other in a backup); backslashes are
    re-interpreted as separators by Windows-shaped consumers; control
    characters (newline) break the backup runbook's line-based marker listing.
    Same discipline as ``valid_artifact_name`` (job_artifact_objects), loosened
    only to allow non-empty interior ``/`` segments (material names are
    multi-segment relative paths, not single segments), plus the
    control-character ban adopted from ``validate_member_path``
    (material_bundles).
    """
    if filename.endswith("/"):
        return (
            f"filename must not end with '/': {filename!r} — object keys ending "
            "in '/' are stored as directories and cannot be downloaded"
        )
    if "\\" in filename:
        return f"filename must not contain backslashes: {filename!r}"
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in filename):
        return f"filename must not contain control characters: {filename!r}"
    segments = filename.split("/")
    if any(segment in {".", ".."} for segment in segments):
        return f"filename must not contain '.' or '..' path segments: {filename!r}"
    if not all(segments):
        return f"filename must not contain empty path segments: {filename!r}"
    return None


def validate_material_filename(filename: str) -> None:
    reason = invalid_material_filename_reason(filename)
    if reason is not None:
        raise InvalidMaterialFilenameError(reason)


_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")


def validate_material_content_hash(content_hash: str) -> None:
    """Pin the declared hash to sha256 hex (#1267 codex P1).

    ``content_hash`` is request-controlled and becomes both the dedup identity
    and a storage-key segment — an unconstrained value like ``../other`` would
    be signed into the key verbatim. complete() compares it against the
    recomputed sha256 hex anyway, so nothing but 64 lowercase hex chars can
    ever become ready.
    """
    if not _SHA256_HEX_RE.fullmatch(content_hash):
        raise InvalidMaterialContentHashError(
            "content_hash must be a lowercase sha256 hex digest (64 hex chars): "
            f"{content_hash[:80]!r}"
        )
