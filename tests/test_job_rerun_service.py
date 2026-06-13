from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.app.executors._lease_transactions import _sqlite_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService, StagedOutputs
from server.app.services.job_errors import NotFoundError
from server.app.services.job_rerun import JobRerunService
from server.app.services.pipeline_catalog import PipelineCatalogService


@pytest.fixture
def rerun_service(job_db, settings):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db.path),
        settings,
        PipelineCatalogService(settings),
        JobArtifactMutationService(),
    )


@pytest.fixture
def job(job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    return job_db.create_job(
        pipeline_key="question_content",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=[
            "fetch_question_context",
            "question_understanding",
            "misconception_analysis",
            "natural_language_reading",
            "solution_decomposition",
            "faq_generation",
            "content_graph_generation",
            "interactive_template_generation",
            "content_review",
            "assemble_package",
        ],
        workspace_id=workspace["id"],
    )


@pytest.fixture
def running_job(job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        pipeline_key="question_content",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )
    job_db.update_job_node(job["id"], "fetch_question_context", status="running")
    return job


def _create_lease(
    job_db: JobQueries,
    job: dict[str, Any],
    node_key: str,
    *,
    expires_offset_seconds: float,
) -> dict[str, Any]:
    run = job_db.start_node_run(job["id"], node_key, ["cmd"], "/dev/null")
    assert run is not None
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=expires_offset_seconds)
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into executor_leases(
                id, execution_id, executor_id, workspace_id, job_id, pipeline_key,
                node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                "lease-1",
                "exec-1",
                "local-default",
                job["workspace_id"],
                job["id"],
                job["pipeline_key"],
                node_key,
                run["id"],
                _sqlite_timestamp(now),
                _sqlite_timestamp(now),
                _sqlite_timestamp(expires),
            ),
        )
    return run


def test_rerun_selected_node_and_descendants_are_stale(rerun_service, job):
    result = rerun_service.rerun(job["workspace_id"], job["id"], "question_understanding")

    assert result["job_id"] == job["id"]
    assert result["node_key"] == "question_understanding"
    assert result["status"] == "succeeded"

    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["question_understanding"] == "pending"
    assert nodes["misconception_analysis"] == "stale"
    assert nodes["natural_language_reading"] == "stale"
    assert nodes["solution_decomposition"] == "stale"
    assert nodes["faq_generation"] == "stale"
    assert nodes["content_graph_generation"] == "stale"
    assert nodes["interactive_template_generation"] == "stale"
    assert nodes["content_review"] == "stale"
    assert nodes["assemble_package"] == "stale"


def test_rerun_preserves_ancestors(rerun_service, job):
    rerun_service.job_db.update_job_node(job["id"], "fetch_question_context", status="completed")

    result = rerun_service.rerun(job["workspace_id"], job["id"], "question_understanding")

    assert result["status"] == "succeeded"
    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["fetch_question_context"] == "completed"


def test_rerun_rejects_running_job(rerun_service, running_job):
    result = rerun_service.rerun(
        running_job["workspace_id"], running_job["id"], "fetch_question_context"
    )

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"


def test_rerun_rejects_active_lease(rerun_service, job):
    _create_lease(rerun_service.job_db, job, "question_understanding", expires_offset_seconds=300)

    result = rerun_service.rerun(job["workspace_id"], job["id"], "question_understanding")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"


def test_rerun_uses_atomic_lease_guarded_mutation(rerun_service, job, monkeypatch):
    calls: list[str] = []
    original = rerun_service.job_db.lease_guarded_mutation

    @contextmanager
    def tracked(job_id: str, now, *, reject_running_nodes: bool):
        calls.append(job_id)
        with original(job_id, now, reject_running_nodes=reject_running_nodes) as conn:
            yield conn

    monkeypatch.setattr(rerun_service.job_db, "lease_guarded_mutation", tracked)

    result = rerun_service.rerun(job["workspace_id"], job["id"], "question_understanding")

    assert result["status"] == "succeeded"
    assert calls == [job["id"]]


def test_rerun_atomic_guard_catches_lease_created_after_precheck(rerun_service, job, monkeypatch):
    original = rerun_service.job_db.lease_guarded_mutation
    created = False

    monkeypatch.setattr(rerun_service.lease_repo, "has_active_for_node", lambda *args: False)
    monkeypatch.setattr(rerun_service, "_job_has_running_nodes", lambda _job_id: False)

    @contextmanager
    def race(job_id: str, now, *, reject_running_nodes: bool):
        nonlocal created
        if not created:
            _create_lease(
                rerun_service.job_db,
                job,
                "question_understanding",
                expires_offset_seconds=300,
            )
            created = True
        with original(job_id, now, reject_running_nodes=reject_running_nodes) as conn:
            yield conn

    monkeypatch.setattr(rerun_service.job_db, "lease_guarded_mutation", race)

    result = rerun_service.rerun(job["workspace_id"], job["id"], "question_understanding")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"
    assert (
        rerun_service.job_db.get_job_node(job["id"], "question_understanding")["status"]
        == "running"
    )


def test_rerun_node_not_found(rerun_service, job):
    result = rerun_service.rerun(job["workspace_id"], job["id"], "nonexistent")

    assert result["status"] == "failed"
    assert result["reason_code"] == "node_not_found"


