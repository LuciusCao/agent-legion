"""Best-effort D12 artifact upload for the local code executor.

Split out of ``code.py`` for the file-size budget: the executor keeps the
lazy storage/DSN plumbing, this module owns the upload loop. A storage
outage never fails the node — the local copy stays and the maintenance
reconciler re-uploads later (EXEC-ARTIFACT-STORE-001).
"""

from __future__ import annotations

import logging
from pathlib import Path

from server.app.db.connection import DatabaseDsn
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)


def build_artifact_object_store(
    storage: ObjectStorage | None, database_dsn: DatabaseDsn | None
) -> JobArtifactObjectStore | None:
    """Artifact upload service (D12); None without storage or a DB handle."""
    if storage is None or database_dsn is None:
        return None
    return JobArtifactObjectStore(database_dsn, storage)


def upload_produced_artifacts(
    store: JobArtifactObjectStore | None,
    *,
    workspace_id: str,
    job_id: str,
    node_key: str,
    job_dir: Path,
    produced: tuple[str, ...],
    skip: frozenset[str] | set[str] = frozenset(),
) -> None:
    """Upload produced artifacts best-effort; per-file failures are logged.

    Names in ``skip`` are already in object storage (e.g. uploaded directly
    by a Worker and registered by the completion path) and are not mirrored.
    """
    if store is None:
        return
    for name in produced:
        if name in skip:
            continue
        try:
            store.upload(
                workspace_id=workspace_id,
                job_id=job_id,
                node_key=node_key,
                name=name,
                local_path=job_dir / name,
            )
        except Exception:
            # #204 broad-except audit: deliberate per-file best-effort mirror
            # (EXEC-ARTIFACT-STORE-001). One artifact's failure must neither
            # fail the node (the local copy is the node's real output and the
            # maintenance reconciler re-uploads later) nor skip the remaining
            # artifacts in the loop. The outcome space is genuinely mixed —
            # the S3 outage surface after the store's own bounded retries AND
            # the manifest upsert's DB write — with no single business family
            # to narrow to; exc_info keeps the per-file root cause visible.
            logger.warning(
                "artifact upload failed for job %s node %s artifact %s; "
                "local copy kept for the reconciler",
                job_id,
                node_key,
                name,
                exc_info=True,
            )
