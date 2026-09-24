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
)

logger = logging.getLogger(__name__)

# Node timeout fallback when the manifest carries none (mirrors the platform
# default) plus slack for the result report after the node exits.
_DEFAULT_NODE_TIMEOUT_SECONDS = 600
_PRESIGN_EXPIRY_SLACK_SECONDS = 900

# #356 spot-check sampling modulus (as a percent; see `spot_check_selected`).
# Issue plan B: 1–5% of the trust-reported (non-download-verified) artifacts
# still get a Host-side digest stream; the default sits in the middle of that
# band. 0 (config kill-switch) selects nobody — full trust; 100 selects
# everybody — the pre-#356 always-verify behavior.
DEFAULT_SPOT_CHECK_PERCENT = 3
_SPOT_SALT = "agent-legion-spot-check-v1"


def spot_check_selected(name: str, ref: Any, percent: int) -> bool:
    """Deterministic spot-check selection for one artifact (#356 plan B).

    The pick derives from the (name, storage_key, size) triple via a stable
    hash — the same artifact is always in or out across retries (a flaky
    selection would make the sampling's failure semantics unreproducible),
    and a Worker cannot steer itself out of the sample without changing the
    content it reports. ``percent`` <= 0 selects nobody; >= 100 everybody.
    """
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    key = f"{_SPOT_SALT}:{name}:{ref['storage_key']}:{ref.get('size_bytes')}"
    bucket = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % 10_000
    return bucket < percent * 100


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
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
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
    store: JobArtifactObjectStore,
    name: str,
    ref: Any,
    max_size_bytes: int | None = None,
    *,
    spot_check_percent: int = DEFAULT_SPOT_CHECK_PERCENT,
) -> str:
    """Digest-only stream of a verified staging object; a Worker-reported
    hash must match, an empty one registers the computed value.
    ``max_size_bytes`` caps decompressed bytes mid-stream (#338).

    #356 plan B: when the ref carries a Worker-reported hash, the digest
    stream runs only for the deterministic spot-check sample
    (``spot_check_selected``); the unsampled majority trusts the reported
    hash — the HEAD size check in phase 1 already bounded the object. An
    EMPTY reported hash still streams unconditionally: there is nothing to
    trust, the manifest row needs a Host-computed digest. Sampling off (0)
    = the pre-#356 always-verify; 100 = always (the kill-switch inverse).

    #356 review P1: a ``.gz`` ref NEVER takes the trust shortcut — its HEAD
    size check bounds the COMPRESSED bytes only, so an unsampled gzip bomb
    would register (and later read back) unbounded decompressed content.
    The ``read_bounded`` decompression cap is a security property of the
    stream itself, not part of the hash comparison; the spot check may skip
    the digest match, never the cap. Bare keys keep the shortcut: for raw
    objects the HEAD size IS the byte bound."""
    declared = str(ref.get("content_hash") or "")
    gzip_ref = is_gzip_key(str(ref["storage_key"]))
    if declared and not gzip_ref and not spot_check_selected(name, ref, spot_check_percent):
        return declared
    digest = hashlib.sha256()
    with store.open_stream({"storage_key": str(ref["storage_key"])}) as stream:
        for chunk in read_bounded(stream, max_size_bytes, name=name):
            digest.update(chunk)
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
