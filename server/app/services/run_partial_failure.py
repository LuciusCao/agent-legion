"""Partial-failure error for chunked run creation (#467 A3).

create_jobs_bulk commits in bounded chunks; a mid-run failure leaves earlier
chunks committed. That state is recoverable (a resubmission of the same items
resumes through the dedup filter — the same contract as the async intake
queue's chunk-error recovery), but it MUST be legible to the operator:
without the progress fields, a failed response would hide that thousands of
jobs were actually created.

Kept out of ``run_service.py`` for the file-size budget, mirroring the
``run_bundle_candidate`` precedent.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from server.app.services.job_errors import InvalidOperationError

logger = logging.getLogger(__name__)


class PartialRunCreationError(InvalidOperationError):
    """A chunked run creation failed after ≥1 chunk had committed.

    Attributes:
        created_so_far: jobs committed by earlier chunks before the failure.
        run_id: the partially-created run's id (kept; resubmitting the SAME
            item list lands on the same deterministic run id and resumes,
            while an edited list creates a NEW run — both are safe: the
            dedup keys guarantee no duplicate jobs either way).
    """

    def __init__(self, message: str, *, run_id: str, created_so_far: int) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.created_so_far = created_so_far

    def partial_detail(self) -> dict[str, object]:
        """The structured HTTP detail body (message/run_id/created_so_far)."""
        return {
            "message": str(self),
            "run_id": self.run_id,
            "created_so_far": self.created_so_far,
        }


def partial_failure_message(committed: int, exc: Exception) -> str:
    """Operator-facing text for a mid-run chunked-creation failure."""
    return (
        f"Run creation failed partway ({exc}); {committed} job(s) were already"
        " created and stay in this run. Resubmitting the SAME item list"
        " resumes automatically (already-created items are skipped);"
        " removing bad items creates a new run."
    )


def compensate_partial_creation(
    job_db: Any, run_id: str, exc: Exception
) -> PartialRunCreationError:
    """Two-branch failure compensation shared by the sync creation paths.

    Returns the error to raise (the caller re-raises it, chaining ``exc``).
    Before the first chunk: the guarded run-row removal restores the old
    single-transaction semantics. After a committed chunk: the partial run
    STAYS, is marked failed with its progress, and the structured
    partial-failure error comes back — a run row left 'created' with no
    failure trace would be actively misleading (codex round-1 #2).
    """
    try:
        committed = job_db.count_jobs_in_run(run_id)
    except Exception:
        # #204 broad-except audit: progress bookkeeping inside the compensate
        # path — must not mask the original creation error; degrades to 0
        # (the empty-run branch) and logs. The original error still raises.
        logger.exception("run %s progress count failed", run_id)
        committed = 0
    if committed > 0:
        try:
            job_db.update_intake_run(
                run_id,
                created_count=committed,
                status="failed",
                error_message=partial_failure_message(committed, exc),
            )
        except (OSError, psycopg.Error) as exc2:
            # #204: compensation-only catch — a DB failure here must not mask
            # the original creation error.
            logger.warning("run %s partial-failure marking failed: %s", run_id, exc2)
        return PartialRunCreationError(
            partial_failure_message(committed, exc),
            run_id=run_id,
            created_so_far=committed,
        )
    try:
        job_db.delete_run_without_jobs(run_id)
    except (OSError, psycopg.Error) as exc3:
        # #204: same compensation-only catch — programming errors propagate.
        logger.warning("run %s left orphaned after job creation failed: %s", run_id, exc3)
    raise exc
