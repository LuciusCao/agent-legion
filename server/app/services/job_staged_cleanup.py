from __future__ import annotations

import logging
from typing import Protocol

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
        logger.exception(
            "Failed to clean staged outputs after %s committed for job %s",
            operation,
            job_id,
        )
