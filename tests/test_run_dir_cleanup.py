from contextlib import closing

from server.app.db.connection import connect_sqlite
from server.app.services.run_dir_cleanup import (
    cleanup_extra_runs_for_node,
    cleanup_extra_runs_per_node,
)
from server.app.storage_paths import make_data_relative


def _setup(conn):
    conn.executescript(
        """
        create table node_runs (
            id integer primary key autoincrement,
            job_id text not null,
            node_key text not null,
            status text not null,
            log_path text not null default '',
            run_dir text not null default '',
            session_dir text not null default ''
        );
        """
    )


def test_cleanup_extra_runs_for_node_keeps_newest(tmp_path):
    data_dir = tmp_path / "data"
    node_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a"
    old_dir = node_dir / "tok-old"
    new_dir = node_dir / "tok-new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    (old_dir / "events.jsonl").write_text("old")
    (new_dir / "events.jsonl").write_text("new")

    with closing(connect_sqlite(tmp_path / "db.sqlite")) as conn:
        _setup(conn)
        conn.execute(
            "insert into node_runs(job_id, node_key, status, run_dir) values (?, ?, 'completed', ?)",
            ("job-1", "node-a", make_data_relative(old_dir, data_dir)),
        )
        conn.commit()

        removed = cleanup_extra_runs_for_node(conn, data_dir, node_dir.parent.parent, "node-a")
        assert removed == 1
        assert not old_dir.exists()
        assert new_dir.exists()
        row = conn.execute("select run_dir from node_runs").fetchone()
        assert row["run_dir"] == ""


def test_cleanup_extra_runs_for_node_keeps_single_run(tmp_path):
    data_dir = tmp_path / "data"
    node_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a"
    only_dir = node_dir / "tok-only"
    only_dir.mkdir(parents=True)

    with closing(connect_sqlite(tmp_path / "db.sqlite")) as conn:
        _setup(conn)
        removed = cleanup_extra_runs_for_node(conn, data_dir, node_dir.parent.parent, "node-a")
        assert removed == 0
        assert only_dir.exists()


def test_cleanup_extra_runs_per_node_scans_all_nodes(tmp_path):
    data_dir = tmp_path / "data"
    for job_id in ("job-1", "job-2"):
        node_dir = data_dir / "jobs" / "ws1" / job_id / "runs" / "node-a"
        (node_dir / "old").mkdir(parents=True)
        (node_dir / "new").mkdir(parents=True)

    with closing(connect_sqlite(tmp_path / "db.sqlite")) as conn:
        _setup(conn)
        removed = cleanup_extra_runs_per_node(conn, data_dir)
        assert removed == 2
