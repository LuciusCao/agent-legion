"""Claim-manifest injection of the object-storage artifact channel (#160 D12).

Split out of ``agent_artifacts.py`` for the file-size budget: that module
owns the legacy CAS staging, this one owns the presigned-URL overlay both
dispatch paths (agent enqueue, code claim rebuild) apply on top of it.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.services.job_artifact_objects import JobArtifactObjectStore

logger = logging.getLogger(__name__)


def inject_artifact_object_block(
    object_store: JobArtifactObjectStore | None, manifest: dict[str, Any]
) -> None:
    """Add the object-storage artifact channel to a claim manifest (#160 D12).

    When object storage is configured the Worker uploads declared outputs
    straight to S3 (``artifact_uploads``: name → presigned PUT) and downloads
    upstream artifacts straight from S3 (``input_artifacts`` value →
    ``{"url": presigned_get, "sha256": content_hash}``; ``storage_key`` never
    crosses the wire). Inputs without a ``job_artifacts`` row (never uploaded,
    legacy jobs) keep the legacy ``sha256:<hash>`` CAS form; Workers that see
    no ``artifact_uploads`` fall back to the legacy per-file POST channel.

    Degradation: any storage error leaves the manifest on the legacy channel
    (all-or-nothing — the new keys are built in locals and assigned once), so
    a storage outage never fails the dispatch/claim itself.
    """
    if object_store is None or not object_store.enabled:
        return
    job_id = str(manifest.get("job_id") or "")
    workspace_id = str(manifest.get("workspace_id") or "")
    try:
        uploads: dict[str, dict[str, str]] = {}
        for name in manifest.get("expected_outputs") or ():
            storage_key, url = object_store.presign_put(
                workspace_id=workspace_id, job_id=job_id, name=str(name)
            )
            uploads[str(name)] = {"storage_key": storage_key, "url": url}
        inputs: dict[str, Any] = {}
        for name, ref in dict(manifest.get("input_artifacts") or {}).items():
            row = object_store.lookup(job_id, str(name))
            if row is not None:
                inputs[str(name)] = {
                    "url": object_store.presign_get(row),
                    "sha256": str(row.get("content_hash") or ""),
                }
            else:
                inputs[str(name)] = ref
    except Exception:
        logger.warning(
            "artifact object-block injection failed for job %s; "
            "the Worker falls back to the legacy artifact channel",
            job_id,
            exc_info=True,
        )
        return
    manifest["artifact_uploads"] = uploads
    manifest["input_artifacts"] = inputs
