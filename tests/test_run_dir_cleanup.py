from contextlib import closing, contextmanager

from server.app.db.connection import connect_sqlite
from server.app.jobs import JobQueries
from server.app.services.cleanup_sweep import (
    RUN_DIR_UPDATE_BATCH_SIZE,
    cleanup_extra_runs_per_node,
)
from server.app.services.run_dir_cleanup import cleanup_extra_runs_for_node
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


def _make_db(tmp_path):
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, JobQueries(tmp_path / "db.sqlite", jobs_dir)


def _seed_job(conn, job_id, workspace_id="ws1"):
    conn.execute(
        "insert or ignore into workspaces(id, name) values (?, ?)", (workspace_id, workspace_id)
    )
    conn.execute(
        """
        insert into jobs(id, workspace_id, workflow_key, source_type, source_id)
        values (?, ?, 'wf', 'question', ?)
        """,
        (job_id, workspace_id, job_id),
    )


def _insert_node_run(conn, job_id, node_key, *, run_dir=""):
    conn.execute(
        """
        insert into node_runs(job_id, node_key, status, run_dir, finished_at)
        values (?, ?, 'completed', ?, current_timestamp)
        """,
        (job_id, node_key, run_dir),
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
    data_dir, db = _make_db(tmp_path)
    old_dirs = []
    for job_id in ("job-1", "job-2"):
        node_dir = data_dir / "jobs" / "ws1" / job_id / "runs" / "node-a"
        old_dir = node_dir / "old"
        old_dir.mkdir(parents=True)
        (node_dir / "new").mkdir(parents=True)
        old_dirs.append((job_id, old_dir))

    with db.connect() as conn:
        for job_id, old_dir in old_dirs:
            _seed_job(conn, job_id)
            _insert_node_run(conn, job_id, "node-a", run_dir=make_data_relative(old_dir, data_dir))

    removed = cleanup_extra_runs_per_node(db, data_dir)
    assert removed == 2
    for _, old_dir in old_dirs:
        assert not old_dir.exists()
    with db._connect_read() as conn:
        rows = conn.execute("select run_dir from node_runs").fetchall()
    assert [row["run_dir"] for row in rows] == ["", ""]


def test_cleanup_extra_runs_per_node_batches_updates(tmp_path, monkeypatch):
    data_dir, db = _make_db(tmp_path)
    node_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a"
    extra_count = RUN_DIR_UPDATE_BATCH_SIZE + 5
    token_dirs = [node_dir / f"tok-{i:04d}" for i in range(extra_count + 1)]
    for token_dir in token_dirs:
        token_dir.mkdir(parents=True)

    with db.connect() as conn:
        _seed_job(conn, "job-1")
        for token_dir in token_dirs:
            _insert_node_run(
                conn, "job-1", "node-a", run_dir=make_data_relative(token_dir, data_dir)
            )

    write_transactions = 0
    real_connect = db.connect

    @contextmanager
    def counting_connect():
        nonlocal write_transactions
        with real_connect() as conn:
            yield conn
        write_transactions += 1

    monkeypatch.setattr(db, "connect", counting_connect)

    removed = cleanup_extra_runs_per_node(db, data_dir)

    assert removed == extra_count
    remaining = [d for d in node_dir.iterdir() if d.is_dir()]
    assert len(remaining) == 1
    # 505 updates must flush as two short transactions (500 + 5), not one per
    # row and not one giant transaction for the whole sweep.
    assert write_transactions == 2
    with db._connect_read() as conn:
        stale = conn.execute("select count(*) as c from node_runs where run_dir != ''").fetchone()
    # Only the surviving (newest) directory keeps its run_dir value.
    assert stale["c"] == 1
