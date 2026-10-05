from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import server.app.services.job_deletion_trash as trash_module
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactNotFoundError, ArtifactStore
from server.app.services.job_deletion import JobDeleteResult, JobDeletionService
from server.app.services.job_operation_error import JobOperationError
from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError, resolve_job_dir


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
    batch = job_db.create_run(
        "demo_workflow", "batch_by_ids", {"question_ids": [source_id]}, workspace_id
    )
    job = job_db.create_job(
        "demo_workflow",
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
            values (%s, %s, 'running', %s, %s, '', '', %s)
            returning id
            """,
            (job_id, node_key, "[]", "", database_timestamp(now)),
        )
        node_run_id = cursor.fetchone()["id"]
        conn.execute(
            """
            insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) values (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
            """,
            (
                f"lease-{job_id}",
                f"exec-{job_id}",
                "local",
                workspace_id,
                job_id,
                node_key,
                node_run_id,
                database_timestamp(now),
                database_timestamp(now),
                database_timestamp(expires),
            ),
        )


def test_delete_rejects_active_lease_despite_stale_ui(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
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
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
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
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
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


def test_delete_cancels_queued_agent_requests(job_db: JobQueries, tmp_path: Path) -> None:
    """#759：job 删除后其 queued 请求行随之物理消失（on delete cascade）。

    钉住升级路径修复依赖的契约：删除类路径无需取消 queued 请求的前提
    是 FK cascade——若有人移除 cascade，遗留的孤儿 queued 行无人 claim
    时永驻队列表。"""
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
    service = JobDeletionService(job_db, lease_repo, settings)
    job = _create_job(job_db, "ws-cancel", "Q009", status="queued")
    with job_db.connect() as conn:
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, node_key,"
            " agent_id, agent_definition_hash, node_concurrency_limit,"
            " state, queued_at, manifest_json)"
            " values ('exec-delete-queued', %s, %s, 'extract_question',"
            " 'generator-v1', 'sha256:whatever', 1, 'queued', current_timestamp, '{}')",
            (job["workspace_id"], job["id"]),
        )

    result: JobDeleteResult = service.delete(job["workspace_id"], job["id"])

    assert result["status"] == "succeeded"
    with job_db.connect() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id='exec-delete-queued'"
        ).fetchone()
    assert row is None


def _create_artifact_store(job_db: JobQueries, tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts", job_db.dsn_identity)


def test_delete_cleans_artifact_refs_and_unreferenced_files(
    job_db: JobQueries, tmp_path: Path
) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
    # Grace-free store so the test exercises physical GC of just-created blobs.
    store = ArtifactStore(tmp_path / "artifacts", job_db.dsn_identity, gc_grace_seconds=0)
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
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
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
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
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
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job = _create_job(job_db, "ws3", "Q003", status="completed")

    with pytest.raises(JobOperationError) as exc_info:
        service.delete("other-workspace", job["id"])

    error = exc_info.value
    assert error.job_id == job["id"]
    assert error.operation == "delete"
    assert error.status == "failed"
    assert error.reason_code == "not_found"


def test_delete_rejects_missing_job(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
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
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
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
    assert results[2]["reason_code"] == "not_found"
    assert results[3]["status"] == "failed"
    assert results[3]["reason_code"] == "not_found"


def _trash_entries(settings: Settings) -> list[Path]:
    roots = [settings.jobs_dir / ".trash", settings.logs_dir / "jobs" / ".trash"]
    return [path for root in roots if root.exists() for path in root.rglob("*")]


def _seed_job_files(settings: Settings, job: dict[str, Any]) -> tuple[Path, Path]:
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "original.json").write_text("original", encoding="utf-8")
    log_path = settings.logs_dir / "jobs" / f"{job['id']}-extract_question.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("log", encoding="utf-8")
    return storage_dir, log_path


def test_delete_transaction_failure_leaves_files_untouched(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    """#958 失败点①：事务失败时文件系统零改动（不再有 trash 暂存与回滚）。"""
    settings = _create_settings(tmp_path)
    service = JobDeletionService(
        job_db, ExecutorLeaseRepository(job_db, data_dir=tmp_path), settings
    )
    job = _create_job(job_db, "ws-txfail", "Q007", status="completed")
    storage_dir, log_path = _seed_job_files(settings, job)

    def _db_down(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(job_db, "delete_job_in_transaction", _db_down)

    with pytest.raises(JobOperationError) as exc_info:
        service.delete(job["workspace_id"], job["id"])

    assert exc_info.value.reason_code == "delete_failed"
    assert job_db.get_job(job["id"]) is not None
    assert (storage_dir / "original.json").read_text(encoding="utf-8") == "original"
    assert log_path.read_text(encoding="utf-8") == "log"
    assert _trash_entries(settings) == []


def test_delete_moves_files_only_after_commit(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    """#958：文件移动发生在提交之后——移动时另一条连接已读不到 jobs 行。"""
    settings = _create_settings(tmp_path)
    service = JobDeletionService(
        job_db, ExecutorLeaseRepository(job_db, data_dir=tmp_path), settings
    )
    job = _create_job(job_db, "ws-order", "Q010", status="completed")
    storage_dir, log_path = _seed_job_files(settings, job)

    real_move = trash_module.shutil.move
    row_visible_at_move: list[bool] = []

    def _observing_move(src: str, dst: str) -> Any:
        row_visible_at_move.append(job_db.get_job(job["id"]) is not None)
        return real_move(src, dst)

    monkeypatch.setattr(trash_module.shutil, "move", _observing_move)

    result = service.delete(job["workspace_id"], job["id"])

    assert result["status"] == "succeeded"
    assert row_visible_at_move == [False, False]  # job_dir + 一个日志
    assert not storage_dir.exists()
    assert not log_path.exists()
    assert _trash_entries(settings) == []
    assert not (settings.jobs_dir / ".trash").exists()


def test_delete_succeeds_when_staging_into_trash_fails(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    """#958 失败点②：提交成功、移入 trash 失败 → 删除仍成功，残留留在原位。"""
    settings = _create_settings(tmp_path)
    service = JobDeletionService(
        job_db, ExecutorLeaseRepository(job_db, data_dir=tmp_path), settings
    )
    job = _create_job(job_db, "ws-stagefail", "Q011", status="completed")
    storage_dir, log_path = _seed_job_files(settings, job)

    def _move_fails(src: str, dst: str) -> None:
        raise OSError("disk unhappy")

    monkeypatch.setattr(trash_module.shutil, "move", _move_fails)

    result = service.delete(job["workspace_id"], job["id"])

    assert result["status"] == "succeeded"
    assert job_db.get_job(job["id"]) is None
    assert (storage_dir / "original.json").exists()
    assert log_path.exists()
    # 空 operation 目录被剪掉，不留 trash 空壳。
    assert _trash_entries(settings) == []


def test_delete_succeeds_when_purging_staged_files_fails(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    """#958 失败点③：移入 trash 后删除失败 → 删除仍成功，残留在 .trash/<op>/。"""
    settings = _create_settings(tmp_path)
    service = JobDeletionService(
        job_db, ExecutorLeaseRepository(job_db, data_dir=tmp_path), settings
    )
    job = _create_job(job_db, "ws-purgefail", "Q012", status="completed")
    storage_dir, _log_path = _seed_job_files(settings, job)

    def _rmtree_fails(path: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError("busy")

    monkeypatch.setattr(trash_module.shutil, "rmtree", _rmtree_fails)

    result = service.delete(job["workspace_id"], job["id"])

    assert result["status"] == "succeeded"
    assert job_db.get_job(job["id"]) is None
    assert not storage_dir.exists()
    staged = [p for p in (settings.jobs_dir / ".trash").rglob(storage_dir.name) if p.is_dir()]
    assert len(staged) == 1
    assert (staged[0] / "original.json").read_text(encoding="utf-8") == "original"


def test_delete_skips_local_cleanup_when_path_revalidation_fails(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    """#958：提交后重新解析路径失败（逃逸）→ 不动文件，删除仍成功。"""
    settings = _create_settings(tmp_path)
    service = JobDeletionService(
        job_db, ExecutorLeaseRepository(job_db, data_dir=tmp_path), settings
    )
    job = _create_job(job_db, "ws-revalidate", "Q013", status="completed")
    storage_dir, _log_path = _seed_job_files(settings, job)

    def _escapes(*args: Any, **kwargs: Any) -> Path:
        raise ManagedPathError("Path escapes job root")

    monkeypatch.setattr(trash_module, "resolve_job_dir", _escapes)

    result = service.delete(job["workspace_id"], job["id"])

    assert result["status"] == "succeeded"
    assert job_db.get_job(job["id"]) is None
    assert (storage_dir / "original.json").exists()


def test_delete_raises_for_escaping_storage_dir(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
    service = JobDeletionService(job_db, lease_repo, settings)

    job = _create_job(job_db, "ws-escape", "Q008", status="completed")
    legitimate_storage = resolve_job_dir(job, settings.jobs_dir)
    legitimate_storage.mkdir(parents=True, exist_ok=True)
    (legitimate_storage / "artifact.json").write_text("{}", encoding="utf-8")

    with job_db.connect() as conn:
        conn.execute(
            "update jobs set storage_dir = %s where id = %s",
            ("../escape", job["id"]),
        )

    with pytest.raises(JobOperationError) as exc_info:
        service.delete(job["workspace_id"], job["id"])

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "delete_failed"
    assert legitimate_storage.exists()
    assert (legitimate_storage / "artifact.json").exists()
    assert not (settings.jobs_dir / ".trash").exists()
