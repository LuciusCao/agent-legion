from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_execution import JobExecutionService
from server.app.services.job_operation_error import JobOperationError
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.registry import load_registered_workflow
from tests.helpers import publish_builtin_revision


@pytest.fixture
def execution_service(job_db: JobQueries, settings):
    return JobExecutionService(
        job_db,
        JobArtifactMutationService(settings.jobs_dir),
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
    )


@pytest.fixture
def workspace(job_db: JobQueries):
    created = job_db.create_workspace(
        "exec-ws", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, created["id"])
    return created


def _create_job(
    job_db: JobQueries,
    workspace_id: str,
    source_id: str = "Q1",
    workflow_key: str = "education_video_problems_generation",
) -> dict[str, Any]:
    batch = job_db.create_run(
        workflow_key,
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace_id,
    )
    definition = load_registered_workflow(workflow_key)
    return job_db.create_job(
        workflow_key=workflow_key,
        source_type="question",
        source_id=source_id,
        run_id=batch["id"],
        title=f"Question {source_id}",
        node_keys=list(definition.executable_nodes),
        workspace_id=workspace_id,
    )


def _node_statuses(job_db: JobQueries, job_id: str) -> dict[str, str]:
    return {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job_id)}


def _create_active_lease(
    job_db: JobQueries,
    job: dict[str, Any],
    node_key: str,
    expires_offset_seconds: float = 300,
) -> None:
    run = job_db.start_node_run(
        job["id"], node_key, ["cmd"], f"logs/jobs/{job['id']}-{node_key}.log"
    )
    assert run is not None
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=expires_offset_seconds)
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) values (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
            """,
            (
                f"lease-{node_key}",
                f"exec-{node_key}",
                "code-default",
                job["workspace_id"],
                job["id"],
                node_key,
                run["id"],
                database_timestamp(now),
                database_timestamp(now),
                database_timestamp(expires),
            ),
        )


def test_continue_to_target(execution_service: JobExecutionService, job_db: JobQueries, workspace):
    job = _create_job(job_db, workspace["id"])
    job_db.set_job_execution_target(job["id"], "write_script")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=%s", (job["id"],))

    result = execution_service.continue_job(workspace["id"], job["id"])

    assert result == {
        "job_id": job["id"],
        "operation": "continue",
        "status": "succeeded",
        "node_key": None,
        "reason_code": None,
        "message": None,
    }
    job_after = job_db.get_job(job["id"])
    assert job_after["status"] == "queued"
    assert job_after["execution_mode"] == "full"
    assert job_after["target_node_key"] is None
    assert job_after["execution_paused"] == 0
    assert job_after["pause_reason"] == ""


def test_run_to_without_start_unpauses_target_reached_job(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    job_db.update_job_node(job["id"], "intake_knowledge_points", status="completed")
    job_db.update_job_node(job["id"], "write_script", status="completed")
    job_db.set_job_execution_target(job["id"], "write_script")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=%s", (job["id"],))

    result = execution_service.run_to(workspace["id"], job["id"], "review_script")

    assert result["status"] == "succeeded"
    assert result["node_key"] == "review_script"
    job_after = job_db.get_job(job["id"])
    assert job_after["status"] == "queued"
    assert job_after["execution_mode"] == "until_node"
    assert job_after["target_node_key"] == "review_script"
    assert job_after["execution_paused"] == 0
    assert job_after["pause_reason"] == ""
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["intake_knowledge_points"] == "completed"
    assert statuses["write_script"] == "completed"
    assert statuses["review_script"] == "pending"


def test_run_to_with_start_unpauses_target_reached_job(
    execution_service: JobExecutionService, job_db: JobQueries, workspace, settings
):
    job = _create_job(job_db, workspace["id"])
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "script.md").write_text("understanding")
    job_db.update_job_node(job["id"], "intake_knowledge_points", status="completed")
    job_db.update_job_node(job["id"], "write_script", status="completed")
    job_db.set_job_execution_target(job["id"], "write_script")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=%s", (job["id"],))

    result = execution_service.run_to(
        workspace["id"],
        job["id"],
        "review_script",
        start_node_key="write_script",
    )

    assert result["status"] == "succeeded"
    job_after = job_db.get_job(job["id"])
    assert job_after["status"] == "queued"
    assert job_after["execution_paused"] == 0
    assert job_after["pause_reason"] == ""
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["write_script"] == "pending"
    assert statuses["review_script"] == "stale"
    assert not (storage / "script.md").exists()


def test_run_to_without_start_preserves_completed_ancestors(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    job_db.update_job_node(job["id"], "intake_knowledge_points", status="completed")
    job_db.update_job_node(job["id"], "write_script", status="failed")

    result = execution_service.run_to(workspace["id"], job["id"], "write_script")

    assert result["job_id"] == job["id"]
    assert result["operation"] == "run_to"
    assert result["status"] == "succeeded"
    assert result["node_key"] == "write_script"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["intake_knowledge_points"] == "completed"
    assert statuses["write_script"] == "pending"
    control = job_db.get_job_execution_control(job["id"])
    assert control["execution_mode"] == "until_node"
    assert control["target_node_key"] == "write_script"


def test_run_to_with_start_reruns_within_target_closure(
    execution_service: JobExecutionService, job_db: JobQueries, workspace, settings
):
    job = _create_job(job_db, workspace["id"])
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "knowledge_point.json").write_text("context")
    (storage / "script.md").write_text("understanding")

    result = execution_service.run_to(
        workspace["id"],
        job["id"],
        "write_script",
        start_node_key="intake_knowledge_points",
    )

    assert result["status"] == "succeeded"
    assert result["node_key"] == "write_script"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["intake_knowledge_points"] == "pending"
    assert statuses["write_script"] == "stale"
    assert not (storage / "knowledge_point.json").exists()
    assert not (storage / "script.md").exists()
    control = job_db.get_job_execution_control(job["id"])
    assert control["target_node_key"] == "write_script"


def _seed_implicit_workflow_job(
    job_db: JobQueries,
    settings,
    definition_dict: dict,
    node_keys: list[str],
) -> tuple[dict, dict]:
    workspace = job_db.create_workspace("exec-implicit", default_workflow_key="wf759_runto")
    definition = workflow_definition_from_dict(definition_dict)
    WorkflowRevisionService(job_db).ensure_active_revision(workspace["id"], definition)
    batch = job_db.create_run(
        "wf759_runto", "batch_by_ids", {"ids": ["1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        workflow_key="wf759_runto",
        source_type="question",
        source_id="1",
        run_id=batch["id"],
        title="implicit",
        node_keys=node_keys,
        workspace_id=workspace["id"],
    )
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    return job, storage


def test_run_to_with_start_stages_implicit_consumers_outside_closure(
    execution_service: JobExecutionService, job_db: JobQueries, settings
):
    """#759 codex P1：目标闭包外的隐式消费者同样在重置集里，产物必须一并
    暂存——closure 只界定 run-to 的执行范围，不参与暂存判定；否则 run-to
    到达目标继续执行时，隐式消费者会与生产者同时就绪并读到旧输出。"""
    job, storage = _seed_implicit_workflow_job(
        job_db,
        settings,
        {
            "key": "wf759_runto",
            "label": "wf759_runto",
            "nodes": {
                "s": {"capability": "cap_s", "outputs": ["x.json"]},
                "a": {
                    "capability": "cap_a",
                    "after": ["s"],
                    "inputs": ["x.json"],
                    "outputs": ["y.json"],
                },
                "t": {
                    "capability": "cap_t",
                    "after": ["a"],
                    "inputs": ["y.json"],
                    "outputs": ["z.json"],
                },
                # c 只经 inputs/outputs 挂接 s，无任何显式边——不在 t 的
                # 显式祖先闭包里，本用例的突变自检锚点。
                "c": {"capability": "cap_c", "inputs": ["x.json"], "outputs": ["c.json"]},
            },
        },
        ["s", "a", "t", "c"],
    )
    for name in ("x.json", "y.json", "c.json"):
        (storage / name).write_text(name, encoding="utf-8")
    for node_key in ("s", "a", "c"):
        job_db.update_job_node(job["id"], node_key, status="completed")
    with job_db.connect() as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values"
            " (%s, 's', 'x.json', 'k/x.json', 1, ''),"
            " (%s, 'c', 'c.json', 'k/c.json', 1, '')",
            (job["id"], job["id"]),
        )

    result = execution_service.run_to(job["workspace_id"], job["id"], "t", start_node_key="s")

    assert result["status"] == "succeeded"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["s"] == "pending"
    assert statuses["c"] == "stale"
    assert not (storage / "x.json").exists()
    assert not (storage / "c.json").exists()
    with job_db.connect() as conn:
        remaining = conn.execute(
            "select name from job_artifacts where job_id=%s", (job["id"],)
        ).fetchall()
    assert remaining == []


def test_run_to_without_start_rereads_statuses_under_lock(
    execution_service: JobExecutionService, job_db: JobQueries, workspace, settings, monkeypatch
):
    """#759 TOCTOU：锁外读数到取锁之间完成的节点不得被暂存/失效——重置集
    必须在 mutation 锁内重读，由同一当前集合驱动暂存、清单删除与节点重置。"""
    job = _create_job(job_db, workspace["id"])
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    job_db.update_job_node(job["id"], "intake_knowledge_points", status="completed")

    from contextlib import contextmanager

    original = job_db.lease_guarded_mutation

    @contextmanager
    def race(job_id, now, *, reject_running_nodes):
        # 取锁前 write_script 完成（新鲜产物 + 权威清单行）。
        job_db.update_job_node(job["id"], "write_script", status="completed")
        (storage / "script.md").write_text("fresh", encoding="utf-8")
        with job_db.connect() as conn:
            conn.execute(
                "insert into job_artifacts(job_id, node_key, name, storage_key,"
                " size_bytes, content_hash) values (%s, 'write_script', 'script.md',"
                " 'k/script.md', 1, '')",
                (job["id"],),
            )
        with original(job_id, now, reject_running_nodes=reject_running_nodes) as conn:
            yield conn

    monkeypatch.setattr(job_db, "lease_guarded_mutation", race)

    result = execution_service.run_to(workspace["id"], job["id"], "publish_content")

    assert result["status"] == "succeeded"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["write_script"] == "completed"
    assert (storage / "script.md").read_text(encoding="utf-8") == "fresh"
    with job_db.connect() as conn:
        row = conn.execute(
            "select name from job_artifacts where job_id=%s and name='script.md'",
            (job["id"],),
        ).fetchone()
    assert row is not None


def test_run_to_without_start_stages_reset_outputs(
    execution_service: JobExecutionService, job_db: JobQueries, settings
):
    """#759：无起始节点的 run-to 同样必须暂存重置节点的产物——否则隐式链
    上的旧输出文件会让下游在生产者重跑期间读到旧结果。"""
    job, storage = _seed_implicit_workflow_job(
        job_db,
        settings,
        {
            "key": "wf759_runto",
            "label": "wf759_runto",
            "nodes": {
                "n": {"capability": "cap_n", "outputs": ["x.json"]},
                # n→m 只有隐式消费边；m→t 是显式边（t 的闭包只含 m/t）。
                "m": {"capability": "cap_m", "inputs": ["x.json"], "outputs": ["y.json"]},
                "t": {
                    "capability": "cap_t",
                    "after": ["m"],
                    "inputs": ["y.json"],
                    "outputs": ["z.json"],
                },
            },
        },
        ["n", "m", "t"],
    )
    (storage / "x.json").write_text("fresh", encoding="utf-8")
    (storage / "y.json").write_text("stale", encoding="utf-8")
    job_db.update_job_node(job["id"], "n", status="completed")
    job_db.update_job_node(job["id"], "m", status="stale", stale_reason="upstream rerun")
    with job_db.connect() as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values (%s, 'm', 'y.json', 'k/y.json', 1, '')",
            (job["id"],),
        )

    result = execution_service.run_to(job["workspace_id"], job["id"], "t")

    assert result["status"] == "succeeded"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["m"] == "pending"
    assert statuses["t"] == "pending"
    assert (storage / "x.json").read_text(encoding="utf-8") == "fresh"
    assert not (storage / "y.json").exists()
    with job_db.connect() as conn:
        remaining = conn.execute(
            "select name from job_artifacts where job_id=%s", (job["id"],)
        ).fetchall()
    assert remaining == []


def test_run_to_rejects_start_node_outside_target_closure(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])

    with pytest.raises(JobOperationError) as exc_info:
        execution_service.run_to(
            workspace["id"],
            job["id"],
            "write_script",
            start_node_key="review_questions",
        )

    error = exc_info.value
    assert error.job_id == job["id"]
    assert error.operation == "run_to"
    assert error.status == "failed"
    assert error.reason_code == "invalid_start"
    assert "review_questions" in (error.message or "")


def test_run_to_rejects_unknown_target(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])

    with pytest.raises(JobOperationError) as exc_info:
        execution_service.run_to(workspace["id"], job["id"], "nonexistent_target")

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "node_not_found"


def test_run_to_rejects_start_node_target(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])

    with pytest.raises(JobOperationError) as exc_info:
        execution_service.run_to(workspace["id"], job["id"], "_start")

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "node_not_executable"
    assert "_start" in (exc_info.value.message or "")


def test_run_to_rejects_start_node_as_start(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])

    with pytest.raises(JobOperationError) as exc_info:
        execution_service.run_to(
            workspace["id"],
            job["id"],
            "intake_knowledge_points",
            start_node_key="_start",
        )

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "node_not_executable"
    assert "_start" in (exc_info.value.message or "")


def test_run_to_rejects_active_lease(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    _create_active_lease(job_db, job, "intake_knowledge_points")

    with pytest.raises(JobOperationError) as exc_info:
        execution_service.run_to(workspace["id"], job["id"], "write_script")

    assert exc_info.value.status == "skipped"
    assert exc_info.value.reason_code == "busy"


def test_run_to_uses_atomic_execution_control_mutation(
    execution_service: JobExecutionService, job_db: JobQueries, workspace, monkeypatch
):
    job = _create_job(job_db, workspace["id"])
    calls: list[tuple[str, str, frozenset[str]]] = []

    from server.app.services import job_run_to as run_to_module

    original = run_to_module.apply_run_to

    def tracked(conn, job_id, target_node_key, closure, **kwargs):
        calls.append((job_id, target_node_key, closure))
        return original(conn, job_id, target_node_key, closure, **kwargs)

    monkeypatch.setattr(run_to_module, "apply_run_to", tracked)

    result = execution_service.run_to(workspace["id"], job["id"], "write_script")

    assert result["status"] == "succeeded"
    assert calls == [
        (
            job["id"],
            "write_script",
            # The graph closure includes the start node; apply_run_to treats it
            # as a no-op (start never enters job_nodes, EXEC-WORKFLOW-START-001).
            frozenset({"_start", "intake_knowledge_points", "write_script"}),
        )
    ]


def test_run_to_atomic_guard_catches_lease_created_after_precheck(
    execution_service: JobExecutionService, job_db: JobQueries, workspace, monkeypatch
):
    job = _create_job(job_db, workspace["id"])
    monkeypatch.setattr(execution_service, "_has_active_lease", lambda _job_id: False)

    from contextlib import contextmanager

    original = job_db.lease_guarded_mutation

    @contextmanager
    def race(job_id, now, *, reject_running_nodes):
        # 预检之后、原子 guard 之前插入 lease：guard 必须当场抓获。
        _create_active_lease(job_db, job, "intake_knowledge_points")
        with original(job_id, now, reject_running_nodes=reject_running_nodes) as conn:
            yield conn

    monkeypatch.setattr(job_db, "lease_guarded_mutation", race)

    with pytest.raises(JobOperationError) as exc_info:
        execution_service.run_to(workspace["id"], job["id"], "write_script")

    assert exc_info.value.status == "skipped"
    assert exc_info.value.reason_code == "busy"
    assert _node_statuses(job_db, job["id"])["intake_knowledge_points"] == "running"


def test_run_to_skips_already_completed_target(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    job_db.update_job_node(job["id"], "intake_knowledge_points", status="completed")
    job_db.update_job_node(job["id"], "write_script", status="completed")

    with pytest.raises(JobOperationError) as exc_info:
        execution_service.run_to(workspace["id"], job["id"], "write_script")

    assert exc_info.value.status == "skipped"
    assert exc_info.value.reason_code == "target_already_completed"


def test_continue_full_dag_after_target_reached(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    job_db.set_job_execution_target(job["id"], "write_script")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=%s", (job["id"],))
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key in ('intake_knowledge_points', 'write_script')",
            (job["id"],),
        )

    result = execution_service.continue_job(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    job_after = job_db.get_job(job["id"])
    assert job_after["execution_mode"] == "full"
    assert job_after["target_node_key"] is None
    assert job_after["execution_paused"] == 0


def test_run_to_rejects_wrong_workspace(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])

    with pytest.raises(JobOperationError) as exc_info:
        execution_service.run_to("other-ws", job["id"], "write_script")

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "not_found"


def test_continue_rejects_wrong_workspace(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])

    with pytest.raises(JobOperationError) as exc_info:
        execution_service.continue_job("other-ws", job["id"])

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "not_found"


def test_batch_run_to_returns_mixed_results_in_request_order(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"], source_id="Q1")

    results = execution_service.batch_run_to(
        workspace["id"],
        [job["id"], "missing-job"],
        "write_script",
    )

    assert len(results) == 2
    assert results[0]["job_id"] == job["id"]
    assert results[0]["status"] == "succeeded"
    assert results[1]["job_id"] == "missing-job"
    assert results[1]["status"] == "failed"
    assert results[1]["reason_code"] == "not_found"


def test_run_to_without_start_deletes_shards_only_for_reset_nodes(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    """#759 自审 P1：delete_shards 必须与节点重置同一集合——按全 closure
    删会把保持 completed 的分片节点的 output_json 永久抹掉。"""
    job = _create_job(job_db, workspace["id"])
    job_db.update_job_node(job["id"], "intake_knowledge_points", status="completed")
    with job_db.connect() as conn:
        conn.execute(
            "insert into node_shards(job_id, node_key, shard_index, status, input_json)"
            " values (%s, 'intake_knowledge_points', 0, 'completed', '{}'),"
            " (%s, 'write_script', 0, 'pending', '{}')",
            (job["id"], job["id"]),
        )

    result = execution_service.run_to(workspace["id"], job["id"], "write_script")

    assert result["status"] == "succeeded"
    with job_db.connect() as conn:
        remaining = {
            row["node_key"]
            for row in conn.execute(
                "select node_key from node_shards where job_id=%s", (job["id"],)
            ).fetchall()
        }
    assert remaining == {"intake_knowledge_points"}
