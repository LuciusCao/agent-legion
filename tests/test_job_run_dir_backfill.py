from contextlib import closing

from server.app.db.connection import connect_database
from server.app.services.job_run_dir_backfill import backfill_node_run_dirs
from server.app.storage_paths import make_data_relative
from tests.postgres_support import TEST_DATABASE_URL


def _setup(conn):
    conn.execute(
        "insert into workspaces(id, name) values ('ws1', 'ws1') on conflict (id) do nothing"
    )
    conn.execute(
        "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
        " values ('job-1', 'ws1', 'wf', 'question', 'q1')"
    )


def test_backfill_derives_run_dir_and_session_dir(tmp_path):
    data_dir = tmp_path / "data"
    token_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a" / "tok-1"
    token_dir.mkdir(parents=True)
    (token_dir / "session").mkdir()
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("log")

    with closing(connect_database(TEST_DATABASE_URL)) as conn:
        _setup(conn)
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, log_path, run_dir, session_dir)
            values (?, ?, 'completed', ?, '', '')
            """,
            ("job-1", "node-a", make_data_relative(log_path, data_dir)),
        )
        conn.commit()

        updated = backfill_node_run_dirs(conn, data_dir)
        assert updated == 1
        row = conn.execute("select run_dir, session_dir from node_runs").fetchone()
        assert row["run_dir"] == "jobs/ws1/job-1/runs/node-a/tok-1"
        assert row["session_dir"] == "jobs/ws1/job-1/runs/node-a/tok-1/session"


def test_backfill_skips_when_no_run_dir_on_disk(tmp_path):
    data_dir = tmp_path / "data"
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("log")

    with closing(connect_database(TEST_DATABASE_URL)) as conn:
        _setup(conn)
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, log_path, run_dir, session_dir)
            values (?, ?, 'completed', ?, '', '')
            """,
            ("job-1", "node-a", make_data_relative(log_path, data_dir)),
        )
        conn.commit()

        updated = backfill_node_run_dirs(conn, data_dir)
        assert updated == 0


def test_backfill_only_updates_empty_session_dir_when_run_dir_present(tmp_path):
    data_dir = tmp_path / "data"
    token_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a" / "tok-1"
    token_dir.mkdir(parents=True)
    (token_dir / "session").mkdir()
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("log")

    with closing(connect_database(TEST_DATABASE_URL)) as conn:
        _setup(conn)
        existing_run_dir = "jobs/ws1/job-1/runs/node-a/tok-1"
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, log_path, run_dir, session_dir)
            values (?, ?, 'completed', ?, ?, '')
            """,
            ("job-1", "node-a", make_data_relative(log_path, data_dir), existing_run_dir),
        )
        conn.commit()

        updated = backfill_node_run_dirs(conn, data_dir)
        assert updated == 1
        row = conn.execute("select run_dir, session_dir from node_runs").fetchone()
        assert row["run_dir"] == existing_run_dir
        assert row["session_dir"] == "jobs/ws1/job-1/runs/node-a/tok-1/session"


def test_backfill_ignores_running_runs(tmp_path):
    data_dir = tmp_path / "data"
    token_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a" / "tok-1"
    token_dir.mkdir(parents=True)
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("log")

    with closing(connect_database(TEST_DATABASE_URL)) as conn:
        _setup(conn)
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, log_path, run_dir, session_dir)
            values (?, ?, 'running', ?, '', '')
            """,
            ("job-1", "node-a", make_data_relative(log_path, data_dir)),
        )
        conn.commit()

        updated = backfill_node_run_dirs(conn, data_dir)
        assert updated == 0
