import sqlite3
from pathlib import Path

import pytest

from server.app.jobs.queries import JobQueries


def test_job_query_connections_enable_sqlite_safety_pragmas(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")

    with db._connect_read() as conn:
        assert conn.execute("pragma foreign_keys").fetchone()[0] == 1
        assert conn.execute("pragma journal_mode").fetchone()[0] == "wal"
        assert conn.execute("pragma busy_timeout").fetchone()[0] >= 5000


def test_fresh_schema_cascades_workspace_jobs_and_runs(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace(
        "Cascade Workspace", default_workflow_key="question_comprehension_info"
    )
    job = db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question_id",
        source_id="Q-CASCADE",
        batch_id="",
        title="Cascade",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )
    run = db.start_node_run(job["id"], "fetch_question_context", ["local"], "run.log")
    assert run is not None

    with db.connect() as conn:
        conn.execute("delete from workspaces where id=?", (workspace["id"],))

    with db._connect_read() as conn:
        assert conn.execute("select 1 from jobs where id=?", (job["id"],)).fetchone() is None
        assert (
            conn.execute("select 1 from job_nodes where job_id=?", (job["id"],)).fetchone() is None
        )
        assert (
            conn.execute("select 1 from node_runs where job_id=?", (job["id"],)).fetchone() is None
        )


def _looks_like_timestamp(value: str) -> bool:
    """Return True for SQLite current_timestamp style strings."""
    return len(value) >= 19 and value[4] == "-" and value[10] in (" ", "T")


def test_create_job_sets_node_created_at(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace(
        "Created At Workspace", default_workflow_key="question_comprehension_info"
    )
    job = db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question_id",
        source_id="Q-CREATED",
        batch_id="",
        title="Created At Job",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )

    assert (
        job["storage_dir"]
        == "jobs/created_at_workspace/created_at_workspace_question_comprehension_info_Q-CREATED"
    )
    assert (tmp_path / "jobs" / "created_at_workspace" / job["id"]).is_dir()

    node = db.get_job_node(job["id"], "fetch_question_context")
    assert node is not None
    assert _looks_like_timestamp(node["created_at"])


def test_mark_node_for_rerun_resets_node_created_at(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace(
        "Rerun Workspace", default_workflow_key="question_comprehension_info"
    )
    job = db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question_id",
        source_id="Q-RERUN",
        batch_id="",
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
            "update job_nodes set created_at=? where job_id=? and node_key=?",
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
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace(
        "Target Workspace", default_workflow_key="question_comprehension_info"
    )
    job = db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question_id",
        source_id="Q-TARGET",
        batch_id="",
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
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace(
        "Pause Workspace", default_workflow_key="question_comprehension_info"
    )
    job = db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question_id",
        source_id="Q-PAUSE",
        batch_id="",
        title="Pause Job",
        node_keys=["node_a"],
        workspace_id=workspace["id"],
    )

    db.pause_job(job["id"], "awaiting_resources")
    with db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=?", (job["id"],))
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
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace(
        "Continue Workspace", default_workflow_key="question_comprehension_info"
    )
    job = db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question_id",
        source_id="Q-CONTINUE",
        batch_id="",
        title="Continue Job",
        node_keys=["node_a"],
        workspace_id=workspace["id"],
    )

    db.set_job_execution_target(job["id"], "node_a")
    db.pause_job(job["id"], "target_reached")
    with db.connect() as conn:
        conn.execute(
            "update jobs set status='paused' where id=?",
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
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace(
        "Validation Workspace", default_workflow_key="question_comprehension_info"
    )
    job = db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question_id",
        source_id="Q-VALID",
        batch_id="",
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
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("update jobs set execution_paused = 2 where id=?", (job["id"],))


def test_execution_control_mutations_bump_updated_at(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace(
        "UpdatedAt Workspace", default_workflow_key="question_comprehension_info"
    )
    job = db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question_id",
        source_id="Q-UPDATED",
        batch_id="",
        title="UpdatedAt Job",
        node_keys=["node_a", "node_b"],
        workspace_id=workspace["id"],
    )
    past_updated_at = "2000-01-01 00:00:00"

    def reset_updated_at_to_past() -> None:
        with db.connect() as conn:
            conn.execute(
                "update jobs set updated_at=? where id=?",
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
        conn.execute("update jobs set status='paused' where id=?", (job["id"],))
    updated_job = db.get_job(job["id"])
    assert updated_job is not None
    assert updated_job["updated_at"] > past_updated_at

    reset_updated_at_to_past()
    db.resume_job(job["id"])
    updated_job = db.get_job(job["id"])
    assert updated_job is not None
    assert updated_job["updated_at"] > past_updated_at


def test_get_job_execution_control_returns_none_for_missing_job(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    assert db.get_job_execution_control("missing-job") is None


def test_execution_control_mutations_raise_for_unknown_job(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")

    with pytest.raises(ValueError):
        db.pause_job("missing-job", "reason")

    with pytest.raises(ValueError):
        db.resume_job("missing-job")

    with pytest.raises(ValueError):
        db.clear_job_execution_target("missing-job")
