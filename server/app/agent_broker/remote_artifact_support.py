"""Mechanics helpers for the Worker-direct S3 artifact channel (#160 D12).

Split for the file-size budget: ``artifact_object_block`` (claim path) and
``remote_artifacts`` (result-commit path) stay the orchestrators, this module
owns the per-artifact mechanics both share — presign TTL policy, the presign
loop bodies, and the staging-object stream download/digest helpers.

Presign TTL derives from the node's resolved ``timeout_seconds``
(``max(3600, timeout + 900)``): a fixed 3600s TTL would strand expired PUT
URLs (403 on upload) for long-timeout nodes.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path, PurePosixPath
from typing import Any

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
    """Copy a verified staging object onto the authority key (server-side).

    Returns the authority key. Callers register the row and then delete
    the staging object best-effort (orphans are lifecycle's backstop).
    """
    assert store.storage is not None
    authority_key = artifact_storage_key(workspace_id, job_id, name)
    store.storage.copy_object(storage_key, authority_key)
    return authority_key


def discard_staging(store: JobArtifactObjectStore, storage_key: str) -> None:
    """Best-effort staging-object cleanup after promotion."""
    if store.storage is None:
        return
    try:
        store.storage.delete_object(storage_key)
    except Exception:
        logger.warning("failed to delete staging object %s", storage_key, exc_info=True)


def presign_expiry_seconds(manifest: dict[str, Any]) -> int:
    """Presign TTL derived from the node's resolved ``timeout_seconds``.

    kind='code' manifests carry it top-level, agent manifests nest it under
    ``execution``; missing/unparseable values fall back to the 600s default.
    """
    timeout: Any = manifest.get("timeout_seconds")
    if timeout is None:
        timeout = (manifest.get("execution") or {}).get("timeout_seconds")
    try:
        timeout_seconds = int(timeout) if timeout is not None else _DEFAULT_NODE_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout_seconds = _DEFAULT_NODE_TIMEOUT_SECONDS
    return max(DEFAULT_PRESIGN_EXPIRY_SECONDS, timeout_seconds + _PRESIGN_EXPIRY_SLACK_SECONDS)


def build_artifact_uploads(
    store: JobArtifactObjectStore, manifest: dict[str, Any]
) -> dict[str, dict[str, str]]:
    """``name → {storage_key, presigned PUT url}`` on per-execution staging keys.

    The Host promotes onto the authority key server-side after verification,
    so a stale Worker's late PUT can never overwrite the authority copy.
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
        url = store.storage.presign_put(storage_key, 0, expires)
        uploads[str(name)] = {"storage_key": storage_key, "url": url}
    return uploads


def upgrade_input_artifacts(
    store: JobArtifactObjectStore, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Upgrade staged inputs with a ``job_artifacts`` row to presigned GETs.

    Value shape ``{"url": presigned_get, "sha256": content_hash}``;
    ``storage_key`` never crosses the wire. Inputs without a row (never
    uploaded, legacy jobs) keep the legacy ``sha256:<hash>`` CAS form.
    """
    assert store.storage is not None
    expires = presign_expiry_seconds(manifest)
    job_id = str(manifest.get("job_id") or "")
    inputs: dict[str, Any] = {}
    for name, ref in dict(manifest.get("input_artifacts") or {}).items():
        row = store.lookup(job_id, str(name))
        if row is not None:
            inputs[str(name)] = {
                "url": store.storage.presign_get(str(row["storage_key"]), expires),
                "sha256": str(row.get("content_hash") or ""),
            }
        else:
            inputs[str(name)] = ref
    return inputs


def download_remote_artifact(
    store: JobArtifactObjectStore, staging_dir: Path, name: str, ref: Any
) -> Path:
    """Stream one verified staging object into the staging dir (hash-checked)."""
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
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
            handle.write(chunk)
    declared = str(ref.get("content_hash") or "")
    if declared and digest.hexdigest() != declared:
        raise ValueError(f"artifact content hash mismatch: {name!r}")
    return target


def verify_remote_digest(store: JobArtifactObjectStore, name: str, ref: Any) -> str:
    """Digest-only stream of a verified staging object (cancelled runs).

    Nothing is written to disk. A Worker-reported hash must match the
    computed digest (mismatch fails the whole batch); an empty report
    registers the computed value, so the registered hash always comes from
    Host-verified content.
    """
    digest = hashlib.sha256()
    with store.open_stream({"storage_key": str(ref["storage_key"])}) as stream:
        while chunk := stream.read(1 << 20):
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
