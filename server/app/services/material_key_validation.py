"""Validation for the material upload declaration (#1119, #1267).

The material storage key is
``{workspace_id}/{content_hash|material_id}/g{upload_generation}/{material_id}/{filename}``
(the trailing ``material_id`` segment, #1279, makes every key single-use
across row lifecycles);
the workspace segment is safe by contract (schema v62 id rules), while
``filename`` and ``content_hash`` come from the presign request and are pinned
here before any row write or presign. The same declaration is re-verified at
complete time against the stored object (``verify_stored_object``): size
always, sha256 when the client declared one.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from server.app.services.job_errors import JobServiceError
from server.app.storage import ObjectStorage


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


_HASH_CHUNK_BYTES = 1024 * 1024


def verify_stored_object(storage: ObjectStorage, row: dict[str, Any]) -> tuple[str | None, str]:
    """Return ``(failure detail or None, object ETag)``.

    complete() 的声明核验（事务外对象 I/O）：size 恒验，声明了 content_hash
    时重算 sha256 比对。同一 HEAD 的 ETag 随结果返回（无 hash 行在此止步，
    ETag 是它唯一的内容身份）：complete 成功时与 ready 同事务落库
    （materials.object_etag，schema v98，#1279），消费侧据以检出 complete
    后的同尺寸静默覆盖。对象缺失/核验失败时 ETag 为空串（不落库）。
    调用方负责落库前的锁内身份复查
    （jobs/queries/material_locked_rereads）。
    """
    head = storage.head_object(str(row["storage_key"]))
    if head is None:
        return f"Uploaded object is missing for material {row['id']}", ""
    declared_size = int(row["size_bytes"])
    if head.size_bytes != declared_size:
        return f"Uploaded object size {head.size_bytes} != declared size {declared_size}", ""
    content_hash = str(row["content_hash"])
    if not content_hash:
        return None, head.etag
    digest = hashlib.sha256()
    stream = storage.open_stream(str(row["storage_key"]))
    while chunk := stream.read(_HASH_CHUNK_BYTES):
        digest.update(chunk)
    actual = digest.hexdigest()
    if actual != content_hash:
        return f"Uploaded object sha256 {actual} != declared hash {content_hash}", ""
    return None, head.etag
