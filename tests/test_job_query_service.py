from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.app.executors._lease_transactions import _database_timestamp
from server.app.executors.config import RemoteCapabilityConfig, RemoteExecutorConfig
from server.app.executors.remote_broker import RemoteExecutionBroker, RemoteExecutionPayload
from server.app.services.job_queries import JobQueryService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.storage_paths import make_data_relative, resolve_job_dir


@pytest.fixture
def query_service(job_db, settings):
    return JobQueryService(
        job_db,
        settings,
        WorkflowCatalogService(settings),
        WorkspaceExecutorConfigurationService(job_db),
    )


def create_question_job(job_db, source_id: str) -> dict[str, Any]:
    workspace = job_db.get_workspace("default") or job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace["id"],
    )
    job: dict[str, Any] = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id=source_id,
        batch_id=batch["id"],
        title=f"Question {source_id}",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )
    return job


def test_job_query_service_lists_jobs(query_service, job_db):
    job_db.create_workspace("default", default_workflow_key="question_comprehension_info")
    job = query_service.list_jobs("default")
    assert isinstance(job, list)


def test_list_jobs_returns_typed_node_summaries(query_service, job_db):
    job = create_question_job(job_db, source_id="Q1")
    job_db.update_job_node(job["id"], "question_understanding", status="completed")
    job_db.update_job_node(
        job["id"], "assemble_package", status="failed", error_message="assemble failed"
    )

    listed = query_service.list_jobs(job["workspace_id"])

    assert [node["node_key"] for node in listed[0]["node_summaries"]] == [
        "question_understanding",
        "assemble_package",
    ]
    assert listed[0]["completed_nodes"] == 1
    assert listed[0]["total_nodes"] == 2
    assert listed[0]["active_node_key"] == "assemble_package"
    assert listed[0]["error_summary"] == "assemble failed"
    assert listed[0]["execution_control"] == {
        "mode": "full",
        "target_node_key": None,
        "paused": False,
        "pause_reason": "",
    }


def test_list_jobs_exposes_job_workflow_version_and_outdated_status(query_service, job_db):
    workspace = job_db.create_workspace(
        "versioned", default_workflow_key="question_comprehension_info"
    )
    definition = WorkflowCatalogService(query_service.settings).definition(
        "question_comprehension_info"
    )
    revision_service = WorkflowRevisionService(job_db)
    original = revision_service.publish_workspace_revision(workspace["id"], definition)
    current = revision_service.publish_workspace_revision(workspace["id"], definition)
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )

    listed = query_service.list_jobs(workspace["id"])

    assert listed[0]["workflow_version"] == 1
    assert listed[0]["current_workflow_revision_id"] == current["id"]
    assert listed[0]["current_workflow_revision_version"] == 2
    assert listed[0]["is_workflow_outdated"] is True


def test_list_jobs_orders_node_summaries_by_workflow_dag(query_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=[
            "assemble_comprehension_info",
            "assess_comprehension_difficulty",
            "classify_comprehension_eligibility",
            "clean_and_parse",
            "fetch_questions",
            "finalize_non_uploadable",
            "generate_key_info",
            "generate_possible_errors",
            "review_key_info",
            "review_possible_errors",
        ],
        workspace_id=workspace["id"],
    )

    listed = query_service.list_jobs(job["workspace_id"])

    node_keys = [node["node_key"] for node in listed[0]["node_summaries"]]
    assert node_keys[:3] == [
        "fetch_questions",
        "clean_and_parse",
        "classify_comprehension_eligibility",
    ]
    assert node_keys.index("assemble_comprehension_info") > node_keys.index(
        "assess_comprehension_difficulty"
    )


def test_list_jobs_loads_nodes_in_one_query(query_service, job_db, monkeypatch):
    for source_id in ("Q1", "Q2", "Q3"):
        create_question_job(job_db, source_id=source_id)
    statements: list[str] = []
    original = job_db._connect_read

    @contextmanager
    def traced():
        with original() as conn:
            execute = conn.execute

            def traced_execute(sql, params=None):
                statements.append(sql)
                return execute(sql, params)

            monkeypatch.setattr(conn, "execute", traced_execute)
            yield conn

    monkeypatch.setattr(job_db, "_connect_read", traced)
    query_service.list_jobs("default")

    node_selects = [sql for sql in statements if "from job_nodes" in sql.lower()]
    assert len(node_selects) == 1


