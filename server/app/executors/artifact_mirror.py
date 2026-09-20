"""Best-effort D12 artifact upload for the local code executor.

Split out of ``code.py`` for the file-size budget: the executor keeps the
lazy storage/DSN plumbing, this module owns the upload loop. A storage
outage never fails the node — the local copy stays and the maintenance
reconciler re-uploads later (EXEC-ARTIFACT-STORE-001).

EXEC-GENERATION-001 (#645 P2-b): the sandboxed child is cooperatively
cancelled, so an execution whose heartbeat was lost can still run to
completion and reach this upload AFTER the lease expired or a reset bumped
the generation — an unguarded upload would overwrite authority keys and
resurrect manifest rows the reset removed. With ``lease_id`` the loop first
runs the artifact write gate (``artifact_write_gate_open``): a stale/orphan
upload is skipped wholesale (bytes stay local, nothing is registered). The
per-row registration repeats the check inside its own transaction, so a
reset landing mid-loop cannot register either. A gate check that itself
fails (DB unreachable) skips the upload too — fail-closed, since the mirror
path must never introduce a new failure mode for the node.
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
    lease_id: str = "",
) -> None:
    """Upload produced artifacts best-effort; per-file failures are logged.

    Names in ``skip`` are already in object storage (e.g. uploaded directly
    by a Worker and registered by the completion path) and are not mirrored.
    ``lease_id`` arms the EXEC-GENERATION-001 write gate (#645 P2-b): empty
    keeps the legacy ungated behavior for callers without a lease context.
    """
    if store is None:
        return
    if lease_id:
        try:
            gate_open = store.artifact_write_gate_open(job_id=job_id, lease_id=lease_id)
        except Exception:
            # #204 broad-except audit: the gate's DB read (pool connect,
            # advisory lock, lease probe) sits on the best-effort mirror path
            # (EXEC-ARTIFACT-STORE-001) and must never introduce a new failure
            # mode for the node. The outcome space is the psycopg/pool surface
            # (unreachable/DSN-misconfigured DB), with no business family to
            # narrow to. Fail-closed on purpose: an unreadable gate cannot
            # prove the lease still owns the current generation, so writing
            # old-epoch bytes would risk exactly the P2-b pollution the gate
            # exists to prevent; skipping is safe because the local copy stays
            # and the reconciler re-uploads later. exc_info keeps the cause.
            logger.warning(
                "artifact write gate unreadable for job %s node %s; upload skipped",
                job_id,
                node_key,
                exc_info=True,
            )
            return
        if not gate_open:
            logger.info(
                "artifact upload skipped for job %s node %s: "
                "lease %s is no longer the current generation's active lease",
                job_id,
                node_key,
                lease_id,
            )
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
                lease_id=lease_id,
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
