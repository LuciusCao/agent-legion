from contextlib import contextmanager
from typing import Any

import pytest

from server.app.services.job_queries import JobQueryService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.storage_paths import resolve_job_dir


@pytest.fixture
def query_service(job_db, settings):
    return JobQueryService(
        job_db,
        settings,
        WorkflowCatalogService(settings),
        WorkspaceExecutorConfigurationService(job_db),
    )


def create_question_job(job_db, source_id: str) -> dict[str, Any]:
    workspace = job_db.get_workspace("default") or job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content",
        "direct_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace["id"],
    )
    job: dict[str, Any] = job_db.create_job(
        workflow_key="question_content",
        source_type="question",
        source_id=source_id,
        batch_id=batch["id"],
        title=f"Question {source_id}",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )
    return job


def test_job_query_service_lists_jobs(query_service, job_db):
    job_db.create_workspace("default")
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


def test_list_jobs_loads_nodes_in_one_query(query_service, job_db, monkeypatch):
    for source_id in ("Q1", "Q2", "Q3"):
        create_question_job(job_db, source_id=source_id)
    statements: list[str] = []
    original = job_db._connect_read

    @contextmanager
    def traced():
        with original() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(job_db, "_connect_read", traced)
    query_service.list_jobs("default")

    node_selects = [sql for sql in statements if "from job_nodes" in sql.lower()]
    assert len(node_selects) == 1


def test_job_query_service_detail_enriches_nodes(query_service, job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        workflow_key="question_content",
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


def test_job_query_service_detail_lists_artifacts_from_relative_storage_dir(
    query_service, job_db, settings
):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        workflow_key="question_content",
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
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        workflow_key="question_content",
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
            {"executor_id": "pi-default", "concurrency_limit": 1},
        ],
        bindings=[
            {
                "workflow_key": "question_content",
                "node_key": "question_understanding",
                "executor_id": "pi-default",
            },
            {
                "workflow_key": "question_content",
                "node_key": "assemble_package",
                "executor_id": "local-default",
            },
        ],
        node_limits=[],
    )

    detail = query_service.detail(job["id"])

    nodes = {node["node_key"]: node for node in detail["nodes"]}
    assert nodes["question_understanding"]["executor_id"] == "pi-default"
    assert nodes["question_understanding"]["executor_kind"] == "pi"
    assert nodes["assemble_package"]["executor_id"] == "local-default"
    assert nodes["assemble_package"]["executor_kind"] == "local"


def test_job_detail_resolves_executor_binding_for_job_workflow_only(query_service, job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        workflow_key="question_content",
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
            {"executor_id": "pi-default", "concurrency_limit": 1},
        ],
        bindings=[
            {
                "workflow_key": "question_content",
                "node_key": "assemble_package",
                "executor_id": "local-default",
            },
            {
                "workflow_key": "reading_analysis",
                "node_key": "assemble_package",
                "executor_id": "pi-default",
            },
        ],
        node_limits=[],
    )

    detail = query_service.detail(job["id"])

    node = detail["nodes"][0]
    assert node["executor_id"] == "local-default"
    assert node["executor_kind"] == "local"


def test_workspace_run_service_filters_runs(query_service, job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        workflow_key="question_content",
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
    workspace = job_db.create_workspace("default")
    job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )

    payload = query_service.workspace_dag(workspace["id"])
    assert payload["nodes"][0]["status_counts"].keys() == {
        "pending",
        "running",
        "completed",
        "failed",
        "stale",
    }