def test_list_jobs_does_not_reload_each_job_for_execution_control(
    query_service, job_db, monkeypatch
):
    for source_id in ("Q1", "Q2", "Q3"):
        create_question_job(job_db, source_id=source_id)
    statements: list[str] = []
    original = job_db._connect_read

    @contextmanager
    def traced():
        with original() as conn:
            execute = conn.execute

            def traced_execute(sql, params=None):
                statements.append(sql)
                return execute(sql, params)

            monkeypatch.setattr(conn, "execute", traced_execute)
            yield conn

    monkeypatch.setattr(job_db, "_connect_read", traced)
    query_service.list_jobs("default")

    job_selects = [sql for sql in statements if "from jobs" in sql.lower()]
    assert len(job_selects) == 1


def test_job_query_service_detail_enriches_nodes(query_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )

    detail = query_service.detail(job["id"])

    assert detail["job"]["id"] == job["id"]
    assert len(detail["nodes"]) == 2
    assert detail["nodes"][0]["label"]
    assert "artifacts" in detail
    for node in detail["nodes"]:
        assert node["executor_id"] is None
        assert node["executor_kind"] is None


def test_job_query_service_detail_orders_nodes_and_uses_edge_dependencies(query_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=[
            "assemble_comprehension_info",
            "classify_comprehension_eligibility",
            "clean_and_parse",
            "fetch_questions",
        ],
        workspace_id=workspace["id"],
    )

    detail = query_service.detail(job["id"])

    assert [node["node_key"] for node in detail["nodes"]] == [
        "fetch_questions",
        "clean_and_parse",
        "classify_comprehension_eligibility",
        "assemble_comprehension_info",
    ]
    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["classify_comprehension_eligibility"]["after"] == ["clean_and_parse"]


def test_job_query_service_detail_lists_artifacts_from_relative_storage_dir(
    query_service, job_db, settings
):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding"],
        workspace_id=workspace["id"],
    )
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "result.json").write_text('{"ok": true}', encoding="utf-8")
    (storage_dir / "nested").mkdir(parents=True, exist_ok=True)

    detail = query_service.detail(job["id"])

    assert detail["artifacts"] == ["result.json"]


def test_job_detail_resolves_executor_id_and_kind_from_settings(query_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )
    job_db.replace_workspace_executor_configuration(
        workspace["id"],
        allocations=[
            {"executor_id": "local-default", "concurrency_limit": 1},
            {"executor_id": "pi", "concurrency_limit": 1},
        ],
        bindings=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "question_understanding",
                "executor_id": "pi",
            },
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "assemble_package",
                "executor_id": "local-default",
            },
        ],
        node_limits=[],
    )

    detail = query_service.detail(job["id"])

    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["question_understanding"]["executor_id"] == "pi"
    assert nodes["question_understanding"]["executor_kind"] == "pi"
    assert nodes["assemble_package"]["executor_id"] == "local-default"
    assert nodes["assemble_package"]["executor_kind"] == "local"


def test_job_detail_resolves_executor_binding_for_job_workflow_only(query_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["assemble_package"],
        workspace_id=workspace["id"],
    )
    job_db.replace_workspace_executor_configuration(
        workspace["id"],
        allocations=[
            {"executor_id": "local-default", "concurrency_limit": 1},
            {"executor_id": "pi", "concurrency_limit": 1},
        ],
        bindings=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "assemble_package",
                "executor_id": "local-default",
            },
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "assemble_comprehension_info",
                "executor_id": "pi",
            },
        ],
        node_limits=[],
    )

    detail = query_service.detail(job["id"])

    node = detail["nodes"][0]
    assert node["executor_id"] == "local-default"
    assert node["executor_kind"] == "local"


def test_workspace_run_service_filters_runs(query_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["assemble_package"],
        workspace_id=workspace["id"],
    )
    job_db.update_job_node(job["id"], "assemble_package", status="failed")

    result = query_service.workspace_runs(
        workspace["id"], status="failed", node_key="assemble_package", job_id=None, limit=25
    )
    assert all(run["status"] == "failed" for run in result)


def test_workspace_dag_preserves_status_buckets(query_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    definition = query_service.workflows.definition(workspace["default_workflow_key"])
    WorkflowRevisionService(job_db).ensure_active_revision(workspace["id"], definition)
    job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )

    payload = query_service.workspace_dag(workspace["id"])
    assert payload["nodes"][0]["status_counts"].keys() == {
        "pending",
        "running",
        "completed",
        "failed",
        "stale",
    }


