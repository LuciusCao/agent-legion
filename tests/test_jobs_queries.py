from datetime import datetime
from pathlib import Path

import pytest
from psycopg import IntegrityError

from server.app.jobs.queries import JobQueries
from server.app.jobs.storage_layout import job_storage_dir
from tests.helpers.job_dirs import job_storage_ref
from tests.postgres_support import TEST_DATABASE_URL


def test_job_query_connections_use_postgres(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")

    with db._connect_read() as conn:
        row = conn.execute("select current_database() as name").fetchone()
    assert row is not None
    assert row["name"]


def test_fresh_schema_cascades_workspace_jobs_and_runs(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = db.create_workspace("Cascade Workspace", default_workflow_key="demo_workflow")
    job = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q-CASCADE",
        run_id="",
        title="Cascade",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )
    run = db.start_node_run(job["id"], "fetch_question_context", ["local"], "run.log")
    assert run is not None

    with db.connect() as conn:
        conn.execute("delete from workspaces where id=%s", (workspace["id"],))

    with db._connect_read() as conn:
        assert conn.execute("select 1 from jobs where id=%s", (job["id"],)).fetchone() is None
        assert (
            conn.execute("select 1 from job_nodes where job_id=%s", (job["id"],)).fetchone() is None
        )
        assert (
            conn.execute("select 1 from node_runs where job_id=%s", (job["id"],)).fetchone() is None
        )


def _looks_like_timestamp(value: datetime | str) -> bool:
    return isinstance(value, datetime) or (
        len(value) >= 19 and value[4] == "-" and value[10] in (" ", "T")
    )


def test_create_job_sets_node_created_at(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = db.create_workspace("Created At Workspace", default_workflow_key="demo_workflow")
    job = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q-CREATED",
        run_id="",
        title="Created At Job",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )

    assert job["storage_dir"] == job_storage_ref(
        "created_at_workspace", "created_at_workspace_demo_workflow_Q-CREATED"
    )
    assert job_storage_dir(tmp_path / "jobs", "created_at_workspace", job["id"]).is_dir()

    node = db.get_job_node(job["id"], "fetch_question_context")
    assert node is not None
    assert _looks_like_timestamp(node["created_at"])


def test_mark_node_for_rerun_resets_node_created_at(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = db.create_workspace("Rerun Workspace", default_workflow_key="demo_workflow")
    job = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q-RERUN",
        run_id="",
        title="Rerun Job",
        node_keys=["fetch_question_context", "question_understanding"],
        workspace_id=workspace["id"],
    )

    # Simulate the node having run and completed with an old created_at.
    db.start_node_run(job["id"], "fetch_question_context", ["local"], "run.log")
    db.update_job_node(
        job["id"],
        "fetch_question_context",
        status="completed",
        started_at="2026-06-09T00:00:00Z",
        finished_at="2026-06-09T00:00:10Z",
    )
    old_created_at = "2026-06-09T00:00:00Z"
    with db.connect() as conn:
        conn.execute(
            "update job_nodes set created_at=%s where job_id=%s and node_key=%s",
            (old_created_at, job["id"], "fetch_question_context"),
        )

    db.mark_node_for_rerun(job["id"], "fetch_question_context", ["question_understanding"])

    rerun_node = db.get_job_node(job["id"], "fetch_question_context")
    assert rerun_node is not None
    assert rerun_node["status"] == "pending"
    assert rerun_node["created_at"] != old_created_at
    assert _looks_like_timestamp(rerun_node["created_at"])

    downstream = db.get_job_node(job["id"], "question_understanding")
    assert downstream is not None
    assert downstream["status"] == "stale"
    assert downstream["created_at"] != old_created_at
    assert _looks_like_timestamp(downstream["created_at"])


def test_set_and_clear_job_execution_target(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = db.create_workspace("Target Workspace", default_workflow_key="demo_workflow")
    job = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q-TARGET",
        run_id="",
        title="Target Job",
        node_keys=["node_a", "node_b"],
        workspace_id=workspace["id"],
    )

    control = db.get_job_execution_control(job["id"])
    assert control is not None
    assert control["execution_mode"] == "full"
    assert control["target_node_key"] is None
    assert control["execution_paused"] is False
    assert control["pause_reason"] == ""

    db.set_job_execution_target(job["id"], "node_b")
    control = db.get_job_execution_control(job["id"])
    assert control is not None
    assert control["execution_mode"] == "until_node"
    assert control["target_node_key"] == "node_b"

    db.clear_job_execution_target(job["id"])
    control = db.get_job_execution_control(job["id"])
    assert control is not None
    assert control["execution_mode"] == "full"
    assert control["target_node_key"] is None


def test_pause_and_resume_job(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = db.create_workspace("Pause Workspace", default_workflow_key="demo_workflow")
    job = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q-PAUSE",
        run_id="",
        title="Pause Job",
        node_keys=["node_a"],
        workspace_id=workspace["id"],
    )

    db.pause_job(job["id"], "awaiting_resources")
    with db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=%s", (job["id"],))
    control = db.get_job_execution_control(job["id"])
    assert control is not None
    assert control["execution_paused"] is True
    assert control["pause_reason"] == "awaiting_resources"

    db.resume_job(job["id"])
    control = db.get_job_execution_control(job["id"])
    assert control is not None
    assert control["execution_paused"] is False
    assert control["pause_reason"] == ""


def test_resume_job_clears_target_reached_state(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = db.create_workspace("Continue Workspace", default_workflow_key="demo_workflow")
    job = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q-CONTINUE",
        run_id="",
        title="Continue Job",
        node_keys=["node_a"],
        workspace_id=workspace["id"],
    )

    db.set_job_execution_target(job["id"], "node_a")
    db.pause_job(job["id"], "target_reached")
    with db.connect() as conn:
        conn.execute(
            "update jobs set status='paused' where id=%s",
            (job["id"],),
        )

    db.resume_job(job["id"])
    control = db.get_job_execution_control(job["id"])
    assert control is not None
    assert control["execution_paused"] is False
    assert control["execution_mode"] == "full"
    assert control["target_node_key"] is None
    assert control["pause_reason"] == ""

    resumed_job = db.get_job(job["id"])
    assert resumed_job is not None
    assert resumed_job["status"] == "queued"


def test_job_execution_target_rejects_invalid_mode_and_paused_values(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = db.create_workspace("Validation Workspace", default_workflow_key="demo_workflow")
    job = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q-VALID",
        run_id="",
        title="Validation Job",
        node_keys=["node_a"],
        workspace_id=workspace["id"],
    )

    with pytest.raises(ValueError):
        db.set_job_execution_target(job["id"], "")

    with pytest.raises(ValueError, match="Unknown target node"):
        db.set_job_execution_target(job["id"], "unknown_node")

    with pytest.raises(ValueError):
        db.set_job_execution_mode(job["id"], "unknown_mode")

    with pytest.raises(ValueError):
        db.set_job_execution_mode(job["id"], "until_node")

    # paused is stored as an integer but exposed as a boolean.
    with db.connect() as conn, pytest.raises(IntegrityError):
        conn.execute("update jobs set execution_paused = 2 where id=%s", (job["id"],))


def test_execution_control_mutations_bump_updated_at(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = db.create_workspace("UpdatedAt Workspace", default_workflow_key="demo_workflow")
    job = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q-UPDATED",
        run_id="",
        title="UpdatedAt Job",
        node_keys=["node_a", "node_b"],
        workspace_id=workspace["id"],
    )
    past_updated_at = "2000-01-01 00:00:00"

    def reset_updated_at_to_past() -> None:
        with db.connect() as conn:
            conn.execute(
                "update jobs set updated_at=%s where id=%s",
                (past_updated_at, job["id"]),
            )

    reset_updated_at_to_past()
    db.set_job_execution_target(job["id"], "node_b")
    updated_job = db.get_job(job["id"])
    assert updated_job is not None
    assert updated_job["updated_at"] > past_updated_at

    reset_updated_at_to_past()
    db.clear_job_execution_target(job["id"])
    updated_job = db.get_job(job["id"])
    assert updated_job is not None
    assert updated_job["updated_at"] > past_updated_at

    reset_updated_at_to_past()
    db.pause_job(job["id"], "testing")
    with db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=%s", (job["id"],))
    updated_job = db.get_job(job["id"])
    assert updated_job is not None
    assert updated_job["updated_at"] > past_updated_at

    reset_updated_at_to_past()
    db.resume_job(job["id"])
    updated_job = db.get_job(job["id"])
    assert updated_job is not None
    assert updated_job["updated_at"] > past_updated_at


def test_get_job_execution_control_returns_none_for_missing_job(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    assert db.get_job_execution_control("missing-job") is None


def test_execution_control_mutations_raise_for_unknown_job(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")

    with pytest.raises(ValueError):
        db.pause_job("missing-job", "reason")

    with pytest.raises(ValueError):
        db.resume_job("missing-job")

    with pytest.raises(ValueError):
        db.clear_job_execution_target("missing-job")


def test_list_jobs_by_ids_returns_only_matching_jobs(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = db.create_workspace("List By Ids Workspace", default_workflow_key="demo_workflow")
    other_workspace = db.create_workspace("Other Workspace", default_workflow_key="demo_workflow")
    job1 = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q1",
        run_id="",
        title="Job 1",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )
    job2 = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q2",
        run_id="",
        title="Job 2",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )
    other_job = db.create_job(
        workflow_key="demo_workflow",
        source_type="question_id",
        source_id="Q-OTHER",
        run_id="",
        title="Other Job",
        node_keys=["fetch_question_context"],
        workspace_id=other_workspace["id"],
    )

    results = db.list_jobs_by_ids(workspace["id"], [job1["id"], job2["id"], other_job["id"]])
    ids = {job["id"] for job in results}

    assert ids == {job1["id"], job2["id"]}


def test_list_jobs_by_ids_returns_empty_for_empty_input(tmp_path: Path) -> None:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = db.create_workspace("Empty List Workspace", default_workflow_key="demo_workflow")
    assert db.list_jobs_by_ids(workspace["id"], []) == []
