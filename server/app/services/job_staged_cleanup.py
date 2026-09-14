from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _StagedOutputs(Protocol):
    def commit(self) -> None: ...


def commit_staged_outputs(
    staged: _StagedOutputs | None,
    job_id: str,
    operation: str,
) -> None:
    """Discard staged files after DB commit without misreporting the mutation."""
    if staged is None:
        return
    try:
        staged.commit()
    except Exception:
        # #204 broad-except audit: post-commit teardown of already-staged
        # files. The DB mutation has COMMITTED by the time this runs — the
        # operation succeeded — so any cleanup failure (OSError from
        # rmtree/unlink inside commit, or whatever the injected staged double
        # raises) must not convert a success into a thrown error after the
        # fact. The residue is a .staged/ dir inside the job dir, which the
        # next eviction/cleanup pass removes; the traceback is logged.
        logger.exception(
            "Failed to clean staged outputs after %s committed for job %s",
            operation,
            job_id,
        )


def delete_rerun_artifact_objects(
    object_store: Any,
    deleted_rows: list[dict[str, Any]],
    job_id: str,
    operation: str,
) -> None:
    """Best-effort object removal for manifest rows a rerun just dropped
    (#508). Mirrors job_deletion's post-commit ordering: the rows are gone
    from ``job_artifacts`` inside the committed transaction, so a failed
    removal only leaves an orphan the bucket lifecycle rule reaps — never a
    listed-but-stale artifact. No object store (None / disabled) = no-op.

    #508 review P1: between the rerun transaction's commit and this cleanup,
    the job is schedulable again — a fast re-attempt may complete and
    register a NEW ``job_artifacts`` row with the same stable authority key
    (``jobs/{workspace}/{job}/{name}``). Deleting that key from the stale
    snapshot would strand the fresh row on a missing object. Re-validate
    every row against the CURRENT manifest before its object deletion: a
    row that reappeared with the same key belongs to the new attempt and is
    skipped (its bytes are the new run's authority copy)."""
    if object_store is None or not getattr(object_store, "enabled", False) or not deleted_rows:
        return
    live_keys = {
        str(row["storage_key"])
        for row in getattr(object_store, "rows_for_job", lambda _job: [])(job_id)
    }
    stale_rows = [row for row in deleted_rows if str(row["storage_key"]) not in live_keys]
    if live_keys:
        logger.info(
            "rerun %s cleanup for job %s skipped %d re-registered object(s)",
            operation,
            job_id,
            len(deleted_rows) - len(stale_rows),
        )
    object_store.delete_objects(stale_rows)
