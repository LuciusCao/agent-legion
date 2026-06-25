from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.app.executors._lease_transactions import _sqlite_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_execution import JobExecutionService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.registry import load_registered_workflow


@pytest.fixture
def execution_service(job_db: JobQueries, settings):
    return JobExecutionService(
        job_db,
        JobArtifactMutationService(settings.jobs_dir),
        ExecutorLeaseRepository(job_db.path),
        WorkflowCatalogService(settings),
    )


@pytest.fixture
def workspace(job_db: JobQueries):
    return job_db.create_workspace("exec-ws", default_workflow_key="question_comprehension_info")


def _create_job(
    job_db: JobQueries,
    workspace_id: str,
    source_id: str = "Q1",
    workflow_key: str = "question_comprehension_info",
) -> dict[str, Any]:
    batch = job_db.create_batch(
        workflow_key,
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace_id,
    )
    definition = load_registered_workflow(Path(".").resolve(), workflow_key)
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
    run = job_db.start_node_run(
        job["id"], node_key, ["cmd"], f"logs/jobs/{job['id']}-{node_key}.log"
    )
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
    job_db.set_job_execution_target(job["id"], "clean_and_parse")
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
    job_db.update_job_node(job["id"], "fetch_questions", status="completed")
    job_db.update_job_node(job["id"], "clean_and_parse", status="completed")
    job_db.set_job_execution_target(job["id"], "clean_and_parse")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=?", (job["id"],))

    result = execution_service.run_to(workspace["id"], job["id"], "generate_key_info")

    assert result["status"] == "succeeded"
    assert result["node_key"] == "generate_key_info"
    job_after = job_db.get_job(job["id"])
    assert job_after["status"] == "queued"
    assert job_after["execution_mode"] == "until_node"
    assert job_after["target_node_key"] == "generate_key_info"
    assert job_after["execution_paused"] == 0
    assert job_after["pause_reason"] == ""
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["fetch_questions"] == "completed"
    assert statuses["clean_and_parse"] == "completed"
    assert statuses["generate_key_info"] == "pending"


def test_run_to_with_start_unpauses_target_reached_job(
    execution_service: JobExecutionService, job_db: JobQueries, workspace, settings
):
    job = _create_job(job_db, workspace["id"])
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "questions_parsed.json").write_text("understanding")
    job_db.update_job_node(job["id"], "fetch_questions", status="completed")
    job_db.update_job_node(job["id"], "clean_and_parse", status="completed")
    job_db.set_job_execution_target(job["id"], "clean_and_parse")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=?", (job["id"],))

    result = execution_service.run_to(
        workspace["id"],
        job["id"],
        "generate_key_info",
        start_node_key="clean_and_parse",
    )

    assert result["status"] == "succeeded"
    job_after = job_db.get_job(job["id"])
    assert job_after["status"] == "queued"
    assert job_after["execution_paused"] == 0
    assert job_after["pause_reason"] == ""
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["clean_and_parse"] == "pending"
    assert statuses["generate_key_info"] == "stale"
    assert not (storage / "questions_parsed.json").exists()


def test_run_to_without_start_preserves_completed_ancestors(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    job_db.update_job_node(job["id"], "fetch_questions", status="completed")
    job_db.update_job_node(job["id"], "clean_and_parse", status="failed")

    result = execution_service.run_to(workspace["id"], job["id"], "clean_and_parse")

    assert result["job_id"] == job["id"]
    assert result["operation"] == "run_to"
    assert result["status"] == "succeeded"
    assert result["node_key"] == "clean_and_parse"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["fetch_questions"] == "completed"
    assert statuses["clean_and_parse"] == "pending"
    control = job_db.get_job_execution_control(job["id"])
    assert control["execution_mode"] == "until_node"
    assert control["target_node_key"] == "clean_and_parse"


def test_run_to_with_start_reruns_within_target_closure(
    execution_service: JobExecutionService, job_db: JobQueries, workspace, settings
):
    job = _create_job(job_db, workspace["id"])
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "questions.json").write_text("context")
    (storage / "questions_parsed.json").write_text("understanding")

    result = execution_service.run_to(
        workspace["id"],
        job["id"],
        "clean_and_parse",
        start_node_key="fetch_questions",
    )

    assert result["status"] == "succeeded"
    assert result["node_key"] == "clean_and_parse"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["fetch_questions"] == "pending"
    assert statuses["clean_and_parse"] == "stale"
    assert not (storage / "questions.json").exists()
    assert not (storage / "questions_parsed.json").exists()
    control = job_db.get_job_execution_control(job["id"])
    assert control["target_node_key"] == "clean_and_parse"


def test_run_to_rejects_start_node_outside_target_closure(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])

    result = execution_service.run_to(
        workspace["id"],
        job["id"],
        "clean_and_parse",
        start_node_key="review_possible_errors",
    )

    assert result["job_id"] == job["id"]
    assert result["operation"] == "run_to"
    assert result["status"] == "failed"
    assert result["reason_code"] == "invalid_start"
    assert "review_possible_errors" in (result["message"] or "")


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
    _create_active_lease(job_db, job, "fetch_questions")

    result = execution_service.run_to(workspace["id"], job["id"], "clean_and_parse")

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

    result = execution_service.run_to(workspace["id"], job["id"], "clean_and_parse")

    assert result["status"] == "succeeded"
    assert calls == [
        (
            job["id"],
            "clean_and_parse",
            frozenset({"fetch_questions", "clean_and_parse"}),
        )
    ]


def test_run_to_atomic_guard_catches_lease_created_after_precheck(
    execution_service: JobExecutionService, job_db: JobQueries, workspace, monkeypatch
):
    job = _create_job(job_db, workspace["id"])
    original = job_db.apply_run_to_atomic

    monkeypatch.setattr(execution_service, "_has_active_lease", lambda _job_id: False)

    def race(job_id: str, target_node_key: str, closure: frozenset[str], *, now=None):
        _create_active_lease(job_db, job, "fetch_questions")
        original(job_id, target_node_key, closure, now=now)

    monkeypatch.setattr(job_db, "apply_run_to_atomic", race)

    result = execution_service.run_to(workspace["id"], job["id"], "clean_and_parse")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"
    assert _node_statuses(job_db, job["id"])["fetch_questions"] == "running"


def test_run_to_skips_already_completed_target(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    job_db.update_job_node(job["id"], "fetch_questions", status="completed")
    job_db.update_job_node(job["id"], "clean_and_parse", status="completed")

    result = execution_service.run_to(workspace["id"], job["id"], "clean_and_parse")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "target_already_completed"


def test_continue_full_dag_after_target_reached(
    execution_service: JobExecutionService, job_db: JobQueries, workspace
):
    job = _create_job(job_db, workspace["id"])
    job_db.set_job_execution_target(job["id"], "clean_and_parse")
    job_db.pause_job(job["id"], "target_reached")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=?", (job["id"],))
        conn.execute(
            "update job_nodes set status='completed' where job_id=? and node_key in ('fetch_questions', 'clean_and_parse')",
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

    result = execution_service.run_to("other-ws", job["id"], "clean_and_parse")

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
        "clean_and_parse",
    )

    assert len(results) == 2
    assert results[0]["job_id"] == job["id"]
    assert results[0]["status"] == "succeeded"
    assert results[1]["job_id"] == "missing-job"
    assert results[1]["status"] == "failed"
    assert results[1]["reason_code"] == "not_found"
