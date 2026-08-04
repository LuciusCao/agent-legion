"""Artifact reference garbage collection for job deletion (GC baseline)."""

from __future__ import annotations

import logging

from server.app.services.artifact_store import ArtifactStore

logger = logging.getLogger(__name__)


def read_artifact_candidates(store: ArtifactStore | None, job_id: str) -> list[str]:
    """Snapshot the artifact hashes referenced by a job before its deletion.

    Deleting the job row cascades its ``artifact_refs`` rows (FK
    ``on delete cascade``), so orphan detection after the delete needs the
    hashes captured beforehand. Failures degrade to an empty snapshot: GC is
    best-effort and must never block the job deletion flow.
    """
    if store is None:
        return []
    try:
        return [ref["hash"] for ref in store.refs_for_job(job_id)]
    except Exception:
        logger.exception("Failed to read artifact refs for job %s", job_id)
        return []


def gc_deleted_job_artifacts(
    store: ArtifactStore | None, job_id: str, candidate_hashes: list[str]
) -> None:
    """Drop the job's artifact refs and physically delete unreferenced artifacts.

    ``delete_refs_for_job`` is an idempotent sweep: it returns an empty list
    when the FK cascade already removed the rows. ``delete_unreferenced``
    re-checks the refcount inside its own transaction, so artifacts still
    referenced by other jobs survive. Failures are logged, never raised:
    orphans are accepted as known debt and must not change the deletion result.
    """
    if store is None:
        return
    try:
        orphaned = store.delete_refs_for_job(job_id)
        hashes = list(dict.fromkeys([*candidate_hashes, *orphaned]))
        if hashes:
            store.delete_unreferenced(hashes)
    except Exception:
        logger.exception("Failed to clean artifact refs after deleting job %s", job_id)
