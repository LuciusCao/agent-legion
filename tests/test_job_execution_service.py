from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.app.executors._lease_transactions import _sqlite_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.pipelines.registry import load_registered_pipeline
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_execution import JobExecutionService
from server.app.services.pipeline_catalog import PipelineCatalogService


@pytest.fixture
def execution_service(job_db: JobQueries, settings):
    return JobExecutionService(
        job_db,
        JobArtifactMutationService(settings.jobs_dir),
        ExecutorLeaseRepository(job_db.path),
        PipelineCatalogService(settings),
    )


@pytest.fixture
def workspace(job_db: JobQueries):
    return job_db.create_workspace("exec-ws")


def _create_job(
    job_db: JobQueries,
    workspace_id: str,
    source_id: str = "Q1",
    workflow_key: str = "question_content",
) -> dict[str, Any]:
    batch = job_db.create_batch(
        workflow_key,
        "direct_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace_id,
    )
    definition = load_registered_pipeline(Path(".").resolve(), workflow_key)
    return job_db.create_job(
        workflow_key=workflow_key,
        source_type="question",
        source_id=source_id,
        batch_id=batch["id"],
        title=f"Question {source_id}",
        node_keys=list(definition.nodes),
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
    run = job_db.start_node_run(job["id"], node_key, ["cmd"], "/dev/null")
    assert run is not None
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=expires_offset_seconds)
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
                f"lease-{node_key}",
                f"exec-{node_key}",
                "local-default",
                job["workspace_id"],
                job["id"],
                job["workflow_key"],
                node_key,
                run["id"],
                _sqlite_timestamp(now),
                _sqlite_timestamp(now),
                _sqlite_timestamp(expires),
            ),
        )


def test_continue_to_target(execution_service: JobExecutionService, job_db: JobQueries, workspace):
    job = _create_job(job_db, workspace["id"])
    job_db.set_job_execution_target(job["id"], "question_understanding")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=?", (job["id"],))

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
    job_db.update_job_node(job["id"], "fetch_question_context", status="completed")
    job_db.update_job_node(job["id"], "question_understanding", status="completed")
    job_db.set_job_execution_target(job["id"], "question_understanding")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=?", (job["id"],))

    result = execution_service.run_to(workspace["id"], job["id"], "misconception_analysis")

    assert result["status"] == "succeeded"
    assert result["node_key"] == "misconception_analysis"
    job_after = job_db.get_job(job["id"])
    assert job_after["status"] == "queued"
    assert job_after["execution_mode"] == "until_node"
    assert job_after["target_node_key"] == "misconception_analysis"
    assert job_after["execution_paused"] == 0
    assert job_after["pause_reason"] == ""
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["fetch_question_context"] == "completed"
    assert statuses["question_understanding"] == "completed"
    assert statuses["misconception_analysis"] == "pending"


