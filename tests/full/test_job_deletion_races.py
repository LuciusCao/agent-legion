"""Concurrent deletion race scenarios for workspace DAG jobs.

#958: the deletion transaction carries only the DB write; local files move
after commit. These tests pin that a concurrent writer during the open
transaction sees the job directory in place, and that a failed transaction
leaves every byte (original and concurrently written) untouched.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_deletion import JobDeleteResult, JobDeletionService
from server.app.services.job_operation_error import JobOperationError
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir


@pytest.mark.full_gate
def test_failed_delete_transaction_never_touches_concurrent_writes(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    """While the deletion transaction is open the job directory stays in place
    (no in-transaction staging); when the transaction fails, both the original
    bytes and a concurrent writer's bytes survive and no trash is created.
    """
    data_dir = tmp_path
    jobs_dir = data_dir / "jobs"
    logs_dir = data_dir / "logs"
    videos_dir = data_dir / "videos"
    packages_dir = data_dir / "packages"
    for path in [data_dir, jobs_dir, logs_dir, videos_dir, packages_dir]:
        path.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        root_dir=tmp_path,
        data_dir=data_dir,
        videos_dir=videos_dir,
        logs_dir=logs_dir,
        packages_dir=packages_dir,
        jobs_dir=jobs_dir,
        config={},
    )

    workspace_id = "ws-race"
    job_db.create_workspace(workspace_id)
    batch = job_db.create_run(
        "demo_workflow",
        "batch_by_ids",
        {"question_ids": ["R1"]},
        workspace_id=workspace_id,
    )
    job = job_db.create_job(
        "demo_workflow",
        "question",
        "R1",
        batch["id"],
        "Race job",
        ["extract_question"],
        workspace_id=workspace_id,
    )
    job_db.update_job_status(job["id"], "completed")

    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    original_artifact = storage_dir / "artifact.bin"
    original_artifact.write_bytes(b"original-bytes")

    lease_repo = ExecutorLeaseRepository(job_db, data_dir=data_dir)
    service = JobDeletionService(job_db, lease_repo, settings)

    in_tx_event = threading.Event()
    written_event = threading.Event()
    result_holder: list[JobDeleteResult] = []
    exception_holder: list[BaseException] = []

    def _failing_after_race(*args: Any, **kwargs: Any) -> None:
        in_tx_event.set()
        if not written_event.wait(timeout=5.0):
            raise TimeoutError("Concurrent write did not happen in time")
        raise RuntimeError("db failure")

    monkeypatch.setattr(job_db, "delete_job_in_transaction", _failing_after_race)

    def _deleter() -> None:
        try:
            result_holder.append(service.delete(workspace_id, job["id"]))
        except JobOperationError as exc:
            result_holder.append(exc.to_result())
        except BaseException as exc:  # pragma: no cover - defensive
            exception_holder.append(exc)

    dir_in_place_during_tx: list[bool] = []

    def _recreator() -> None:
        if not in_tx_event.wait(timeout=5.0):
            raise TimeoutError("Transaction did not open in time")
        # The transaction is open: the directory must still be at its path.
        dir_in_place_during_tx.append((storage_dir / "artifact.bin").exists())
        (storage_dir / "sentinel.bin").write_bytes(b"concurrent-bytes")
        written_event.set()

    deleter = threading.Thread(target=_deleter)
    recreator = threading.Thread(target=_recreator)
    deleter.start()
    recreator.start()
    deleter.join(timeout=10.0)
    recreator.join(timeout=10.0)

    assert not deleter.is_alive()
    assert not recreator.is_alive()
    assert not exception_holder, exception_holder
    assert result_holder, "Deletion service did not return a result"

    result = result_holder[0]
    assert result["status"] == "failed"
    assert result["reason_code"] == "delete_failed"
    assert dir_in_place_during_tx == [True]

    assert job_db.get_job(job["id"]) is not None
    assert original_artifact.read_bytes() == b"original-bytes"
    assert (storage_dir / "sentinel.bin").read_bytes() == b"concurrent-bytes"
    assert not (settings.jobs_dir / ".trash").exists()
