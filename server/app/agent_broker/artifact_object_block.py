"""Claim-manifest injection of the object-storage artifact channel (#160 D12).

Split out of ``agent_artifacts.py`` for the file-size budget: that module
owns the legacy CAS staging, this one owns the presigned-URL overlay both
dispatch paths (agent enqueue, code claim rebuild) apply on top of it. The
per-artifact mechanics (presign TTL policy, presign loops, staging streams)
live in ``remote_artifact_support.py``; this module stays the orchestrator.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.agent_broker.remote_artifact_support import (
    build_artifact_uploads,
    upgrade_input_artifacts,
)
from server.app.services.job_artifact_objects import JobArtifactObjectStore

logger = logging.getLogger(__name__)


def inject_artifact_object_block(
    object_store: JobArtifactObjectStore | None, manifest: dict[str, Any]
) -> None:
    """Add the object-storage artifact channel to a claim manifest (#160 D12).

    When object storage is configured the Worker uploads declared outputs
    straight to S3 (``artifact_uploads``: name → presigned PUT on a
    per-execution staging key) and downloads upstream artifacts straight from
    S3 (``input_artifacts`` value → ``{"url": presigned_get, "sha256":
    content_hash}``). Workers that see no ``artifact_uploads`` fall back to
    the legacy per-file POST channel. Presign TTLs derive from the node's
    resolved ``timeout_seconds`` so long-timeout nodes never hit an expired
    URL mid-run.

    Degradation: any storage error leaves the manifest on the legacy channel
    (all-or-nothing — the new keys are built in locals and assigned once), so
    a storage outage never fails the dispatch/claim itself.
    """
    if object_store is None or not object_store.enabled:
        return
    if not str(manifest.get("execution_id") or ""):
        # Staging keys are per-execution; a manifest without one cannot use
        # the object channel (the Host would reject the reported keys).
        return
    try:
        uploads = build_artifact_uploads(object_store, manifest)
        inputs = upgrade_input_artifacts(object_store, manifest)
    except Exception:
        logger.warning(
            "artifact object-block injection failed for job %s; "
            "the Worker falls back to the legacy artifact channel",
            manifest.get("job_id") or "",
            exc_info=True,
        )
        return
    manifest["artifact_uploads"] = uploads
    manifest["input_artifacts"] = inputs
