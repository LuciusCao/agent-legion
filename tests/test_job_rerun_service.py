from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.app.executors._lease_transactions import _sqlite_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService, StagedOutputs
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import load_workflow_definition


@pytest.fixture
def rerun_service(job_db, settings):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db.path),
        settings,
        WorkflowCatalogService(settings),
        JobArtifactMutationService(settings.jobs_dir),
    )


@pytest.fixture
def job(job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    return job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=[
            "fetch_questions",
            "clean_and_parse",
            "generate_key_info",
            "review_key_info",
            "generate_possible_errors",
            "review_possible_errors",
            "assess_comprehension_difficulty",
            "assemble_comprehension_info",
            "review_possible_errors",
            "assemble_comprehension_info",
        ],
        workspace_id=workspace["id"],
    )


@pytest.fixture
def running_job(job_db):
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
        node_keys=["fetch_questions"],
        workspace_id=workspace["id"],
    )
    job_db.update_job_node(job["id"], "fetch_questions", status="running")
    return job


def _create_lease(
    job_db: JobQueries,
    job: dict[str, Any],
    node_key: str,
    *,
    expires_offset_seconds: float,
) -> dict[str, Any]:
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
                "lease-1",
                "exec-1",
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
    return run


def test_rerun_selected_node_and_descendants_are_stale(rerun_service, job):
    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

    assert result["job_id"] == job["id"]
    assert result["node_key"] == "clean_and_parse"
    assert result["status"] == "succeeded"

    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["clean_and_parse"] == "pending"
    assert nodes["generate_key_info"] == "stale"
    assert nodes["review_key_info"] == "stale"
    assert nodes["generate_possible_errors"] == "stale"
    assert nodes["review_possible_errors"] == "stale"
    assert nodes["assess_comprehension_difficulty"] == "stale"
    assert nodes["assemble_comprehension_info"] == "stale"
    assert nodes["review_possible_errors"] == "stale"
    assert nodes["assemble_comprehension_info"] == "stale"


def test_rerun_resets_node_created_at(rerun_service, job):
    old_created_at = "2026-06-09T00:00:00Z"
    with rerun_service.job_db.connect() as conn:
        conn.execute(
            "update job_nodes set created_at=? where job_id=? and node_key=?",
            (old_created_at, job["id"], "clean_and_parse"),
        )

    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

    assert result["status"] == "succeeded"
    rerun_node = rerun_service.job_db.get_job_node(job["id"], "clean_and_parse")
    assert rerun_node is not None
    assert rerun_node["created_at"] != old_created_at
    assert len(rerun_node["created_at"]) >= 19


def test_rerun_preserves_ancestors(rerun_service, job):
    rerun_service.job_db.update_job_node(job["id"], "fetch_questions", status="completed")

    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

    assert result["status"] == "succeeded"
    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["fetch_questions"] == "completed"


def test_rerun_rejects_running_job(rerun_service, running_job):
    result = rerun_service.rerun(running_job["workspace_id"], running_job["id"], "fetch_questions")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"


def test_rerun_rejects_active_lease(rerun_service, job):
    _create_lease(rerun_service.job_db, job, "clean_and_parse", expires_offset_seconds=300)

    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

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

    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

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
                "clean_and_parse",
                expires_offset_seconds=300,
            )
            created = True
        with original(job_id, now, reject_running_nodes=reject_running_nodes) as conn:
            yield conn

    monkeypatch.setattr(rerun_service.job_db, "lease_guarded_mutation", race)

    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"
    assert rerun_service.job_db.get_job_node(job["id"], "clean_and_parse")["status"] == "running"


def test_rerun_node_not_found(rerun_service, job):
    result = rerun_service.rerun(job["workspace_id"], job["id"], "nonexistent")

    assert result["status"] == "failed"
    assert result["reason_code"] == "node_not_found"


def test_rerun_job_not_found(rerun_service):
    result = rerun_service.rerun("default", "missing", "clean_and_parse")

    assert result["status"] == "failed"
    assert result["reason_code"] == "not_found"


def test_rerun_wrong_workspace(rerun_service, job):
    result = rerun_service.rerun("other", job["id"], "clean_and_parse")

    assert result["status"] == "failed"
    assert result["reason_code"] == "wrong_workspace"


