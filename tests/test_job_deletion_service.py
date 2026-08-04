from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactNotFoundError, ArtifactStore
from server.app.services.job_deletion import (
    DeletionRollbackConflict,
    JobDeleteResult,
    JobDeletionService,
)
from server.app.services.job_operation_error import JobOperationError
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir


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
    job_db.create_workspace(workspace_id, default_workflow_key="question_comprehension_info")
    batch = job_db.create_batch(
        "question_comprehension_info", "batch_by_ids", {"question_ids": [source_id]}, workspace_id
    )
    job = job_db.create_job(
        "question_comprehension_info",
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
            returning id
            """,
            (job_id, node_key, "[]", "", database_timestamp(now)),
        )
        node_run_id = cursor.fetchone()["id"]
        conn.execute(
            """
            insert into executor_leases(
                id, execution_id, executor_id, workspace_id, job_id, workflow_key,
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
                "question_comprehension_info",
                node_key,
                node_run_id,
                database_timestamp(now),
                database_timestamp(now),
                database_timestamp(expires),
            ),
        )


def test_delete_rejects_active_lease_despite_stale_ui(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job = _create_job(job_db, "ws1", "Q001", status="queued")
    # Stale UI still shows queued, but an active non-expired lease exists.
    _insert_active_lease(job_db, job["id"])

    with pytest.raises(JobOperationError) as exc_info:
        service.delete(job["workspace_id"], job["id"])

    error = exc_info.value
    assert error.job_id == job["id"]
    assert error.operation == "delete"
    assert error.status == "failed"
    assert error.reason_code == "active_lease"
    # Database row and storage directory must remain intact.
    assert job_db.get_job(job["id"]) is not None
    assert resolve_job_dir(job, settings.jobs_dir).exists()


def test_delete_atomic_guard_catches_lease_created_after_precheck(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)
    job = _create_job(job_db, "ws-race", "Q001", status="queued")
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
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

    with pytest.raises(JobOperationError) as exc_info:
        service.delete(job["workspace_id"], job["id"])

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "busy"
    assert job_db.get_job(job["id"]) is not None
    assert (storage_dir / "artifact.json").exists()


def test_delete_succeeds_for_inactive_job(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job = _create_job(job_db, "ws2", "Q002", status="completed")
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "artifact.json").write_text("{}", encoding="utf-8")

    result: JobDeleteResult = service.delete(job["workspace_id"], job["id"])

    assert result["job_id"] == job["id"]
    assert result["operation"] == "delete"
    assert result["status"] == "succeeded"
    assert job_db.get_job(job["id"]) is None
    assert not storage_dir.exists()


def _create_artifact_store(job_db: JobQueries, tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts", job_db.path)


def test_delete_cleans_artifact_refs_and_unreferenced_files(
    job_db: JobQueries, tmp_path: Path
) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    # Grace-free store so the test exercises physical GC of just-created blobs.
    store = ArtifactStore(tmp_path / "artifacts", job_db.path, gc_grace_seconds=0)
    service = JobDeletionService(job_db, lease_repo, settings, artifact_store=store)

    job_a = _create_job(job_db, "ws-gc", "Q100", status="completed")
    job_b = _create_job(job_db, "ws-gc", "Q101", status="completed")
    exclusive_hash = store.put(b"exclusive artifact")
    shared_hash = store.put(b"shared artifact")
    store.add_ref(job_a["id"], "extract_question", "exclusive.json", exclusive_hash)
    store.add_ref(job_a["id"], "extract_question", "shared.json", shared_hash)
    store.add_ref(job_b["id"], "extract_question", "shared.json", shared_hash)

    result: JobDeleteResult = service.delete(job_a["workspace_id"], job_a["id"])

    assert result["status"] == "succeeded"
    assert store.refs_for_job(job_a["id"]) == []
    # 独占 artifact 被物理删除；共享 artifact 因 job_b 仍引用而保留。
    with pytest.raises(ArtifactNotFoundError):
        store.open(exclusive_hash)
    assert store.open(shared_hash).read_bytes() == b"shared artifact"
    assert [ref["hash"] for ref in store.refs_for_job(job_b["id"])] == [shared_hash]


def test_delete_with_artifact_store_and_no_refs_keeps_existing_behavior(
    job_db: JobQueries, tmp_path: Path
) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    store = _create_artifact_store(job_db, tmp_path)
    service = JobDeletionService(job_db, lease_repo, settings, artifact_store=store)

    job = _create_job(job_db, "ws-no-refs", "Q102", status="completed")
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)

    result: JobDeleteResult = service.delete(job["workspace_id"], job["id"])

    assert result["status"] == "succeeded"
    assert job_db.get_job(job["id"]) is None
    assert not storage_dir.exists()
    assert store.refs_for_job(job["id"]) == []


def test_delete_without_artifact_store_skips_cleanup(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    store = _create_artifact_store(job_db, tmp_path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job = _create_job(job_db, "ws-no-store", "Q103", status="completed")
    artifact_hash = store.put(b"orphan candidate")
    store.add_ref(job["id"], "extract_question", "out.json", artifact_hash)

    result: JobDeleteResult = service.delete(job["workspace_id"], job["id"])

    assert result["status"] == "succeeded"
    # refs 仍由 FK 级联清除；未注入 store 时跳过物理 GC，artifact 文件保留。
    assert store.refs_for_job(job["id"]) == []
    assert store.open(artifact_hash).read_bytes() == b"orphan candidate"


def test_delete_rejects_wrong_workspace(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job = _create_job(job_db, "ws3", "Q003", status="completed")

    with pytest.raises(JobOperationError) as exc_info:
        service.delete("other-workspace", job["id"])

    error = exc_info.value
    assert error.job_id == job["id"]
    assert error.operation == "delete"
    assert error.status == "failed"
    assert error.reason_code == "wrong_workspace"


def test_delete_rejects_missing_job(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    with pytest.raises(JobOperationError) as exc_info:
        service.delete("ws-missing", "missing-job-id")

    error = exc_info.value
    assert error.job_id == "missing-job-id"
    assert error.operation == "delete"
    assert error.status == "failed"
    assert error.reason_code == "not_found"


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
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
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

    with pytest.raises(JobOperationError) as exc_info:
        service.delete(job["workspace_id"], job["id"])

    assert exc_info.value.status == "failed"
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


def test_delete_raises_for_escaping_storage_dir(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job = _create_job(job_db, "ws-escape", "Q008", status="completed")
    legitimate_storage = resolve_job_dir(job, settings.jobs_dir)
    legitimate_storage.mkdir(parents=True, exist_ok=True)
    (legitimate_storage / "artifact.json").write_text("{}", encoding="utf-8")

    with job_db.connect() as conn:
        conn.execute(
            "update jobs set storage_dir = ? where id = ?",
            ("../escape", job["id"]),
        )

    with pytest.raises(JobOperationError) as exc_info:
        service.delete(job["workspace_id"], job["id"])

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "delete_failed"
    assert legitimate_storage.exists()
    assert (legitimate_storage / "artifact.json").exists()
    assert not (settings.jobs_dir / ".trash").exists()
