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
    workspace = db.create_workspace("Cascade Workspace")
    job = db.create_job(
        pipeline_key="question_content",
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


def test_set_and_clear_job_execution_target(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace("Target Workspace")
    job = db.create_job(
        pipeline_key="reading_analysis",
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
    workspace = db.create_workspace("Pause Workspace")
    job = db.create_job(
        pipeline_key="reading_analysis",
        source_type="question_id",
        source_id="Q-PAUSE",
        batch_id="",
        title="Pause Job",
        node_keys=["node_a"],
        workspace_id=workspace["id"],
    )

    db.pause_job(job["id"], "awaiting_resources")
    control = db.get_job_execution_control(job["id"])
    assert control is not None
    assert control["execution_paused"] is True
    assert control["pause_reason"] == "awaiting_resources"

    db.resume_job(job["id"])
    control = db.get_job_execution_control(job["id"])
    assert control is not None
    assert control["execution_paused"] is False
    assert control["pause_reason"] == ""


def test_job_execution_target_rejects_invalid_mode_and_paused_values(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace("Validation Workspace")
    job = db.create_job(
        pipeline_key="reading_analysis",
        source_type="question_id",
        source_id="Q-VALID",
        batch_id="",
        title="Validation Job",
        node_keys=["node_a"],
        workspace_id=workspace["id"],
    )

    with pytest.raises(ValueError):
        db.set_job_execution_target(job["id"], "")

    with pytest.raises(ValueError):
        db.set_job_execution_mode(job["id"], "unknown_mode")

    with pytest.raises(ValueError):
        db.set_job_execution_mode(job["id"], "until_node")

    # paused is stored as an integer but exposed as a boolean.
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("update jobs set execution_paused = 2 where id=?", (job["id"],))


def test_execution_control_mutations_bump_updated_at(tmp_path: Path) -> None:
    import time

    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = db.create_workspace("UpdatedAt Workspace")
    job = db.create_job(
        pipeline_key="reading_analysis",
        source_type="question_id",
        source_id="Q-UPDATED",
        batch_id="",
        title="UpdatedAt Job",
        node_keys=["node_a", "node_b"],
        workspace_id=workspace["id"],
    )
    original_updated_at = job["updated_at"]

    time.sleep(1.1)
    db.set_job_execution_target(job["id"], "node_b")
    updated_job = db.get_job(job["id"])
    assert updated_job is not None
    assert updated_job["updated_at"] > original_updated_at

    time.sleep(1.1)
    db.clear_job_execution_target(job["id"])
    updated_job = db.get_job(job["id"])
    assert updated_job is not None
    assert updated_job["updated_at"] > original_updated_at

    time.sleep(1.1)
    db.pause_job(job["id"], "testing")
    updated_job = db.get_job(job["id"])
    assert updated_job is not None
    assert updated_job["updated_at"] > original_updated_at

    time.sleep(1.1)
    db.resume_job(job["id"])
    updated_job = db.get_job(job["id"])
    assert updated_job is not None
    assert updated_job["updated_at"] > original_updated_at


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