def test_rerun_stages_and_removes_artifacts(rerun_service, job, settings):
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "questions_parsed.json").write_text("understanding")
    (storage / "key_info_raw.json").write_text("misconceptions")
    (storage / "fetch.log").write_text("log")

    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

    assert result["status"] == "succeeded"
    assert not (storage / "questions_parsed.json").exists()
    assert not (storage / "key_info_raw.json").exists()
    assert (storage / "fetch.log").exists()


def test_rerun_removes_affected_run_history_and_clears_database_paths(rerun_service, job, settings):
    storage = resolve_job_dir(job, settings.jobs_dir)
    affected_run = storage / "runs" / "clean_and_parse" / "run-1"
    descendant_run = storage / "runs" / "generate_key_info" / "run-2"
    ancestor_run = storage / "runs" / "fetch_questions" / "run-3"
    for run_dir in (affected_run, descendant_run, ancestor_run):
        run_dir.mkdir(parents=True)
        (run_dir / "events.jsonl").write_text("events")

    with rerun_service.job_db.connect() as conn:
        for node_key, run_dir in (
            ("clean_and_parse", affected_run),
            ("generate_key_info", descendant_run),
            ("fetch_questions", ancestor_run),
        ):
            relative = run_dir.relative_to(settings.data_dir).as_posix()
            conn.execute(
                """
                insert into node_runs(job_id, node_key, status, run_dir, session_dir)
                values (?, ?, 'completed', ?, ?)
                """,
                (job["id"], node_key, relative, f"{relative}/session"),
            )

    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

    assert result["status"] == "succeeded"
    assert not affected_run.exists()
    assert not descendant_run.exists()
    assert ancestor_run.exists()
    runs = {run["node_key"]: run for run in rerun_service.job_db.list_node_runs(job["id"])}
    assert runs["clean_and_parse"]["run_dir"] == ""
    assert runs["clean_and_parse"]["session_dir"] == ""
    assert runs["generate_key_info"]["run_dir"] == ""
    assert runs["fetch_questions"]["run_dir"] != ""


def test_rerun_rolls_back_artifacts_when_db_fails(rerun_service, job, settings, monkeypatch):
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "questions_parsed.json").write_text("understanding")

    def _fail(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        rerun_service.job_db,
        "mark_nodes_for_rerun_in_transaction",
        _fail,
    )

    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

    assert result["status"] == "failed"
    assert (storage / "questions_parsed.json").read_text() == "understanding"


def test_rerun_reports_success_when_post_commit_cleanup_fails(
    rerun_service, job, settings, monkeypatch, caplog
):
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "questions_parsed.json").write_text("understanding")

    def _fail_commit(self):
        raise OSError("cleanup failed")

    monkeypatch.setattr(StagedOutputs, "commit", _fail_commit)

    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

    assert result["status"] == "succeeded"
    assert "cleanup failed" in caplog.text
    assert rerun_service.job_db.get_job_node(job["id"], "clean_and_parse")["status"] == "pending"


def test_rerun_expired_lease_is_not_blocking(rerun_service, job):
    run = _create_lease(rerun_service.job_db, job, "clean_and_parse", expires_offset_seconds=-1)
    rerun_service.job_db.finish_node_run(run["id"], "failed", 1, "expired")

    result = rerun_service.rerun(job["workspace_id"], job["id"], "clean_and_parse")

    assert result["status"] == "succeeded"
    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["clean_and_parse"] == "pending"


def test_batch_rerun_returns_results_in_request_order(rerun_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1", "Q2"]},
        workspace_id=workspace["id"],
    )
    jobs = []
    for qid in ["Q1", "Q2"]:
        jobs.append(
            job_db.create_job(
                workflow_key="question_comprehension_info",
                source_type="question",
                source_id=qid,
                batch_id=batch["id"],
                title=f"Question {qid}",
                node_keys=["fetch_questions", "clean_and_parse"],
                workspace_id=workspace["id"],
            )
        )

    results = rerun_service.batch_rerun(
        workspace["id"], [jobs[1]["id"], jobs[0]["id"]], "clean_and_parse"
    )

    assert [r["job_id"] for r in results] == [jobs[1]["id"], jobs[0]["id"]]
    assert all(r["status"] == "succeeded" for r in results)