def test_rerun_job_not_found(rerun_service):
    result = rerun_service.rerun("default", "missing", "question_understanding")

    assert result["status"] == "failed"
    assert result["reason_code"] == "not_found"


def test_rerun_wrong_workspace(rerun_service, job):
    result = rerun_service.rerun("other", job["id"], "question_understanding")

    assert result["status"] == "failed"
    assert result["reason_code"] == "wrong_workspace"


def test_rerun_stages_and_removes_artifacts(rerun_service, job):
    storage = Path(job["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "understanding.json").write_text("understanding")
    (storage / "misconceptions.json").write_text("misconceptions")
    (storage / "fetch.log").write_text("log")

    result = rerun_service.rerun(job["workspace_id"], job["id"], "question_understanding")

    assert result["status"] == "succeeded"
    assert not (storage / "understanding.json").exists()
    assert not (storage / "misconceptions.json").exists()
    assert (storage / "fetch.log").exists()


def test_rerun_rolls_back_artifacts_when_db_fails(rerun_service, job, monkeypatch):
    storage = Path(job["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "understanding.json").write_text("understanding")

    def _fail(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        rerun_service.job_db,
        "mark_nodes_for_rerun_in_transaction",
        _fail,
    )

    result = rerun_service.rerun(job["workspace_id"], job["id"], "question_understanding")

    assert result["status"] == "failed"
    assert (storage / "understanding.json").read_text() == "understanding"


def test_rerun_reports_success_when_post_commit_cleanup_fails(
    rerun_service, job, monkeypatch, caplog
):
    storage = Path(job["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "understanding.json").write_text("understanding")

    def _fail_commit(self):
        raise OSError("cleanup failed")

    monkeypatch.setattr(StagedOutputs, "commit", _fail_commit)

    result = rerun_service.rerun(job["workspace_id"], job["id"], "question_understanding")

    assert result["status"] == "succeeded"
    assert "cleanup failed" in caplog.text
    assert (
        rerun_service.job_db.get_job_node(job["id"], "question_understanding")["status"]
        == "pending"
    )


def test_rerun_expired_lease_is_not_blocking(rerun_service, job):
    run = _create_lease(
        rerun_service.job_db, job, "question_understanding", expires_offset_seconds=-1
    )
    rerun_service.job_db.finish_node_run(run["id"], "failed", 1, "expired")

    result = rerun_service.rerun(job["workspace_id"], job["id"], "question_understanding")

    assert result["status"] == "succeeded"
    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["question_understanding"] == "pending"


def test_batch_rerun_returns_results_in_request_order(rerun_service, job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content",
        "direct_ids",
        {"question_ids": ["Q1", "Q2"]},
        workspace_id=workspace["id"],
    )
    jobs = []
    for qid in ["Q1", "Q2"]:
        jobs.append(
            job_db.create_job(
                pipeline_key="question_content",
                source_type="question",
                source_id=qid,
                batch_id=batch["id"],
                title=f"Question {qid}",
                node_keys=["fetch_question_context", "question_understanding"],
                workspace_id=workspace["id"],
            )
        )

    results = rerun_service.batch_rerun(
        workspace["id"], [jobs[1]["id"], jobs[0]["id"]], "question_understanding"
    )

    assert [r["job_id"] for r in results] == [jobs[1]["id"], jobs[0]["id"]]
    assert all(r["status"] == "succeeded" for r in results)


def test_batch_rerun_node_not_found_for_one_job(rerun_service, job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        pipeline_key="question_content",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )

    results = rerun_service.batch_rerun(workspace["id"], [job["id"]], "question_understanding")

    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["reason_code"] == "node_not_found"


def test_batch_rerun_mixed_pipelines(rerun_service, job_db):
    workspace = job_db.create_workspace("default")
    q_batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    r_batch = job_db.create_batch(
        "reading_analysis", "batch_by_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    q_job = job_db.create_job(
        pipeline_key="question_content",
        source_type="question",
        source_id="Q1",
        batch_id=q_batch["id"],
        title="Question 1",
        node_keys=["fetch_question_context", "question_understanding"],
        workspace_id=workspace["id"],
    )
    r_job = job_db.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q1",
        batch_id=r_batch["id"],
        title="Reading Q1",
        node_keys=["fetch_questions", "clean_and_parse"],
        workspace_id=workspace["id"],
    )

    results = rerun_service.batch_rerun(
        workspace["id"], [q_job["id"], r_job["id"]], "question_understanding"
    )

    assert results[0]["status"] == "succeeded"
    assert results[1]["status"] == "failed"
    assert results[1]["reason_code"] == "node_not_found"


def test_job_delete_removes_storage_and_logs(rerun_service, job, settings):
    storage = Path(job["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    log = settings.logs_dir / "jobs" / f"{job['id']}-node.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("log", encoding="utf-8")

    rerun_service.delete(job["id"])

    assert not storage.exists()
    assert not log.exists()


def test_job_delete_rejects_missing_job(rerun_service):
    with pytest.raises(NotFoundError, match="Job not found"):
        rerun_service.delete("missing")
