from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db


def test_node_runs_has_runner_column(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    conn = connect_sqlite(db_path)
    try:
        columns = {row["name"] for row in conn.execute("pragma table_info(node_runs)")}
        assert "runner" in columns
        # default is '' for pre-existing rows: insert without naming runner
        conn.execute("insert into workspaces(id, name) values ('w1', 'ws')")
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values ('j1', 'w1', 'wf', 'st', 's1')"
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, command_json, log_path,"
            " run_dir, session_dir, started_at)"
            " values ('j1', 'n1', 'running', '[]', '', '', '', '2026-07-18 00:00:00.000000')"
        )
        row = conn.execute("select runner from node_runs where job_id = 'j1'").fetchone()
        assert row["runner"] == ""
    finally:
        conn.close()
