from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg
import pytest

from server.app.services.run_partial_failure import (
    PartialRunCreationError,
    compensate_partial_creation,
    raise_compensated_partial_creation,
)

pytestmark = pytest.mark.no_db


def _job_db(committed: int) -> SimpleNamespace:
    return SimpleNamespace(
        count_jobs_in_run=MagicMock(return_value=committed),
        update_intake_run=MagicMock(),
        delete_run_without_jobs=MagicMock(),
    )


def test_partial_compensation_marks_failed_and_preserves_progress() -> None:
    job_db = _job_db(3)
    original = ValueError("chunk failed")

    with pytest.raises(PartialRunCreationError) as caught:
        raise_compensated_partial_creation(job_db, "run-1", original)

    error = caught.value
    assert error.__cause__ is original
    assert error.partial_detail() == {
        "message": str(error),
        "run_id": "run-1",
        "created_so_far": 3,
    }
    assert "3 job(s) were already created" in str(error)
    job_db.update_intake_run.assert_called_once_with(
        "run-1",
        created_count=3,
        status="failed",
        error_message=str(error),
    )
    job_db.delete_run_without_jobs.assert_not_called()


def test_empty_compensation_deletes_run_and_reraises_original() -> None:
    job_db = _job_db(0)
    original = ValueError("before first commit")

    with pytest.raises(ValueError) as caught:
        raise_compensated_partial_creation(job_db, "run-empty", original)

    assert caught.value is original
    job_db.delete_run_without_jobs.assert_called_once_with("run-empty")
    job_db.update_intake_run.assert_not_called()


def test_marking_failure_does_not_mask_partial_error() -> None:
    job_db = _job_db(2)
    job_db.update_intake_run.side_effect = psycopg.OperationalError("write unavailable")
    original = RuntimeError("insert failed")

    error = compensate_partial_creation(job_db, "run-2", original)

    assert isinstance(error, PartialRunCreationError)
    assert error.created_so_far == 2
    assert "insert failed" in str(error)
    job_db.delete_run_without_jobs.assert_not_called()


def test_count_failure_degrades_to_guarded_empty_run_removal() -> None:
    job_db = _job_db(0)
    job_db.count_jobs_in_run.side_effect = RuntimeError("count unavailable")
    job_db.delete_run_without_jobs.side_effect = OSError("delete unavailable")

    assert compensate_partial_creation(job_db, "run-3", ValueError("create failed")) is None

    job_db.delete_run_without_jobs.assert_called_once_with("run-3")
    job_db.update_intake_run.assert_not_called()
