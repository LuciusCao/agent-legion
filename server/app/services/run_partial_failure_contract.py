"""Operator-facing error contract for partially committed run creation."""

from __future__ import annotations

from server.app.services.job_errors import InvalidOperationError


class PartialRunCreationError(InvalidOperationError):
    """A chunked run creation failed after at least one chunk committed."""

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
