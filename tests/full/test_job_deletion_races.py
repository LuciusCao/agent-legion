"""Concurrent deletion race scenarios for workspace DAG jobs.

These tests exercise the conflict-safe deletion rollback path under realistic
concurrent recreation of the destination directory.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_deletion import JobDeleteResult, JobDeletionService
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir


@pytest.mark.full_gate
def test_delete_rollback_survives_concurrent_recreation(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    """If the destination is recreated while deletion is staged, rollback must
    preserve the recreated destination byte-for-byte and keep the staged original
    recoverable.
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
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["R1"]}, workspace_id=workspace_id
    )
    job = job_db.create_job(
        "question_content",
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

    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    staged_event = threading.Event()
    recreated_event = threading.Event()
    result_holder: list[JobDeleteResult] = []
    exception_holder: list[BaseException] = []

    def _failing_after_race(*args: Any, **kwargs: Any) -> None:
        staged_event.set()
        if not recreated_event.wait(timeout=5.0):
            raise TimeoutError("Destination was not recreated in time")
        raise RuntimeError("db failure")

    monkeypatch.setattr(job_db, "delete_job_in_transaction", _failing_after_race)

    def _deleter() -> None:
        try:
            result_holder.append(service.delete(workspace_id, job["id"]))
        except BaseException as exc:  # pragma: no cover - defensive
            exception_holder.append(exc)

    def _recreator() -> None:
        if not staged_event.wait(timeout=5.0):
            raise TimeoutError("Staging did not complete in time")
        # Recreate the destination with different content while deletion is staged.
        storage_dir.mkdir(parents=True, exist_ok=True)
        (storage_dir / "sentinel.bin").write_bytes(b"recreated-bytes")
        recreated_event.set()

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
    assert result["reason_code"] == "rollback_conflict"

    # The recreated destination must survive byte-for-byte.
    assert storage_dir.exists()
    sentinel = storage_dir / "sentinel.bin"
    assert sentinel.exists()
    assert sentinel.read_bytes() == b"recreated-bytes"

    # The staged original must remain recoverable.
    trash_root = settings.jobs_dir / ".trash"
    assert trash_root.exists()
    staged_dirs = [p for p in trash_root.rglob("*") if p.is_dir() and p.name == storage_dir.name]
    assert staged_dirs, "Staged original directory not found in trash"
    staged_storage = staged_dirs[0]
    assert (staged_storage / "artifact.bin").read_bytes() == b"original-bytes"
