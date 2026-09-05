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

from server.app.services.job_errors import InvalidOperationError


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
