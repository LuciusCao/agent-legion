from contextlib import closing
from datetime import UTC, datetime, timedelta

from server.app.db.connection import connect_sqlite
from server.app.services.log_cleanup import CleanupConfig, cleanup_old_logs
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
            session_dir text not null default '',
            finished_at text not null default ''
        );
        """
    )


def test_cleanup_removes_old_logs_and_run_dirs(tmp_path):
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a" / "tok-1"
    jobs_dir.mkdir(parents=True)
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("old log")

    with closing(connect_sqlite(tmp_path / "db.sqlite")) as conn:
        _setup(conn)
        old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, log_path, run_dir, finished_at)
            values (?, ?, 'completed', ?, '', ?)
            """,
            ("job-1", "node-a", make_data_relative(log_path, data_dir), old_finished),
        )
        conn.commit()

        cfg = CleanupConfig(log_retention_days=30, run_dir_retention_days=30)
        logs, dirs = cleanup_old_logs(conn, data_dir, cfg)
        assert logs == 1
        assert dirs == 1
        assert not log_path.exists()
        assert not jobs_dir.exists()


def test_cleanup_keeps_recent_logs_and_run_dirs(tmp_path):
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a" / "tok-1"
    jobs_dir.mkdir(parents=True)
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("recent log")

    with closing(connect_sqlite(tmp_path / "db.sqlite")) as conn:
        _setup(conn)
        recent_finished = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, log_path, run_dir, finished_at)
            values (?, ?, 'completed', ?, '', ?)
            """,
            ("job-1", "node-a", make_data_relative(log_path, data_dir), recent_finished),
        )
        conn.commit()

        cfg = CleanupConfig(log_retention_days=30, run_dir_retention_days=30)
        logs, dirs = cleanup_old_logs(conn, data_dir, cfg)
        assert logs == 0
        assert dirs == 0
        assert log_path.exists()
        assert jobs_dir.exists()


def test_cleanup_derives_run_dir_when_empty(tmp_path):
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a" / "tok-1"
    jobs_dir.mkdir(parents=True)
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("old log")

    with closing(connect_sqlite(tmp_path / "db.sqlite")) as conn:
        _setup(conn)
        old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, log_path, run_dir, finished_at)
            values (?, ?, 'completed', ?, '', ?)
            """,
            ("job-1", "node-a", make_data_relative(log_path, data_dir), old_finished),
        )
        conn.commit()

        cfg = CleanupConfig(log_retention_days=30, run_dir_retention_days=30)
        logs, dirs = cleanup_old_logs(conn, data_dir, cfg)
        assert logs == 1
        assert dirs == 1
        assert not jobs_dir.exists()


def test_cleanup_config_from_settings():
    cfg = CleanupConfig.from_settings(
        {"cleanup": {"log_retention_days": "7", "run_dir_retention_days": 14}}
    )
    assert cfg.log_retention_days == 7
    assert cfg.run_dir_retention_days == 14
    assert cfg.keep_only_latest_run_per_node is True


def test_cleanup_keeps_only_latest_run_per_node(tmp_path):
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
            """
            insert into node_runs(job_id, node_key, status, log_path, run_dir, finished_at)
            values (?, ?, 'completed', '', ?, ?)
            """,
            (
                "job-1",
                "node-a",
                make_data_relative(old_dir, data_dir),
                (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            ),
        )
        conn.commit()

        cfg = CleanupConfig(keep_only_latest_run_per_node=True)
        logs, dirs = cleanup_old_logs(conn, data_dir, cfg)
        assert dirs == 1
        assert not old_dir.exists()
        assert new_dir.exists()
        row = conn.execute("select run_dir from node_runs").fetchone()
        assert row["run_dir"] == ""


def test_cleanup_respects_keep_all_runs_flag(tmp_path):
    data_dir = tmp_path / "data"
    node_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a"
    old_dir = node_dir / "tok-old"
    new_dir = node_dir / "tok-new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)

    with closing(connect_sqlite(tmp_path / "db.sqlite")) as conn:
        _setup(conn)
        conn.commit()

        cfg = CleanupConfig(keep_only_latest_run_per_node=False)
        logs, dirs = cleanup_old_logs(conn, data_dir, cfg)
        assert dirs == 0
        assert old_dir.exists()
        assert new_dir.exists()
