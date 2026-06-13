from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.app.executors._lease_transactions import _sqlite_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_deletion import (
    DeletionRollbackConflict,
    JobDeleteResult,
    JobDeletionService,
)
from server.app.settings import Settings


def _create_settings(tmp_path: Path) -> Settings:
    # Match the paths produced by the job_db fixture, which uses
    # load_settings(data_dir=tmp_path) and therefore jobs_dir=tmp_path/jobs.
    data_dir = tmp_path
    jobs_dir = data_dir / "jobs"
    logs_dir = data_dir / "logs"
    videos_dir = data_dir / "videos"
    packages_dir = data_dir / "packages"
    for path in [data_dir, jobs_dir, logs_dir, videos_dir, packages_dir]:
        path.mkdir(parents=True, exist_ok=True)
    return Settings(
        root_dir=tmp_path,
        data_dir=data_dir,
        videos_dir=videos_dir,
        logs_dir=logs_dir,
        packages_dir=packages_dir,
        jobs_dir=jobs_dir,
        config={},
    )


def _create_job(
    job_db: JobQueries, workspace_id: str, source_id: str, status: str = "queued"
) -> dict[str, Any]:
    job_db.create_workspace(workspace_id)
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": [source_id]}, workspace_id
    )
    job = job_db.create_job(
        "question_content",
        "question",
        source_id,
        batch["id"],
        f"Job {source_id}",
        ["extract_question"],
        workspace_id=workspace_id,
    )
    if status != "queued":
        job_db.update_job_status(job["id"], status)
    return job