def test_batch_rerun_node_not_found_for_one_job(rerun_service, job_db):
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
        node_keys=["fetch_questions"],
        workspace_id=workspace["id"],
    )

    results = rerun_service.batch_rerun(workspace["id"], [job["id"]], "clean_and_parse")

    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["reason_code"] == "node_not_found"


def test_rerun_from_failed_node_uses_failed_node(rerun_service, job):
    rerun_service.job_db.update_job_status(job["id"], "failed", "boom")
    rerun_service.job_db.update_job_node(job["id"], "clean_and_parse", status="failed")

    result = rerun_service.rerun(job["workspace_id"], job["id"], from_failed_node=True)

    assert result["status"] == "succeeded"
    assert result["node_key"] == "clean_and_parse"
    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["clean_and_parse"] == "pending"


def test_rerun_from_failed_node_skips_non_failed(rerun_service, job):
    result = rerun_service.rerun(job["workspace_id"], job["id"], from_failed_node=True)

    assert result["status"] == "skipped"
    assert result["reason_code"] == "not_failed"


def test_rerun_from_failed_node_skips_when_no_failed_node(rerun_service, job):
    rerun_service.job_db.update_job_status(job["id"], "failed", "boom")

    result = rerun_service.rerun(job["workspace_id"], job["id"], from_failed_node=True)

    assert result["status"] == "skipped"
    assert result["reason_code"] == "no_failed_node"


def test_batch_rerun_from_failed_node_per_job(rerun_service):
    job_db = rerun_service.job_db
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1", "Q2"]},
        workspace_id=workspace["id"],
    )
    jobs = []
    for qid in ["Q1", "Q2"]:
        jobs.append(
            job_db.create_job(
                workflow_key="question_comprehension_info",
                source_type="question",
                source_id=qid,
                batch_id=batch["id"],
                title=f"Question {qid}",
                node_keys=["fetch_questions", "clean_and_parse", "generate_key_info"],
                workspace_id=workspace["id"],
            )
        )

    job_db.update_job_status(jobs[0]["id"], "failed", "boom")
    job_db.update_job_node(jobs[0]["id"], "clean_and_parse", status="failed")
    job_db.update_job_status(jobs[1]["id"], "failed", "boom")
    job_db.update_job_node(jobs[1]["id"], "generate_key_info", status="failed")

    results = rerun_service.batch_rerun(
        workspace["id"], [jobs[0]["id"], jobs[1]["id"]], from_failed_node=True
    )

    assert results[0]["status"] == "succeeded"
    assert results[0]["node_key"] == "clean_and_parse"
    assert results[1]["status"] == "succeeded"
    assert results[1]["node_key"] == "generate_key_info"


def test_batch_rerun_mixed_workflows(rerun_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    q_batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    r_batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q2"]},
        workspace_id=workspace["id"],
    )
    q_job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=q_batch["id"],
        title="Question 1",
        node_keys=["fetch_questions", "clean_and_parse"],
        workspace_id=workspace["id"],
    )
    r_job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q2",
        batch_id=r_batch["id"],
        title="Reading Q2",
        node_keys=["fetch_questions"],
        workspace_id=workspace["id"],
    )

    results = rerun_service.batch_rerun(
        workspace["id"], [q_job["id"], r_job["id"]], "clean_and_parse"
    )

    assert results[0]["status"] == "succeeded"
    assert results[1]["status"] == "failed"
    assert results[1]["reason_code"] == "node_not_found"


def _node_statuses(job_db, job_id):
    return {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job_id)}


def test_rerun_from_classifier_resets_not_applicable_downstream(job_db, rerun_service):
    workspace = job_db.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )
    job_db.update_job_node(job["id"], "generate_key_info", status="not_applicable")
    result = rerun_service.rerun(
        workspace["id"],
        job["id"],
        "classify_comprehension_eligibility",
    )
    assert result["status"] == "succeeded"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["classify_comprehension_eligibility"] == "pending"
    assert statuses["generate_key_info"] == "stale"
