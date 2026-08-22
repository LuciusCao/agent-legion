from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService, StagedOutputs
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_rerun import JobRerunService
from server.app.storage_paths import resolve_job_dir
from tests.helpers import load_builtin_definition, publish_builtin_revision


@pytest.fixture
def rerun_service(job_db, settings):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db.path, data_dir=settings.data_dir),
        settings,
        JobArtifactMutationService(settings.jobs_dir),
    )


@pytest.fixture
def job(job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    return job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        run_id=batch["id"],
        title="Question 1",
        node_keys=[
            "intake_knowledge_points",
            "write_script",
            "review_script",
            "generate_questions",
            "review_questions",
            "publish_content",
        ],
        workspace_id=workspace["id"],
    )


@pytest.fixture
def running_job(job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        run_id=batch["id"],
        title="Question 1",
        node_keys=["intake_knowledge_points"],
        workspace_id=workspace["id"],
    )
    job_db.update_job_node(job["id"], "intake_knowledge_points", status="running")
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
            values (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
            """,
            (
                "lease-1",
                "exec-1",
                "code-default",
                job["workspace_id"],
                job["id"],
                job["workflow_key"],
                node_key,
                run["id"],
                database_timestamp(now),
                database_timestamp(now),
                database_timestamp(expires),
            ),
        )
    return run


def test_rerun_selected_node_and_descendants_are_stale(rerun_service, job):
    result = rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert result["job_id"] == job["id"]
    assert result["node_key"] == "write_script"
    assert result["status"] == "succeeded"

    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["write_script"] == "pending"
    assert nodes["review_script"] == "stale"
    assert nodes["publish_content"] == "stale"
    # The other diamond branch is not downstream of write_script.
    assert nodes["generate_questions"] == "pending"
    assert nodes["review_questions"] == "pending"


def test_rerun_cancels_queued_agent_requests(rerun_service, job, job_db):
    """rerun 前已入队的 queued agent 请求必须取消：claim 侧不复查上游，
    不取消会在上游重跑完成前抢跑（输入 artifact 已被 rerun 删除）。"""
    with job_db.connect() as conn:
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, workflow_key, node_key,"
            " agent_id, agent_definition_hash, node_concurrency_limit,"
            " state, queued_at, manifest_json)"
            " values ('exec-queued', %s, %s, %s, 'review_script',"
            " 'generator-v1', 'sha256:whatever', 1, 'queued', current_timestamp, '{}')",
            (job["workspace_id"], job["id"], job["workflow_key"]),
        )

    result = rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert result["status"] == "succeeded"
    with job_db.connect() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id='exec-queued'"
        ).fetchone()
    assert row["state"] == "cancelled"


def test_rerun_resets_node_created_at(rerun_service, job):
    old_created_at = "2026-06-09T00:00:00Z"
    with rerun_service.job_db.connect() as conn:
        conn.execute(
            "update job_nodes set created_at=%s where job_id=%s and node_key=%s",
            (old_created_at, job["id"], "write_script"),
        )

    result = rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert result["status"] == "succeeded"
    rerun_node = rerun_service.job_db.get_job_node(job["id"], "write_script")
    assert rerun_node is not None
    assert rerun_node["created_at"] != old_created_at
    assert len(rerun_node["created_at"]) >= 19


def test_rerun_resets_packed_flag(rerun_service, job):
    rerun_service.job_db.set_jobs_packed([job["id"]], packed=1)

    result = rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert result["status"] == "succeeded"
    assert rerun_service.job_db.get_job(job["id"])["packed"] == 0


def test_rerun_preserves_ancestors(rerun_service, job):
    rerun_service.job_db.update_job_node(job["id"], "intake_knowledge_points", status="completed")

    result = rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert result["status"] == "succeeded"
    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["intake_knowledge_points"] == "completed"


def test_rerun_rejects_running_job(rerun_service, running_job):
    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun(
            running_job["workspace_id"], running_job["id"], "intake_knowledge_points"
        )

    assert exc_info.value.status == "skipped"
    assert exc_info.value.reason_code == "busy"


def test_rerun_rejects_active_lease(rerun_service, job):
    _create_lease(rerun_service.job_db, job, "write_script", expires_offset_seconds=300)

    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert exc_info.value.status == "skipped"
    assert exc_info.value.reason_code == "busy"


def test_rerun_uses_atomic_lease_guarded_mutation(rerun_service, job, monkeypatch):
    calls: list[str] = []
    original = rerun_service.job_db.lease_guarded_mutation

    @contextmanager
    def tracked(job_id: str, now, *, reject_running_nodes: bool):
        calls.append(job_id)
        with original(job_id, now, reject_running_nodes=reject_running_nodes) as conn:
            yield conn

    monkeypatch.setattr(rerun_service.job_db, "lease_guarded_mutation", tracked)

    result = rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

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
                "write_script",
                expires_offset_seconds=300,
            )
            created = True
        with original(job_id, now, reject_running_nodes=reject_running_nodes) as conn:
            yield conn

    monkeypatch.setattr(rerun_service.job_db, "lease_guarded_mutation", race)

    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert exc_info.value.status == "skipped"
    assert exc_info.value.reason_code == "busy"
    assert rerun_service.job_db.get_job_node(job["id"], "write_script")["status"] == "running"


def test_rerun_node_not_found(rerun_service, job):
    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun(job["workspace_id"], job["id"], "nonexistent")

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "node_not_found"


def test_rerun_job_not_found(rerun_service):
    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun("default", "missing", "write_script")

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "not_found"


def test_rerun_wrong_workspace(rerun_service, job):
    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun("other", job["id"], "write_script")

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "wrong_workspace"


def test_rerun_stages_and_removes_artifacts(rerun_service, job, settings):
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "script.md").write_text("understanding")
    (storage / "script_review.json").write_text("misconceptions")
    (storage / "fetch.log").write_text("log")

    result = rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert result["status"] == "succeeded"
    assert not (storage / "script.md").exists()
    assert not (storage / "script_review.json").exists()
    assert (storage / "fetch.log").exists()


def test_rerun_removes_affected_run_history_and_clears_database_paths(rerun_service, job, settings):
    storage = resolve_job_dir(job, settings.jobs_dir)
    affected_run = storage / "runs" / "write_script" / "run-1"
    descendant_run = storage / "runs" / "review_script" / "run-2"
    ancestor_run = storage / "runs" / "intake_knowledge_points" / "run-3"
    for run_dir in (affected_run, descendant_run, ancestor_run):
        run_dir.mkdir(parents=True)
        (run_dir / "events.jsonl").write_text("events")

    with rerun_service.job_db.connect() as conn:
        for node_key, run_dir in (
            ("write_script", affected_run),
            ("review_script", descendant_run),
            ("intake_knowledge_points", ancestor_run),
        ):
            relative = run_dir.relative_to(settings.data_dir).as_posix()
            conn.execute(
                """
                insert into node_runs(job_id, node_key, status, run_dir, session_dir)
                values (%s, %s, 'completed', %s, %s)
                """,
                (job["id"], node_key, relative, f"{relative}/session"),
            )

    result = rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert result["status"] == "succeeded"
    assert not affected_run.exists()
    assert not descendant_run.exists()
    assert ancestor_run.exists()
    runs = {run["node_key"]: run for run in rerun_service.job_db.list_node_runs(job["id"])}
    assert runs["write_script"]["run_dir"] == ""
    assert runs["write_script"]["session_dir"] == ""
    assert runs["review_script"]["run_dir"] == ""
    assert runs["intake_knowledge_points"]["run_dir"] != ""


def test_rerun_rolls_back_artifacts_when_db_fails(rerun_service, job, settings, monkeypatch):
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "script.md").write_text("understanding")

    def _fail(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        rerun_service.job_db,
        "mark_nodes_for_rerun_in_transaction",
        _fail,
    )

    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert exc_info.value.status == "failed"
    assert (storage / "script.md").read_text() == "understanding"


def test_rerun_reports_success_when_post_commit_cleanup_fails(
    rerun_service, job, settings, monkeypatch, caplog
):
    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "script.md").write_text("understanding")

    def _fail_commit(self):
        raise OSError("cleanup failed")

    monkeypatch.setattr(StagedOutputs, "commit", _fail_commit)

    result = rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert result["status"] == "succeeded"
    assert "cleanup failed" in caplog.text
    assert rerun_service.job_db.get_job_node(job["id"], "write_script")["status"] == "pending"


def test_rerun_expired_lease_is_not_blocking(rerun_service, job):
    run = _create_lease(rerun_service.job_db, job, "write_script", expires_offset_seconds=-1)
    rerun_service.job_db.finish_node_run(run["id"], "failed", 1, "expired")

    result = rerun_service.rerun(job["workspace_id"], job["id"], "write_script")

    assert result["status"] == "succeeded"
    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["write_script"] == "pending"


def test_batch_rerun_returns_results_in_request_order(rerun_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1", "Q2"]},
        workspace_id=workspace["id"],
    )
    jobs = []
    for qid in ["Q1", "Q2"]:
        jobs.append(
            job_db.create_job(
                workflow_key="education_video_problems_generation",
                source_type="question",
                source_id=qid,
                run_id=batch["id"],
                title=f"Question {qid}",
                node_keys=["intake_knowledge_points", "write_script"],
                workspace_id=workspace["id"],
            )
        )

    results = rerun_service.batch_rerun(
        workspace["id"], [jobs[1]["id"], jobs[0]["id"]], "write_script"
    )

    assert [r["job_id"] for r in results] == [jobs[1]["id"], jobs[0]["id"]]
    assert all(r["status"] == "succeeded" for r in results)


def test_batch_rerun_node_not_found_for_one_job(rerun_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        run_id=batch["id"],
        title="Question 1",
        node_keys=["intake_knowledge_points"],
        workspace_id=workspace["id"],
    )

    results = rerun_service.batch_rerun(workspace["id"], [job["id"]], "write_script")

    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["reason_code"] == "node_not_found"


def test_rerun_from_failed_node_uses_failed_node(rerun_service, job):
    rerun_service.job_db.update_job_status(job["id"], "failed", "boom")
    rerun_service.job_db.update_job_node(job["id"], "write_script", status="failed")

    result = rerun_service.rerun(job["workspace_id"], job["id"], from_failed_node=True)

    assert result["status"] == "succeeded"
    assert result["node_key"] == "write_script"
    nodes = {n["node_key"]: n["status"] for n in rerun_service.job_db.list_job_nodes(job["id"])}
    assert nodes["write_script"] == "pending"


def test_rerun_from_failed_node_skips_non_failed(rerun_service, job):
    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun(job["workspace_id"], job["id"], from_failed_node=True)

    assert exc_info.value.status == "skipped"
    assert exc_info.value.reason_code == "not_failed"


def test_rerun_from_failed_node_skips_when_no_failed_node(rerun_service, job):
    rerun_service.job_db.update_job_status(job["id"], "failed", "boom")

    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun(job["workspace_id"], job["id"], from_failed_node=True)

    assert exc_info.value.status == "skipped"
    assert exc_info.value.reason_code == "no_failed_node"


def test_batch_rerun_from_failed_node_per_job(rerun_service):
    job_db = rerun_service.job_db
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1", "Q2"]},
        workspace_id=workspace["id"],
    )
    jobs = []
    for qid in ["Q1", "Q2"]:
        jobs.append(
            job_db.create_job(
                workflow_key="education_video_problems_generation",
                source_type="question",
                source_id=qid,
                run_id=batch["id"],
                title=f"Question {qid}",
                node_keys=["intake_knowledge_points", "write_script", "review_script"],
                workspace_id=workspace["id"],
            )
        )

    job_db.update_job_status(jobs[0]["id"], "failed", "boom")
    job_db.update_job_node(jobs[0]["id"], "write_script", status="failed")
    job_db.update_job_status(jobs[1]["id"], "failed", "boom")
    job_db.update_job_node(jobs[1]["id"], "review_script", status="failed")

    results = rerun_service.batch_rerun(
        workspace["id"], [jobs[0]["id"], jobs[1]["id"]], from_failed_node=True
    )

    assert results[0]["status"] == "succeeded"
    assert results[0]["node_key"] == "write_script"
    assert results[1]["status"] == "succeeded"
    assert results[1]["node_key"] == "review_script"


def test_batch_rerun_mixed_workflows(rerun_service, job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    q_batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    r_batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q2"]},
        workspace_id=workspace["id"],
    )
    q_job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        run_id=q_batch["id"],
        title="Question 1",
        node_keys=["intake_knowledge_points", "write_script"],
        workspace_id=workspace["id"],
    )
    r_job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q2",
        run_id=r_batch["id"],
        title="Reading Q2",
        node_keys=["intake_knowledge_points"],
        workspace_id=workspace["id"],
    )

    results = rerun_service.batch_rerun(workspace["id"], [q_job["id"], r_job["id"]], "write_script")

    assert results[0]["status"] == "succeeded"
    assert results[1]["status"] == "failed"
    assert results[1]["reason_code"] == "node_not_found"


def _node_statuses(job_db, job_id):
    return {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job_id)}


def test_rerun_from_intake_resets_not_applicable_downstream(job_db, rerun_service):
    workspace = job_db.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    definition = load_builtin_definition("education_video_problems_generation")
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )
    job_db.update_job_node(job["id"], "review_script", status="not_applicable")
    result = rerun_service.rerun(
        workspace["id"],
        job["id"],
        "intake_knowledge_points",
    )
    assert result["status"] == "succeeded"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["intake_knowledge_points"] == "pending"
    assert statuses["review_script"] == "stale"
