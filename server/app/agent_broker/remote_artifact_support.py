"""Mechanics helpers for the Worker-direct S3 artifact channel (#160 D12).

``artifact_object_block`` (claim path) and ``remote_artifacts`` (result-commit
path) stay the orchestrators; this module owns the shared per-artifact
mechanics — presign TTL policy (``max(3600, timeout + 900)`` so long-timeout
nodes never hit an expired URL), presign loops, staging download/digest.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path, PurePosixPath
from typing import Any

from server.app.services.job_artifact_gzip import GZIP_SUFFIX, is_gzip_key, read_bounded
from server.app.services.job_artifact_objects import (
    DEFAULT_PRESIGN_EXPIRY_SECONDS,
    JobArtifactObjectStore,
    artifact_staging_key,
    artifact_storage_key,
)

logger = logging.getLogger(__name__)

# Node timeout fallback when the manifest carries none (mirrors the platform
# default) plus slack for the result report after the node exits.
_DEFAULT_NODE_TIMEOUT_SECONDS = 600
_PRESIGN_EXPIRY_SLACK_SECONDS = 900


def promote_remote(
    store: JobArtifactObjectStore,
    *,
    workspace_id: str,
    job_id: str,
    name: str,
    storage_key: str,
) -> str:
    """Copy a verified staging object onto the authority key (server-side);
    returns the authority key. The ``.gz`` form marker (#338) carries over:
    server-side copy preserves bytes, so the key keeps the staging suffix.
    """
    assert store.storage is not None
    authority_key = artifact_storage_key(workspace_id, job_id, name)
    authority_key += GZIP_SUFFIX if is_gzip_key(storage_key) else ""
    store.storage.copy_object(storage_key, authority_key)
    return authority_key


def discard_staging(store: JobArtifactObjectStore, storage_key: str) -> None:
    """Best-effort staging-object cleanup after promotion."""
    if store.storage is None:
        return
    try:
        store.storage.delete_object(storage_key)
    except Exception:
        # #204 broad-except audit: deliberate best-effort staging cleanup.
        # This runs in the promote success path AND in promote_all's finally
        # after a failure — either way the caller's outcome must not change:
        # an orphaned staging object is explicitly lifecycle's backstop
        # (documented across this module family), so a storage error during
        # its deletion is only worth a warning with the traceback. The
        # storage layer is third-party surface (botocore); no business
        # exception family could enumerate it.
        logger.warning("failed to delete staging object %s", storage_key, exc_info=True)


def presign_expiry_seconds(manifest: dict[str, Any]) -> int:
    """Presign TTL from the node's resolved ``timeout_seconds`` (top-level for
    code manifests, nested under ``execution`` for agent; default 600s)."""
    timeout: Any = manifest.get("timeout_seconds")
    if timeout is None:
        timeout = (manifest.get("execution") or {}).get("timeout_seconds")
    try:
        timeout_seconds = int(timeout) if timeout is not None else _DEFAULT_NODE_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout_seconds = _DEFAULT_NODE_TIMEOUT_SECONDS
    return max(DEFAULT_PRESIGN_EXPIRY_SECONDS, timeout_seconds + _PRESIGN_EXPIRY_SLACK_SECONDS)


def build_artifact_uploads(
    store: JobArtifactObjectStore, manifest: dict[str, Any], *, gzip_uploads: bool = False
) -> dict[str, dict[str, str]]:
    """``name → {storage_key, presigned PUT url}`` on per-execution staging keys
    (the Host promotes server-side after verification). ``gzip_uploads`` (#338,
    v4+ Workers): the staging key carries the ``.gz`` form marker.
    """
    assert store.storage is not None
    expires = presign_expiry_seconds(manifest)
    uploads: dict[str, dict[str, str]] = {}
    for name in manifest.get("expected_outputs") or ():
        storage_key = artifact_staging_key(
            str(manifest.get("workspace_id") or ""),
            str(manifest.get("job_id") or ""),
            str(manifest.get("execution_id") or ""),
            str(name),
        )
        storage_key += GZIP_SUFFIX if gzip_uploads else ""
        url = store.storage.presign_put(storage_key, 0, expires)
        uploads[str(name)] = {"storage_key": storage_key, "url": url}
    return uploads


def upgrade_input_artifacts(
    store: JobArtifactObjectStore, manifest: dict[str, Any], *, gzip_capable: bool = False
) -> dict[str, Any]:
    """Upgrade staged inputs with a ``job_artifacts`` row to presigned GETs
    (value ``{"url", "sha256"}``; no row keeps the legacy CAS form). #338: a
    ``.gz`` row adds ``content_encoding: "gzip"`` for v4+ Workers; for older
    Workers it stays CAS, so a mixed fleet never mismatches the stored form.
    """
    assert store.storage is not None
    expires = presign_expiry_seconds(manifest)
    job_id = str(manifest.get("job_id") or "")
    inputs: dict[str, Any] = {}
    for name, ref in dict(manifest.get("input_artifacts") or {}).items():
        row = store.lookup(job_id, str(name))
        if row is not None:
            storage_key = str(row["storage_key"])
            if is_gzip_key(storage_key) and not gzip_capable:
                # 旧协议 worker：.gz 对象不升级为 presigned GET，保留 CAS
                # 通道（dispatch 时从本地未压缩副本 staging，双形态读不受影响）。
                inputs[str(name)] = ref
                continue
            upgraded = {
                "url": store.storage.presign_get(storage_key, expires),
                "sha256": str(row.get("content_hash") or ""),
            }
            if is_gzip_key(storage_key):
                upgraded["content_encoding"] = "gzip"
            inputs[str(name)] = upgraded
        else:
            inputs[str(name)] = ref
    return inputs


def download_remote_artifact(
    store: JobArtifactObjectStore,
    staging_dir: Path,
    name: str,
    ref: Any,
    max_size_bytes: int | None = None,
) -> tuple[Path, str]:
    """Stream one verified staging object into the staging dir (hash-checked,
    ``max_size_bytes``-capped on decompressed bytes, #338); returns (path, hash).
    """
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe expected output name: {name!r}")
    target = staging_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with (
        store.open_stream({"storage_key": str(ref["storage_key"])}) as stream,
        target.open("wb") as handle,
    ):
        for chunk in read_bounded(stream, max_size_bytes, name=name):
            digest.update(chunk)
            handle.write(chunk)
    declared = str(ref.get("content_hash") or "")
    if declared and digest.hexdigest() != declared:
        raise ValueError(f"artifact content hash mismatch: {name!r}")
    return target, declared or digest.hexdigest()


def verify_remote_digest(
    store: JobArtifactObjectStore, name: str, ref: Any, max_size_bytes: int | None = None
) -> str:
    """Digest-only stream of a verified staging object (cancelled runs); a
    Worker-reported hash must match, an empty one registers the computed value.
    ``max_size_bytes`` caps decompressed bytes mid-stream (#338)."""
    digest = hashlib.sha256()
    with store.open_stream({"storage_key": str(ref["storage_key"])}) as stream:
        for chunk in read_bounded(stream, max_size_bytes, name=name):
            digest.update(chunk)
    declared = str(ref.get("content_hash") or "")
    if declared and digest.hexdigest() != declared:
        raise ValueError(f"artifact content hash mismatch: {name!r}")
    return declared or digest.hexdigest()


def build_manifest_rows(
    workspace_id: str,
    job_id: str,
    node_key: str,
    remote: dict[str, Any],
    authority_keys: dict[str, str],
    content_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    """``record_remote_many`` rows for already-verified refs (apply phase)."""
    return [
        {
            "workspace_id": workspace_id,
            "job_id": job_id,
            "node_key": node_key,
            "name": name,
            "storage_key": authority_keys[name],
            "size_bytes": int(ref["size_bytes"]),
            "content_hash": content_hashes[name],
        }
        for name, ref in remote.items()
    ]
