from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.app.executors._lease_transactions import database_timestamp
from server.app.jobs.storage_layout import job_shard
from server.app.services.job_queries import JobQueryService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.storage_paths import make_data_relative, resolve_job_dir
from tests.helpers import (
    load_builtin_definition,
    publish_builtin_revision,
    seed_workspace_agent_definitions,
)


@pytest.fixture
def query_service(job_db, settings):
    return JobQueryService(
        job_db,
        settings,
        WorkspaceExecutorConfigurationService(job_db),
    )


def create_question_job(job_db, source_id: str) -> dict[str, Any]:
    workspace = job_db.get_workspace("default") or job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    # Jobs created without an intake snapshot resolve their definition from
    # the workspace's active revision (schema v50), so publish it.
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace["id"],
    )
    job: dict[str, Any] = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id=source_id,
        batch_id=batch["id"],
        title=f"Question {source_id}",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )
    return job


def test_job_query_service_lists_jobs(query_service, job_db):
    job_db.create_workspace("default", default_workflow_key="education_video_problems_generation")
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
        "versioned", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    revision_service = WorkflowRevisionService(job_db)
    original = revision_service.publish_workspace_revision(workspace["id"], definition)
    current = revision_service.publish_workspace_revision(workspace["id"], definition)
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job_db.create_job(
        workflow_key="education_video_problems_generation",
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
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=[
            "publish_content",
            "review_questions",
            "generate_questions",
            "review_script",
            "write_script",
            "intake_knowledge_points",
        ],
        workspace_id=workspace["id"],
    )

    listed = query_service.list_jobs(job["workspace_id"])

    node_keys = [node["node_key"] for node in listed[0]["node_summaries"]]
    # Ordering is topological over the workspace's ACTIVE revision (schema
    # v50); ready-ties break by the stored definition's node order (canonical
    # JSON, alphabetical) — identical to what intake and Studio already use.
    assert node_keys[:3] == [
        "intake_knowledge_points",
        "generate_questions",
        "review_questions",
    ]
    assert node_keys.index("publish_content") > node_keys.index("review_questions")


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
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
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
        # P-0.5：非 Agent 路由节点投影为常量 code 池。
        assert node["executor_id"] == "code"
        assert node["executor_kind"] == "code"


def test_job_query_service_detail_orders_nodes_and_uses_edge_dependencies(query_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=[
            "publish_content",
            "generate_questions",
            "write_script",
            "intake_knowledge_points",
        ],
        workspace_id=workspace["id"],
    )

    detail = query_service.detail(job["id"])

    assert [node["node_key"] for node in detail["nodes"]] == [
        "intake_knowledge_points",
        "generate_questions",
        "publish_content",
        "write_script",
    ]
    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["write_script"]["after"] == ["intake_knowledge_points"]


def test_job_query_service_detail_lists_artifacts_from_relative_storage_dir(
    query_service, job_db, settings
):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
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


def test_job_detail_projects_agent_route_over_code_pool(query_service, job_db):
    """P-0.5：Agent 路由节点投影 agent_id（executor 字段为空），其余一律
    常量 code 池，不再读任何 executor 配置。"""
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_routes(workspace_id, workflow_key, node_key,"
            " target_kind, target_id) values (%s, %s, %s, 'agent', 'agent-v1')",
            (workspace["id"], "education_video_problems_generation", "assemble_package"),
        )

    detail = query_service.detail(job["id"])

    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["question_understanding"]["executor_id"] == "code"
    assert nodes["question_understanding"]["executor_kind"] == "code"
    assert nodes["assemble_package"]["executor_id"] is None
    assert nodes["assemble_package"]["executor_kind"] is None
    assert nodes["assemble_package"]["agent_id"] == "agent-v1"