def _insert_active_lease(
    job_db: JobQueries,
    job_id: str,
    node_key: str = "extract_question",
    expires_in_seconds: int = 300,
) -> None:
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=expires_in_seconds)
    workspace_id = job_id.split("_")[0]
    with job_db.connect() as conn:
        cursor = conn.execute(
            """
            insert into node_runs(job_id, node_key, status, command_json, log_path, run_dir, session_dir, started_at)
            values (?, ?, 'running', ?, ?, '', '', ?)
            """,
            (job_id, node_key, "[]", "", _sqlite_timestamp(now)),
        )
        node_run_id = cursor.lastrowid
        conn.execute(
            """
            insert into executor_leases(
                id, execution_id, executor_id, workspace_id, job_id, pipeline_key,
                node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                f"lease-{job_id}",
                f"exec-{job_id}",
                "local",
                workspace_id,
                job_id,
                "question_content",
                node_key,
                node_run_id,
                _sqlite_timestamp(now),
                _sqlite_timestamp(now),
                _sqlite_timestamp(expires),
            ),
        )


def test_delete_rejects_active_lease_despite_stale_ui(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job = _create_job(job_db, "ws1", "Q001", status="queued")
    # Stale UI still shows queued, but an active non-expired lease exists.
    _insert_active_lease(job_db, job["id"])

    result: JobDeleteResult = service.delete(job["workspace_id"], job["id"])

    assert result["job_id"] == job["id"]
    assert result["operation"] == "delete"
    assert result["status"] == "failed"
    assert result["reason_code"] == "active_lease"
    # Database row and storage directory must remain intact.
    assert job_db.get_job(job["id"]) is not None
    assert Path(str(job["storage_dir"])).exists()


def test_delete_atomic_guard_catches_lease_created_after_precheck(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)
    job = _create_job(job_db, "ws-race", "Q001", status="queued")
    storage_dir = Path(str(job["storage_dir"]))
    (storage_dir / "artifact.json").write_text("{}", encoding="utf-8")

    original = job_db.lease_guarded_mutation
    created = False
    monkeypatch.setattr(lease_repo, "has_active_for_job", lambda *args: False)

    @contextmanager
    def race(job_id: str, now, *, reject_running_nodes: bool):
        nonlocal created
        if not created:
            _insert_active_lease(job_db, job_id)
            created = True
        with original(job_id, now, reject_running_nodes=reject_running_nodes) as conn:
            yield conn

    monkeypatch.setattr(job_db, "lease_guarded_mutation", race)

    result = service.delete(job["workspace_id"], job["id"])

    assert result["status"] == "failed"
    assert result["reason_code"] == "active_lease"
    assert job_db.get_job(job["id"]) is not None
    assert (storage_dir / "artifact.json").exists()


def test_delete_succeeds_for_inactive_job(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job = _create_job(job_db, "ws2", "Q002", status="completed")
    storage_dir = Path(str(job["storage_dir"]))
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "artifact.json").write_text("{}", encoding="utf-8")

    result: JobDeleteResult = service.delete(job["workspace_id"], job["id"])

    assert result["job_id"] == job["id"]
    assert result["operation"] == "delete"
    assert result["status"] == "succeeded"
    assert job_db.get_job(job["id"]) is None
    assert not storage_dir.exists()


def test_delete_rejects_wrong_workspace(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job = _create_job(job_db, "ws3", "Q003", status="completed")

    result: JobDeleteResult = service.delete("other-workspace", job["id"])

    assert result["job_id"] == job["id"]
    assert result["operation"] == "delete"
    assert result["status"] == "failed"
    assert result["reason_code"] == "wrong_workspace"


def test_delete_rejects_missing_job(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    result: JobDeleteResult = service.delete("ws-missing", "missing-job-id")

    assert result["job_id"] == "missing-job-id"
    assert result["operation"] == "delete"
    assert result["status"] == "failed"
    assert result["reason_code"] == "not_found"


def test_batch_delete_returns_ordered_results(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job_a = _create_job(job_db, "ws4", "Q004", status="completed")
    job_b = _create_job(job_db, "ws4", "Q005", status="completed")
    job_c = _create_job(job_db, "ws5", "Q006", status="completed")
    _insert_active_lease(job_db, job_b["id"])

    results: list[JobDeleteResult] = service.batch_delete(
        job_a["workspace_id"], [job_a["id"], job_b["id"], job_c["id"], "missing"]
    )

    assert [r["job_id"] for r in results] == [job_a["id"], job_b["id"], job_c["id"], "missing"]
    assert results[0]["status"] == "succeeded"
    assert results[1]["status"] == "failed"
    assert results[1]["reason_code"] == "active_lease"
    assert results[2]["status"] == "failed"
    assert results[2]["reason_code"] == "wrong_workspace"
    assert results[3]["status"] == "failed"
    assert results[3]["reason_code"] == "not_found"


def test_delete_rollback_preserves_recreated_destination(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    """Rollback must not overwrite a destination recreated after staging."""
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)
    job = _create_job(job_db, "ws-rollback", "Q007", status="completed")
    storage_dir = Path(str(job["storage_dir"]))
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "original.json").write_text("original", encoding="utf-8")

    captured_paths: list[tuple[Path, Path]] = []
    original_restore = JobDeletionService._restore_paths

    def _capture_and_skip(restore_paths: list[tuple[Path, Path]]) -> None:
        captured_paths.extend(restore_paths)

    monkeypatch.setattr(JobDeletionService, "_restore_paths", staticmethod(_capture_and_skip))

    def _fail_once(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(job_db, "delete_job_in_transaction", _fail_once)

    result = service.delete(job["workspace_id"], job["id"])

    assert result["status"] == "failed"
    assert captured_paths
    staged_storage, original_storage = captured_paths[0]
    assert staged_storage.exists()
    assert staged_storage != original_storage
    assert not original_storage.exists()

    # Simulate a concurrent recreation of the destination with different content.
    original_storage.mkdir(parents=True, exist_ok=True)
    (original_storage / "sentinel.json").write_text("recreated", encoding="utf-8")

    with pytest.raises(DeletionRollbackConflict) as exc_info:
        original_restore(captured_paths)

    assert exc_info.value.original_path == original_storage
    assert exc_info.value.staged_path == staged_storage
    assert (original_storage / "sentinel.json").exists()
    assert (original_storage / "sentinel.json").read_text(encoding="utf-8") == "recreated"
    assert staged_storage.exists()
    assert (staged_storage / "original.json").read_text(encoding="utf-8") == "original"
