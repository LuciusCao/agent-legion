from pathlib import Path

from server.app.jobs.queries import JobQueries
from tests.helpers import ensure_legacy_workspace_tables


def test_upsert_workspace_agent_assignment(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    ensure_legacy_workspace_tables(db)
    workspace = db.create_workspace("Workspace One")
    result = db.upsert_workspace_agent_assignment(workspace["id"], "pi", 3)
    assert result["workspace_id"] == workspace["id"]
    assert result["agent_id"] == "pi"
    assert result["concurrency_limit"] == 3

    # update
    result2 = db.upsert_workspace_agent_assignment(workspace["id"], "pi", 5)
    assert result2["concurrency_limit"] == 5

    # list confirms
    agents = db.list_workspace_agents(workspace["id"])
    assert len(agents) == 1
    assert agents[0]["concurrency_limit"] == 5

    # clamp to minimum of 1
    result3 = db.upsert_workspace_agent_assignment(workspace["id"], "pi", 0)
    assert result3["concurrency_limit"] == 1


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