def _create_job_with_node_run(job_db, settings, workspace_id: str = "default") -> dict[str, Any]:
    workspace = job_db.create_workspace(
        workspace_id, default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job: dict[str, Any] = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["assemble_package"],
        workspace_id=workspace["id"],
    )
    log_path = make_data_relative(
        settings.data_dir / "logs" / "jobs" / "example.log", settings.data_dir
    )
    run_dir = make_data_relative(
        settings.data_dir / "jobs" / "ws" / "job" / "runs" / "node" / "token", settings.data_dir
    )
    session_dir = make_data_relative(
        settings.data_dir / "jobs" / "ws" / "job" / "runs" / "node" / "token" / "session",
        settings.data_dir,
    )
    job_db.start_node_run(
        job["id"],
        "assemble_package",
        ["cmd"],
        log_path,
        run_dir=run_dir,
        session_dir=session_dir,
    )
    return job


def test_list_jobs_resolves_storage_dir_absolute(query_service, job_db, settings):
    job = create_question_job(job_db, source_id="Q1")

    listed = query_service.list_jobs(job["workspace_id"])

    assert len(listed) == 1
    expected_suffix = f"{job['workspace_id']}/{job['id']}"
    assert listed[0]["storage_dir"] == str(settings.jobs_dir / expected_suffix)
    assert Path(listed[0]["storage_dir"]).is_absolute()


def test_detail_resolves_storage_dir_and_run_paths_absolute(query_service, job_db, settings):
    job = _create_job_with_node_run(job_db, settings)

    detail = query_service.detail(job["id"])

    expected_suffix = f"{job['workspace_id']}/{job['id']}"
    assert detail["job"]["storage_dir"] == str(settings.jobs_dir / expected_suffix)
    assert Path(detail["job"]["storage_dir"]).is_absolute()

    assert len(detail["runs"]) == 1
    run = detail["runs"][0]
    assert run["log_path"] == str(settings.data_dir / "logs" / "jobs" / "example.log")
    assert run["run_dir"] == str(
        settings.data_dir / "jobs" / "ws" / "job" / "runs" / "node" / "token"
    )
    assert run["session_dir"] == str(
        settings.data_dir / "jobs" / "ws" / "job" / "runs" / "node" / "token" / "session"
    )
    assert all(Path(run[field]).is_absolute() for field in ("log_path", "run_dir", "session_dir"))


def test_workspace_runs_resolves_run_paths_absolute(query_service, job_db, settings):
    job = _create_job_with_node_run(job_db, settings)

    runs = query_service.workspace_runs(job["workspace_id"])

    assert len(runs) == 1
    run = runs[0]
    assert run["log_path"] == str(settings.data_dir / "logs" / "jobs" / "example.log")
    assert run["run_dir"] == str(
        settings.data_dir / "jobs" / "ws" / "job" / "runs" / "node" / "token"
    )
    assert run["session_dir"] == str(
        settings.data_dir / "jobs" / "ws" / "job" / "runs" / "node" / "token" / "session"
    )


def test_detail_preserves_empty_optional_run_dirs(query_service, job_db, settings):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["assemble_package"],
        workspace_id=workspace["id"],
    )
    log_path = make_data_relative(
        settings.data_dir / "logs" / "jobs" / "empty.log", settings.data_dir
    )
    job_db.start_node_run(job["id"], "assemble_package", ["cmd"], log_path)

    detail = query_service.detail(job["id"])

    run = detail["runs"][0]
    assert run["log_path"] == str(settings.data_dir / "logs" / "jobs" / "empty.log")
    assert run["run_dir"] == ""
    assert run["session_dir"] == ""


def test_query_service_does_not_mutate_repository_records(query_service, job_db, settings):
    job = _create_job_with_node_run(job_db, settings)
    original_job = job_db.get_job(job["id"])
    original_run = job_db.list_node_runs(job["id"])[0]
    original_storage_dir = original_job["storage_dir"]
    original_log_path = original_run["log_path"]

    query_service.detail(job["id"])
    query_service.list_jobs(job["workspace_id"])
    query_service.workspace_runs(job["workspace_id"])

    assert job_db.get_job(job["id"])["storage_dir"] == original_storage_dir
    assert job_db.list_node_runs(job["id"])[0]["log_path"] == original_log_path


def test_job_detail_includes_workflow_revision_and_outcome(query_service, job_db):
    workspace = job_db.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=["fetch_questions"],
        workspace_id=workspace["id"],
        workflow_revision_id="question_comprehension_info:v1",
        workflow_definition_hash="hash1",
        workflow_definition_snapshot_json='{"key":"question_comprehension_info"}',
    )
    job_db.update_job_status(job["id"], "completed")
    job_db.update_job_outcome(job["id"], "non_uploadable")

    detail = query_service.detail(job["id"])["job"]

    assert detail["workflow_revision_id"] == "question_comprehension_info:v1"
    assert detail["workflow_definition_hash"] == "hash1"
    assert detail["outcome"] == "non_uploadable"
    assert "current_workflow_revision_id" in detail
    assert "current_workflow_revision_version" in detail


