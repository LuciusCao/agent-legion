from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from server.app.db.connection import DatabaseConnection
from server.app.jobs import JobQueries
from server.app.services import cleanup_sweep
from server.app.services.cleanup_sweep_store import CleanupSweepStore
from server.app.services.log_cleanup import CleanupConfig, cleanup_old_logs
from server.app.storage_paths import make_data_relative
from tests.helpers.job_dirs import job_storage_ref, make_job_dir, pin_run_dir_order
from tests.postgres_support import TEST_DATABASE_URL


def _make_db(tmp_path):
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, JobQueries(TEST_DATABASE_URL, jobs_dir)


def _seed_workspace(conn, workspace_id="ws1"):
    conn.execute(
        "insert into workspaces(id, name) values (%s, %s)",
        (workspace_id, workspace_id),
    )


def _insert_job(conn, job_id, workspace_id="ws1"):
    conn.execute(
        """
        insert into jobs(id, workspace_id, source_type, source_id, storage_dir) values (%s, %s, 'question', %s, %s)
        """,
        (job_id, workspace_id, job_id, job_storage_ref(workspace_id, job_id)),
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
        values (%s, %s, %s, %s, %s, %s)
        """,
        (job_id, node_key, status, log_path, run_dir, finished_at),
    )


def test_cleanup_removes_old_logs_and_run_dirs(tmp_path):
    data_dir, db = _make_db(tmp_path)
    run_dir = make_job_dir(data_dir, "ws1", "job-1") / "runs" / "node-a" / "tok-1"
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
            run_dir=make_data_relative(run_dir, data_dir),
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
    run_dir = make_job_dir(data_dir, "ws1", "job-1") / "runs" / "node-a" / "tok-1"
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


def test_cleanup_keeps_run_dir_when_row_run_dir_empty(tmp_path):
    """#1315: 过期行的 run_dir 为空 = 该行不拥有任何目录（从未记录，或已被
    keep-only-latest 删除并清空列）。sweep 不得为这类行推导/删除任何目录；
    残留目录由 keep-only-latest 按目录自身时间戳兜底。"""
    data_dir, db = _make_db(tmp_path)
    run_dir = make_job_dir(data_dir, "ws1", "job-1") / "runs" / "node-a" / "tok-1"
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
    assert dirs == 0
    assert run_dir.exists()


def test_cleanup_empty_run_dir_row_never_deletes_current_run_dir(tmp_path):
    """#1315 回归：过期空 run_dir 行与「当前活跃 worker 目录」共存时，sweep
    不得误删活跃目录——fallback 推导出的 max-mtime token 目录属于更新的 run
    （由更新行的 run_dir 与 retention 时间线管辖），不属于这条过期行。"""
    data_dir, db = _make_db(tmp_path)
    worker_dir = make_job_dir(data_dir, "ws1", "job-1") / "runs" / "node-a" / "worker"
    worker_dir.mkdir(parents=True)
    (worker_dir / "events.jsonl").write_text("current run events")

    old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    recent_finished = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        # 历史过期行：run_dir 已丢失（空）——#1315 的触发形态
        _insert_node_run(conn, "job-1", "node-a", finished_at=old_finished)
        # 当前 run 的归属行：run_dir 指向 worker 目录，远在 retention 线内
        _insert_node_run(
            conn,
            "job-1",
            "node-a",
            run_dir=make_data_relative(worker_dir, data_dir),
            finished_at=recent_finished,
        )

    cfg = CleanupConfig(log_retention_days=30, run_dir_retention_days=30)
    _logs, dirs = cleanup_old_logs(db, data_dir, cfg)

    assert dirs == 0
    assert (worker_dir / "events.jsonl").read_text() == "current run events"


def test_cleanup_shared_run_dir_claim_strips_stale_claim_without_deleting(tmp_path):
    """#1315 r1：存量共享认领（部署前 finish 不转移所有权，过期行与新鲜行
    共享同一非空 run_dir——worker 固定 token 目录形态）。sweep 不得删除共享
    目录，只缴清过期行的陈旧认领；目录等唯一认领行自己过期时才删除。"""
    data_dir, db = _make_db(tmp_path)
    worker_dir = make_job_dir(data_dir, "ws1", "job-1") / "runs" / "node-a" / "worker"
    worker_dir.mkdir(parents=True)
    (worker_dir / "events.jsonl").write_text("current run events")
    worker_rel = make_data_relative(worker_dir, data_dir)

    old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    recent_finished = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        _insert_node_run(conn, "job-1", "node-a", run_dir=worker_rel, finished_at=old_finished)
        _insert_node_run(conn, "job-1", "node-a", run_dir=worker_rel, finished_at=recent_finished)

    cfg = CleanupConfig(log_retention_days=30, run_dir_retention_days=30)
    _logs, dirs = cleanup_old_logs(db, data_dir, cfg)

    assert dirs == 0
    assert (worker_dir / "events.jsonl").read_text() == "current run events"
    with db._connect_read() as conn:
        rows = conn.execute("select run_dir from node_runs order by finished_at").fetchall()
    # 过期行的认领被缴清；新鲜行成为唯一认领者。
    assert [row["run_dir"] for row in rows] == ["", worker_rel]

    # 唯一认领行自己过期后，目录才被删除（finished_at 晚于游标，下轮必被啃到）。
    with db.connect() as conn:
        conn.execute(
            "update node_runs set finished_at = %s where run_dir = %s",
            ((datetime.now(UTC) - timedelta(days=39)).isoformat(), worker_rel),
        )
    _logs, dirs = cleanup_old_logs(db, data_dir, cfg)
    assert dirs == 1
    assert not worker_dir.exists()


def test_cleanup_same_chunk_expired_shared_claims_still_delete_dir(tmp_path):
    """#1315 r2 P2-1：同 chunk 两条过期共享认领行（40d / 39d，批缴清尚未
    flush）——守卫判据必须是「存在更新认领者」：c1 缴清（c2 更新），c2 是
    最新认领者=指定所有者、无更新认领者 → 正常删除目录。「任一其他认领者」
    判据会把 c2 一并缴清：游标越过、行永不重访，目录永久泄漏。"""
    data_dir, db = _make_db(tmp_path)
    worker_dir = make_job_dir(data_dir, "ws1", "job-1") / "runs" / "node-a" / "worker"
    worker_dir.mkdir(parents=True)
    (worker_dir / "events.jsonl").write_text("expired run events")
    worker_rel = make_data_relative(worker_dir, data_dir)

    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        _insert_node_run(
            conn,
            "job-1",
            "node-a",
            run_dir=worker_rel,
            finished_at=(datetime.now(UTC) - timedelta(days=40)).isoformat(),
        )
        _insert_node_run(
            conn,
            "job-1",
            "node-a",
            run_dir=worker_rel,
            finished_at=(datetime.now(UTC) - timedelta(days=39)).isoformat(),
        )

    cfg = CleanupConfig(log_retention_days=30, run_dir_retention_days=30)
    _logs, dirs = cleanup_old_logs(db, data_dir, cfg)

    assert dirs == 1
    assert not worker_dir.exists()
    with db._connect_read() as conn:
        rows = conn.execute("select run_dir from node_runs order by finished_at").fetchall()
    # c1 的认领被缴清；c2（最新认领者）执行了删除，保留其已失效的认领值。
    assert [row["run_dir"] for row in rows] == ["", worker_rel]


def test_cleanup_config_from_settings():
    cfg = CleanupConfig.from_settings(
        {"cleanup": {"log_retention_days": "7", "run_dir_retention_days": 14}}
    )
    assert cfg.log_retention_days == 7
    assert cfg.run_dir_retention_days == 14
    assert cfg.keep_only_latest_run_per_node is True


def test_cleanup_keeps_only_latest_run_per_node(tmp_path):
    data_dir, db = _make_db(tmp_path)
    node_dir = make_job_dir(data_dir, "ws1", "job-1") / "runs" / "node-a"
    old_dir = node_dir / "tok-old"
    new_dir = node_dir / "tok-new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    (old_dir / "events.jsonl").write_text("old")
    (new_dir / "events.jsonl").write_text("new")
    pin_run_dir_order(old_dir, new_dir)

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
    node_dir = make_job_dir(data_dir, "ws1", "job-1") / "runs" / "node-a"
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
    # At least 2 chunk reads per status proves chunked paging.
    assert reads >= 3


def test_cleanup_deletes_files_outside_db_transaction(tmp_path, monkeypatch):
    data_dir, db = _make_db(tmp_path)
    node_dir = make_job_dir(data_dir, "ws1", "job-1") / "runs" / "node-a"
    old_dir = node_dir / "tok-old"
    new_dir = node_dir / "tok-new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    pin_run_dir_order(old_dir, new_dir)
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
    # keep-latest removes tok-old and clears the row's run_dir; with the #1315
    # fail-closed rule the retention pass must not derive a replacement target
    # for the now-empty claim, so tok-new (owned by no expired row) survives.
    assert dirs == 1
    assert not log_path.exists()
    assert not old_dir.exists()
    assert new_dir.exists()
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


def test_sweep_cursor_store_defaults_and_roundtrip():
    store = CleanupSweepStore(TEST_DATABASE_URL)
    assert store.load("completed:log") == (datetime.min.replace(tzinfo=UTC), 0)
    mark = datetime(2026, 1, 1, tzinfo=UTC)
    store.save("completed:log", mark, 42)
    store.save("failed:run_dir", mark, 7)
    assert store.load("completed:log") == (mark, 42)
    assert store.load("failed:run_dir") == (mark, 7)


def test_cleanup_resumes_from_persisted_sweep_cursor(tmp_path):
    data_dir, db = _make_db(tmp_path)
    log_dir = data_dir / "logs" / "jobs"
    log_dir.mkdir(parents=True)
    old_finished = datetime.now(UTC) - timedelta(days=40)

    old_log = log_dir / "old.log"
    old_log.write_text("old")
    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        _insert_node_run(
            conn,
            "job-1",
            "node-a",
            log_path=make_data_relative(old_log, data_dir),
            finished_at=old_finished.isoformat(),
        )

    cfg = CleanupConfig(
        log_retention_days=30, run_dir_retention_days=30, keep_only_latest_run_per_node=False
    )
    logs, _dirs = cleanup_old_logs(db, data_dir, cfg)
    assert logs == 1

    store = CleanupSweepStore(TEST_DATABASE_URL)
    finished, row_id = store.load("completed:log")
    assert finished == old_finished
    assert row_id > 0

    # A late-arriving row below the cursor is never swept: cleanup is
    # best-effort and rows are never deleted, so re-paging the whole expired
    # tail every pass is traded away (see CleanupSweepStore docstring).
    older_log = log_dir / "older.log"
    older_log.write_text("older")
    with db.connect() as conn:
        _insert_node_run(
            conn,
            "job-1",
            "node-b",
            log_path=make_data_relative(older_log, data_dir),
            finished_at=(old_finished - timedelta(days=1)).isoformat(),
        )
    logs, _dirs = cleanup_old_logs(db, data_dir, cfg)
    assert logs == 0
    assert older_log.exists()

    # A row that expires after the cursor (retention cutoff moved forward)
    # is picked up by the next pass.
    newer_log = log_dir / "newer.log"
    newer_log.write_text("newer")
    with db.connect() as conn:
        _insert_node_run(
            conn,
            "job-1",
            "node-c",
            log_path=make_data_relative(newer_log, data_dir),
            finished_at=(old_finished + timedelta(days=1)).isoformat(),
        )
    logs, _dirs = cleanup_old_logs(db, data_dir, cfg)
    assert logs == 1
    assert not newer_log.exists()


def test_cleanup_sweep_cursors_track_log_and_run_dir_retentions_independently(tmp_path):
    """Regression: the run-dir pass must not advance the log cursor past a row
    whose log is not yet expired (log_retention 7d > run_dir_retention 3d)."""
    data_dir, db = _make_db(tmp_path)
    log_dir = data_dir / "logs" / "jobs"
    log_dir.mkdir(parents=True)
    run_dir = make_job_dir(data_dir, "ws1", "job-1") / "runs" / "node-a" / "tok-1"
    run_dir.mkdir(parents=True)
    log_path = log_dir / "job-1-node-a.log"
    log_path.write_text("log")

    now = datetime.now(UTC)
    with db.connect() as conn:
        _seed_workspace(conn)
        _insert_job(conn, "job-1")
        _insert_node_run(
            conn,
            "job-1",
            "node-a",
            log_path=make_data_relative(log_path, data_dir),
            run_dir=make_data_relative(run_dir, data_dir),
            finished_at=(now - timedelta(days=5)).isoformat(),
        )

    cfg = CleanupConfig(
        log_retention_days=7, run_dir_retention_days=3, keep_only_latest_run_per_node=False
    )
    logs, dirs = cleanup_old_logs(db, data_dir, cfg, now=now)
    assert (logs, dirs) == (0, 1)
    assert log_path.exists()
    assert not run_dir.exists()

    # Three days later the log crosses its own retention; the log cursor was
    # never advanced by the run-dir pass, so the log is still swept.
    logs, _dirs = cleanup_old_logs(db, data_dir, cfg, now=now + timedelta(days=3))
    assert logs == 1
    assert not log_path.exists()
