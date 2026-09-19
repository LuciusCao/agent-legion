"""Ready-gate input hydration for the workflow worker (issue #759 P1).

The local job_dir is an evictable cache (EXEC-ARTIFACT-STORE-001): the
maintenance thread reclaims local files once the ``job_artifacts`` manifest
row is durably registered, so "file gone but manifest row present" is a
normal state. The ready gate (``find_ready_nodes`` / ``evaluate_branches``)
only probes the local filesystem, so a manifest-only input parked the job at
queued forever — ``restore_missing_inputs`` only runs after a claim the job
could never reach.

This module closes the gap on the evaluation-miss path: for each evaluated
job it batch-fetches the manifest rows once and re-materializes the missing
locally declared inputs (node ``inputs`` ∪ branch-condition artifacts) with
the exact ``restore_missing_inputs`` semantics (``.part`` + sha256 +
``os.replace``, per-file best-effort). It deliberately stays OUT of the pure
``find_ready_nodes``; the caller (``eval_batch``) runs it before branch and
ready evaluation and defers — without caching — any job that still has
manifest-backed inputs missing after the attempt, so the next poll pass
retries (a missing object may be transient).

Threading: the poll thread evaluates jobs sequentially, and
``JobArtifactObjectStore`` is already shared across the route/maintenance/
claim threads (per-call pooled connections), so sharing one instance here
follows the existing usage contract.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from server.app.executors.artifact_restore import restore_from_manifest_row
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from server.app.services.job_artifact_objects import JobArtifactObjectStore

logger = logging.getLogger(__name__)


def declared_artifact_names(definition: WorkflowDefinition) -> frozenset[str]:
    """Every artifact name the ready gate probes locally: node inputs ∪
    branch-condition artifacts."""
    names = {name for node in definition.nodes.values() for name in node.inputs}
    names.update(edge.condition.artifact for edge in definition.edges if edge.condition is not None)
    return frozenset(names)


def hydrate_job_artifacts(
    store: JobArtifactObjectStore | None,
    *,
    job_id: str,
    job_dir: Path,
    definition: WorkflowDefinition,
) -> frozenset[str]:
    """Re-materialize manifest-backed inputs missing from the job_dir.

    Best-effort; no-op without a configured object store. Returns the names
    that STILL lack a local file despite having a manifest row (restore
    failed / object missing) — the caller must not cache the evaluation of
    such a job, so the next poll pass retries. Inputs with no manifest row
    are genuinely absent and are not hydration's business: they are left out
    of the returned set so the job evaluates (and caches) as not-ready.
    """
    if store is None or not store.enabled:
        return frozenset()
    missing = [
        name for name in declared_artifact_names(definition) if not (job_dir / name).is_file()
    ]
    if not missing:
        return frozenset()
    try:
        rows = store.rows_for_job(job_id)
    except Exception:
        # #204 broad-except audit: the manifest read is the one failure the
        # per-file containment cannot see (it happens before any per-file
        # work). A transient DB outage must degrade to the pre-#759 behavior
        # — evaluate with local files only — rather than fail the whole
        # evaluation pass; the outcome space is the psycopg/pool surface of
        # that one query. The traceback is logged so the silent degradation
        # stays visible.
        logger.warning(
            "artifact manifest read failed for job %s; evaluating without hydration",
            job_id,
            exc_info=True,
        )
        return frozenset()
    rows_by_name: dict[str, dict] = {}
    for row in rows:
        # rows_for_job orders by uploaded_at ascending; last write per name
        # wins, matching lookup()'s latest-row semantics.
        rows_by_name[str(row["name"])] = row
    unrestored = {
        name
        for name in missing
        if name in rows_by_name
        and not restore_from_manifest_row(
            store, job_id=job_id, job_dir=job_dir, name=name, row=rows_by_name[name]
        )
    }
    return frozenset(unrestored)
