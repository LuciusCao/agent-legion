import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from server.app.db.connection import DatabaseConnection
from server.app.jobs import JobQueries
from server.app.services import cleanup_sweep
from server.app.services.log_cleanup import CleanupConfig, cleanup_old_logs
from server.app.storage_paths import make_data_relative
from tests.postgres_support import TEST_DATABASE_URL


def _make_db(tmp_path):
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, JobQueries(TEST_DATABASE_URL, jobs_dir)


def _seed_workspace(conn, workspace_id="ws1"):
    conn.execute("insert into workspaces(id, name) values (?, ?)", (workspace_id, workspace_id))


def _insert_job(conn, job_id, workspace_id="ws1"):
    conn.execute(
        """
        insert into jobs(id, workspace_id, workflow_key, source_type, source_id)
        values (?, ?, 'wf', 'question', ?)
        """,
        (job_id, workspace_id, job_id),
    )


def _insert_node_run(
    conn,
    job_id,
    node_key,
    *,
    status="completed",
    log_path="",
    run_dir="",
    finished_at="",
):
    conn.execute(
        """
        insert into node_runs(job_id, node_key, status, log_path, run_dir, finished_at)
        values (?, ?, ?, ?, ?, ?)
        """,
        (job_id, node_key, status, log_path, run_dir, finished_at),
    )


def test_cleanup_removes_old_logs_and_run_dirs(tmp_path):
    data_dir, db = _make_db(tmp_path)
    run_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a" / "tok-1"
    run_dir.mkdir(parents=True)
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("old log")

    old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        _insert_node_run(
            conn,
            "job-1",
            "node-a",
            log_path=make_data_relative(log_path, data_dir),
            finished_at=old_finished,
        )

    cfg = CleanupConfig(log_retention_days=30, run_dir_retention_days=30)
    logs, dirs = cleanup_old_logs(db, data_dir, cfg)
    assert logs == 1
    assert dirs == 1
    assert not log_path.exists()
    assert not run_dir.exists()


def test_cleanup_keeps_recent_logs_and_run_dirs(tmp_path):
    data_dir, db = _make_db(tmp_path)
    run_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a" / "tok-1"
    run_dir.mkdir(parents=True)
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("recent log")

    recent_finished = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        _insert_node_run(
            conn,
            "job-1",
            "node-a",
            log_path=make_data_relative(log_path, data_dir),
            finished_at=recent_finished,
        )

    cfg = CleanupConfig(log_retention_days=30, run_dir_retention_days=30)
    logs, dirs = cleanup_old_logs(db, data_dir, cfg)
    assert logs == 0
    assert dirs == 0
    assert log_path.exists()
    assert run_dir.exists()


def test_cleanup_derives_run_dir_when_empty(tmp_path):
    data_dir, db = _make_db(tmp_path)
    run_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a" / "tok-1"
    run_dir.mkdir(parents=True)
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("old log")

    old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        _insert_node_run(
            conn,
            "job-1",
            "node-a",
            log_path=make_data_relative(log_path, data_dir),
            finished_at=old_finished,
        )

    cfg = CleanupConfig(log_retention_days=30, run_dir_retention_days=30)
    logs, dirs = cleanup_old_logs(db, data_dir, cfg)
    assert logs == 1
    assert dirs == 1
    assert not run_dir.exists()


def test_cleanup_config_from_settings():
    cfg = CleanupConfig.from_settings(
        {"cleanup": {"log_retention_days": "7", "run_dir_retention_days": 14}}
    )
    assert cfg.log_retention_days == 7
    assert cfg.run_dir_retention_days == 14
    assert cfg.keep_only_latest_run_per_node is True


def _pin_run_dir_order(old_dir, new_dir):
    """Make the keep-latest sweep's newest/oldest pick deterministic.

    ``find_extra_run_dirs`` orders run dirs by ``st_birthtime`` where available
    (macOS) and ``st_mtime`` otherwise (Linux). Two dirs created back-to-back
    can compare equal on platforms with coarser timestamps, leaving the pick
    to arbitrary directory iteration order — so pin the mtimes explicitly.
    """
    old_ts = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    new_ts = (datetime.now(UTC) - timedelta(days=1)).timestamp()
    os.utime(old_dir, (old_ts, old_ts))
    os.utime(new_dir, (new_ts, new_ts))


def test_cleanup_keeps_only_latest_run_per_node(tmp_path):
    data_dir, db = _make_db(tmp_path)
    node_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a"
    old_dir = node_dir / "tok-old"
    new_dir = node_dir / "tok-new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    (old_dir / "events.jsonl").write_text("old")
    (new_dir / "events.jsonl").write_text("new")
    _pin_run_dir_order(old_dir, new_dir)

    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        _insert_node_run(
            conn,
            "job-1",
            "node-a",
            run_dir=make_data_relative(old_dir, data_dir),
            finished_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
        )

    cfg = CleanupConfig(keep_only_latest_run_per_node=True)
    logs, dirs = cleanup_old_logs(db, data_dir, cfg)
    assert dirs == 1
    assert not old_dir.exists()
    assert new_dir.exists()
    with db._connect_read() as conn:
        row = conn.execute("select run_dir from node_runs").fetchone()
    assert row["run_dir"] == ""


def test_cleanup_respects_keep_all_runs_flag(tmp_path):
    data_dir, db = _make_db(tmp_path)
    node_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a"
    old_dir = node_dir / "tok-old"
    new_dir = node_dir / "tok-new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)

    cfg = CleanupConfig(keep_only_latest_run_per_node=False)
    logs, dirs = cleanup_old_logs(db, data_dir, cfg)
    assert dirs == 0
    assert old_dir.exists()
    assert new_dir.exists()