def _create_two_node_job(job_db) -> dict[str, Any]:
    workspace = job_db.get_workspace("default") or job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job: dict[str, Any] = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )
    return job


def _bind_remote_executor(job_db, settings, workspace_id: str, node_key: str) -> None:
    settings.executor_definitions["remote-test"] = RemoteExecutorConfig(
        kind="remote",
        global_capacity=2,
        capabilities={"cap_a": RemoteCapabilityConfig(skill="video_knowledge/transcribe_video")},
    )
    job_db.replace_workspace_executor_configuration(
        workspace_id,
        allocations=[{"executor_id": "remote-test", "concurrency_limit": 2}],
        bindings=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": node_key,
                "executor_id": "remote-test",
            }
        ],
        node_limits=[],
    )


def _insert_active_lease(job_db, job: dict[str, Any], node_key: str, execution_id: str) -> None:
    run = job_db.start_node_run(
        job["id"], node_key, ["cmd"], f"logs/jobs/{job['id']}-{node_key}.log"
    )
    assert run is not None
    now = datetime.now(UTC)
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into executor_leases(
                id, execution_id, executor_id, workspace_id, job_id, workflow_key,
                node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                f"lease-{execution_id}",
                execution_id,
                "remote-test",
                job["workspace_id"],
                job["id"],
                job["workflow_key"],
                node_key,
                run["id"],
                _database_timestamp(now),
                _database_timestamp(now),
                _database_timestamp(now + timedelta(seconds=300)),
            ),
        )


def _submit_remote_execution(job_db, settings, execution_id: str, job, node_key: str):
    broker = RemoteExecutionBroker(job_db.path, settings.data_dir / "remote_bundles")
    broker.submit(
        RemoteExecutionPayload(
            execution_id=execution_id,
            lease_id=f"lease-{execution_id}",
            job_id=job["id"],
            node_key=node_key,
            capability="cap_a",
            bundle_name=f"{execution_id}.tar.gz",
            manifest={"job_id": job["id"], "node_key": node_key},
        )
    )
    return broker


def test_job_detail_projects_claimed_remote_worker(query_service, job_db, settings):
    job = _create_two_node_job(job_db)
    _bind_remote_executor(job_db, settings, job["workspace_id"], "question_understanding")
    _insert_active_lease(job_db, job, "question_understanding", "exec-remote-1")
    broker = _submit_remote_execution(
        job_db, settings, "exec-remote-1", job, "question_understanding"
    )
    claimed = broker.dequeue("worker-mac-1", ["cap_a"])
    assert claimed is not None and claimed.execution_id == "exec-remote-1"

    detail = query_service.detail(job["id"])

    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["question_understanding"]["executor_id"] == "remote-test"
    assert nodes["question_understanding"]["executor_kind"] == "remote"
    assert nodes["question_understanding"]["worker_id"] == "worker-mac-1"
    assert nodes["assemble_package"]["worker_id"] is None


def test_job_detail_worker_id_none_while_execution_queued(query_service, job_db, settings):
    job = _create_two_node_job(job_db)
    _bind_remote_executor(job_db, settings, job["workspace_id"], "question_understanding")
    _insert_active_lease(job_db, job, "question_understanding", "exec-remote-2")
    _submit_remote_execution(job_db, settings, "exec-remote-2", job, "question_understanding")

    detail = query_service.detail(job["id"])

    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["question_understanding"]["executor_kind"] == "remote"
    assert nodes["question_understanding"]["worker_id"] is None


def test_job_detail_worker_id_none_after_lease_released(query_service, job_db, settings):
    job = _create_two_node_job(job_db)
    _bind_remote_executor(job_db, settings, job["workspace_id"], "question_understanding")
    _insert_active_lease(job_db, job, "question_understanding", "exec-remote-3")
    broker = _submit_remote_execution(
        job_db, settings, "exec-remote-3", job, "question_understanding"
    )
    assert broker.dequeue("worker-mac-2", ["cap_a"]) is not None
    with job_db.connect() as conn:
        conn.execute(
            "update executor_leases set status='released' where execution_id=?",
            ("exec-remote-3",),
        )

    detail = query_service.detail(job["id"])

    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["question_understanding"]["worker_id"] is None