def test_run_to_with_start_unpauses_target_reached_job(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    storage = Path(job["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "understanding.json").write_text("understanding")
    job_db.update_job_node(job["id"], "fetch_question_context", status="completed")
    job_db.update_job_node(job["id"], "question_understanding", status="completed")
    job_db.set_job_execution_target(job["id"], "question_understanding")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=?", (job["id"],))

    result = execution_service.run_to(
        workspace["id"],
        job["id"],
        "misconception_analysis",
        start_node_key="question_understanding",
    )

    assert result["status"] == "succeeded"
    job_after = job_db.get_job(job["id"])
    assert job_after["status"] == "queued"
    assert job_after["execution_paused"] == 0
    assert job_after["pause_reason"] == ""
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["question_understanding"] == "pending"
    assert statuses["misconception_analysis"] == "stale"
    assert not (storage / "understanding.json").exists()


def test_run_to_without_start_preserves_completed_ancestors(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    job_db.update_job_node(job["id"], "fetch_question_context", status="completed")
    job_db.update_job_node(job["id"], "question_understanding", status="failed")

    result = execution_service.run_to(workspace["id"], job["id"], "question_understanding")

    assert result["job_id"] == job["id"]
    assert result["operation"] == "run_to"
    assert result["status"] == "succeeded"
    assert result["node_key"] == "question_understanding"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["fetch_question_context"] == "completed"
    assert statuses["question_understanding"] == "pending"
    control = job_db.get_job_execution_control(job["id"])
    assert control["execution_mode"] == "until_node"
    assert control["target_node_key"] == "question_understanding"


def test_run_to_with_start_reruns_within_target_closure(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    storage = Path(job["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "question_context.json").write_text("context")
    (storage / "understanding.json").write_text("understanding")

    result = execution_service.run_to(
        workspace["id"],
        job["id"],
        "question_understanding",
        start_node_key="fetch_question_context",
    )

    assert result["status"] == "succeeded"
    assert result["node_key"] == "question_understanding"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["fetch_question_context"] == "pending"
    assert statuses["question_understanding"] == "stale"
    assert not (storage / "question_context.json").exists()
    assert not (storage / "understanding.json").exists()
    control = job_db.get_job_execution_control(job["id"])
    assert control["target_node_key"] == "question_understanding"


def test_run_to_rejects_start_node_outside_target_closure(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])

    result = execution_service.run_to(
        workspace["id"],
        job["id"],
        "question_understanding",
        start_node_key="content_review",
    )

    assert result["job_id"] == job["id"]
    assert result["operation"] == "run_to"
    assert result["status"] == "failed"
    assert result["reason_code"] == "invalid_start"
    assert "content_review" in (result["message"] or "")


def test_run_to_rejects_unknown_target(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])

    result = execution_service.run_to(workspace["id"], job["id"], "nonexistent_target")

    assert result["status"] == "failed"
    assert result["reason_code"] == "node_not_found"


def test_run_to_rejects_active_lease(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    _create_active_lease(job_db, job, "fetch_question_context")

    result = execution_service.run_to(workspace["id"], job["id"], "question_understanding")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"


def test_run_to_uses_atomic_execution_control_mutation(
    execution_service: JobExecutionService, job_db: JobQueries, workspace, monkeypatch
):
    job = _create_job(job_db, workspace["id"])
    calls: list[tuple[str, str, frozenset[str]]] = []

    original = job_db.apply_run_to_atomic

    def tracked(
        job_id: str,
        target_node_key: str,
        closure: frozenset[str],
        *,
        now=None,
    ) -> None:
        calls.append((job_id, target_node_key, closure))
        original(job_id, target_node_key, closure, now=now)

    monkeypatch.setattr(job_db, "apply_run_to_atomic", tracked)

    result = execution_service.run_to(workspace["id"], job["id"], "question_understanding")

    assert result["status"] == "succeeded"
    assert calls == [
        (
            job["id"],
            "question_understanding",
            frozenset({"fetch_question_context", "question_understanding"}),
        )
    ]


def test_run_to_atomic_guard_catches_lease_created_after_precheck(
    execution_service: JobExecutionService, job_db: JobQueries, workspace, monkeypatch
):
    job = _create_job(job_db, workspace["id"])
    original = job_db.apply_run_to_atomic

    monkeypatch.setattr(execution_service, "_has_active_lease", lambda _job_id: False)

    def race(job_id: str, target_node_key: str, closure: frozenset[str], *, now=None):
        _create_active_lease(job_db, job, "fetch_question_context")
        original(job_id, target_node_key, closure, now=now)

    monkeypatch.setattr(job_db, "apply_run_to_atomic", race)

    result = execution_service.run_to(workspace["id"], job["id"], "question_understanding")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"
    assert _node_statuses(job_db, job["id"])["fetch_question_context"] == "running"


def test_run_to_skips_already_completed_target(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    job_db.update_job_node(job["id"], "fetch_question_context", status="completed")
    job_db.update_job_node(job["id"], "question_understanding", status="completed")

    result = execution_service.run_to(workspace["id"], job["id"], "question_understanding")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "target_already_completed"


def test_continue_full_dag_after_target_reached(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    job_db.set_job_execution_target(job["id"], "question_understanding")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=?", (job["id"],))
        conn.execute(
            "update job_nodes set status='completed' where job_id=? and node_key in ('fetch_question_context', 'question_understanding')",
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

    result = execution_service.run_to("other-ws", job["id"], "question_understanding")

    assert result["status"] == "failed"
    assert result["reason_code"] == "wrong_workspace"


def test_continue_rejects_wrong_workspace(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])

    result = execution_service.continue_job("other-ws", job["id"])

    assert result["status"] == "failed"
    assert result["reason_code"] == "wrong_workspace"


def test_batch_run_to_returns_mixed_results_in_request_order(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"], source_id="Q1")

    results = execution_service.batch_run_to(
        workspace["id"],
        [job["id"], "missing-job"],
        "question_understanding",
    )

    assert len(results) == 2
    assert results[0]["job_id"] == job["id"]
    assert results[0]["status"] == "succeeded"
    assert results[1]["job_id"] == "missing-job"
    assert results[1]["status"] == "failed"
    assert results[1]["reason_code"] == "not_found"