def test_cleanup_processes_expired_rows_in_chunks(tmp_path, monkeypatch):
    data_dir, db = _make_db(tmp_path)
    log_dir = data_dir / "logs" / "jobs"
    log_dir.mkdir(parents=True)
    chunk_size = cleanup_sweep.LOG_CLEANUP_CHUNK_SIZE
    per_status = chunk_size + 200  # two full chunks plus a partial one per status
    old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    recent_finished = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        for status in ("completed", "failed"):
            for i in range(per_status):
                log_path = log_dir / f"{status}-{i}.log"
                log_path.write_text("old")
                _insert_node_run(
                    conn,
                    "job-1",
                    f"{status}-node-{i}",
                    status=status,
                    log_path=make_data_relative(log_path, data_dir),
                    finished_at=old_finished,
                )
        recent_log = log_dir / "recent.log"
        recent_log.write_text("recent")
        _insert_node_run(
            conn,
            "job-1",
            "node-recent",
            log_path=make_data_relative(recent_log, data_dir),
            finished_at=recent_finished,
        )

    reads = 0
    real_connect_read = db._connect_read

    @contextmanager
    def counting_connect_read():
        nonlocal reads
        reads += 1
        with real_connect_read() as conn:
            yield conn

    monkeypatch.setattr(db, "_connect_read", counting_connect_read)

    cfg = CleanupConfig(
        log_retention_days=30, run_dir_retention_days=30, keep_only_latest_run_per_node=False
    )
    logs, dirs = cleanup_old_logs(db, data_dir, cfg)

    assert logs == per_status * 2
    assert dirs == 0
    assert not any(log_dir.glob("completed-*.log"))
    assert not any(log_dir.glob("failed-*.log"))
    assert recent_log.exists()
    # 1 job-id read + at least 2 chunk reads per status proves chunked paging.
    assert reads >= 3


def test_cleanup_deletes_files_outside_db_transaction(tmp_path, monkeypatch):
    data_dir, db = _make_db(tmp_path)
    node_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "node-a"
    old_dir = node_dir / "tok-old"
    new_dir = node_dir / "tok-new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    _pin_run_dir_order(old_dir, new_dir)
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("old log")

    old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        _insert_node_run(
            conn,
            "job-1",
            "node-a",
            log_path=make_data_relative(log_path, data_dir),
            run_dir=make_data_relative(old_dir, data_dir),
            finished_at=old_finished,
        )

    active_write_conns: list[DatabaseConnection] = []
    real_connect = db.connect

    @contextmanager
    def tracking_connect():
        with real_connect() as conn:
            active_write_conns.append(conn)
            try:
                yield conn
            finally:
                active_write_conns.remove(conn)

    monkeypatch.setattr(db, "connect", tracking_connect)

    real_remove_path = cleanup_sweep.remove_path
    removed_paths = []

    def spy_remove_path(path):
        assert not active_write_conns, f"filesystem deletion inside a DB transaction: {path}"
        removed_paths.append(path)
        real_remove_path(path)

    monkeypatch.setattr(cleanup_sweep, "remove_path", spy_remove_path)

    cfg = CleanupConfig(log_retention_days=30, run_dir_retention_days=30)
    logs, dirs = cleanup_old_logs(db, data_dir, cfg)

    assert removed_paths, "expected filesystem deletions to be exercised"
    assert logs == 1
    assert dirs == 2  # keep-latest sweep removes tok-old; retention deletes derived tok-new
    assert not log_path.exists()
    assert not old_dir.exists()
    assert not new_dir.exists()
    with db._connect_read() as conn:
        row = conn.execute("select run_dir, session_dir from node_runs").fetchone()
    assert row["run_dir"] == ""
    assert row["session_dir"] == ""


def test_cleanup_sql_cutoff_handles_mixed_finished_at_formats(tmp_path):
    data_dir, db = _make_db(tmp_path)
    log_dir = data_dir / "logs" / "jobs"
    log_dir.mkdir(parents=True)
    now = datetime.now(UTC)
    formats = {
        # production writers: current_timestamp / "%Y-%m-%d %H:%M:%S.%f"
        "space_old": (now - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S.%f"),
        # same calendar day as the cutoff but one hour newer: the coarse SQL
        # filter selects it, the exact per-row check must keep it.
        "space_boundary": (now - timedelta(days=30) + timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ),
        "iso_naive_old": (now - timedelta(days=40)).replace(tzinfo=None).isoformat(),
        "iso_aware_old": (now - timedelta(days=40)).isoformat(),
    }
    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        for node_key, finished_at in formats.items():
            log_path = log_dir / f"{node_key}.log"
            log_path.write_text(node_key)
            _insert_node_run(
                conn,
                "job-1",
                node_key,
                log_path=make_data_relative(log_path, data_dir),
                finished_at=finished_at,
            )

    cfg = CleanupConfig(
        log_retention_days=30, run_dir_retention_days=30, keep_only_latest_run_per_node=False
    )
    logs, dirs = cleanup_old_logs(db, data_dir, cfg)

    assert logs == 3
    assert not (log_dir / "space_old.log").exists()
    assert not (log_dir / "iso_naive_old.log").exists()
    assert not (log_dir / "iso_aware_old.log").exists()
    assert (log_dir / "space_boundary.log").exists()