def test_workspace_run_service_filters_runs(query_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
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
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    definition = load_builtin_definition(workspace["default_workflow_key"])
    WorkflowRevisionService(job_db).ensure_active_revision(workspace["id"], definition)
    job_db.create_batch(
        "education_video_problems_generation",
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
        workspace_id, default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job: dict[str, Any] = job_db.create_job(
        workflow_key="education_video_problems_generation",
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
    expected_suffix = f"{job['workspace_id']}/{job_shard(job['id'])}/{job['id']}"
    assert listed[0]["storage_dir"] == str(settings.jobs_dir / expected_suffix)
    assert Path(listed[0]["storage_dir"]).is_absolute()


def test_detail_resolves_storage_dir_and_run_paths_absolute(query_service, job_db, settings):
    job = _create_job_with_node_run(job_db, settings)

    detail = query_service.detail(job["id"])

    expected_suffix = f"{job['workspace_id']}/{job_shard(job['id'])}/{job['id']}"
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
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
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
    workspace = job_db.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
        workflow_revision_id="education_video_problems_generation:v1",
        workflow_definition_hash="hash1",
        workflow_definition_snapshot_json='{"key":"education_video_problems_generation"}',
    )
    job_db.update_job_status(job["id"], "completed")
    job_db.update_job_outcome(job["id"], "non_uploadable")

    detail = query_service.detail(job["id"])["job"]

    assert detail["workflow_revision_id"] == "education_video_problems_generation:v1"
    assert detail["workflow_definition_hash"] == "hash1"
    assert detail["outcome"] == "non_uploadable"
    assert "current_workflow_revision_id" in detail
    assert "current_workflow_revision_version" in detail


def _create_two_node_job(job_db) -> dict[str, Any]:
    workspace = job_db.get_workspace("default") or job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job: dict[str, Any] = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )
    return job


def _bind_agent(job_db, workspace_id: str, node_key: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_routes(workspace_id, workflow_key, node_key, target_kind, target_id)"
            " values (%s, 'education_video_problems_generation', %s, 'agent', 'example-write-script-v1')"
            " on conflict(workspace_id, workflow_key, node_key) do update set"
            " target_kind='agent', target_id=excluded.target_id",
            (workspace_id, node_key),
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
            values (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
            """,
            (
                f"lease-{execution_id}",
                execution_id,
                "agent:example-write-script-v1",
                job["workspace_id"],
                job["id"],
                job["workflow_key"],
                node_key,
                run["id"],
                database_timestamp(now),
                database_timestamp(now),
                database_timestamp(now + timedelta(seconds=300)),
            ),
        )


def _insert_agent_request(job_db, execution_id: str, job, node_key: str) -> None:
    # Agent definitions are workspace-scoped (schema v46): seed the demo
    # templates into the job's workspace before reading the published hash.
    seed_workspace_agent_definitions(str(job["workspace_id"]))
    with job_db.connect() as conn:
        definition = conn.execute(
            "select definition_hash from versioned_entities"
            " where entity_type='agent' and workspace_id=%s"
            " and entity_key='example-write-script-v1' and status='published'",
            (job["workspace_id"],),
        ).fetchone()
        conn.execute(
            "insert into agent_execution_requests(execution_id, workspace_id, job_id, workflow_key,"
            " node_key, agent_id, agent_definition_hash, node_concurrency_limit, queued_at, manifest_json)"
            " values (%s, %s, %s, %s, %s, 'example-write-script-v1', %s, 20, current_timestamp, '{}')",
            (
                execution_id,
                job["workspace_id"],
                job["id"],
                job["workflow_key"],
                node_key,
                definition["definition_hash"],
            ),
        )


def test_job_detail_projects_agent_and_claimed_worker(query_service, job_db):
    job = _create_two_node_job(job_db)
    _bind_agent(job_db, job["workspace_id"], "question_understanding")
    _insert_active_lease(job_db, job, "question_understanding", "exec-agent-1")
    _insert_agent_request(job_db, "exec-agent-1", job, "question_understanding")
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set state='claimed', worker_id='worker-mac-1'"
            " where execution_id='exec-agent-1'"
        )

    detail = query_service.detail(job["id"])

    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["question_understanding"]["agent_id"] == "example-write-script-v1"
    assert nodes["question_understanding"]["executor_id"] is None
    assert nodes["question_understanding"]["worker_id"] == "worker-mac-1"
    assert nodes["assemble_package"]["worker_id"] is None


def test_job_detail_worker_id_none_while_agent_execution_queued(query_service, job_db):
    job = _create_two_node_job(job_db)
    _bind_agent(job_db, job["workspace_id"], "question_understanding")
    _insert_active_lease(job_db, job, "question_understanding", "exec-agent-2")
    _insert_agent_request(job_db, "exec-agent-2", job, "question_understanding")

    detail = query_service.detail(job["id"])

    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["question_understanding"]["agent_id"] == "example-write-script-v1"
    assert nodes["question_understanding"]["worker_id"] is None


def test_job_detail_worker_id_none_after_agent_lease_released(query_service, job_db):
    job = _create_two_node_job(job_db)
    _bind_agent(job_db, job["workspace_id"], "question_understanding")
    _insert_active_lease(job_db, job, "question_understanding", "exec-agent-3")
    _insert_agent_request(job_db, "exec-agent-3", job, "question_understanding")
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set state='claimed', worker_id='worker-mac-2'"
            " where execution_id='exec-agent-3'"
        )
        conn.execute(
            "update executor_leases set status='released' where execution_id=%s",
            ("exec-agent-3",),
        )

    detail = query_service.detail(job["id"])

    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["question_understanding"]["worker_id"] is None
