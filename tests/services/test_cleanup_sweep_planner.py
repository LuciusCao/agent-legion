"""Issue #122: the expired node-run sweep forces its page query onto the index.

At prod scale one terminal status covers ~98% of node_runs, so the planner
chose seq scan + sort of the whole expired tail per page (19min/page). The
sweep now scopes ``set local enable_seqscan = off`` + a statement_timeout to
every page read; this test pins that contract and the sweep's correctness
under it.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from server.app.jobs import JobQueries
from server.app.services import cleanup_sweep
from server.app.storage_paths import make_data_relative
from tests.postgres_support import TEST_DATABASE_URL


def _make_db(tmp_path):
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, JobQueries(TEST_DATABASE_URL, jobs_dir)


class _RecordingConn:
    """Proxy that records executed statements while delegating to the real one."""

    def __init__(self, inner):
        self._inner = inner
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        if params is None:
            return self._inner.execute(statement)
        return self._inner.execute(statement, params)


def test_sweep_forces_index_and_serves_pages(tmp_path, monkeypatch):
    data_dir, db = _make_db(tmp_path)
    log_path = data_dir / "logs" / "jobs" / "job-1-node-a.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("old log")
    old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws1', 'ws1', 'demo_workflow')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir)"
            " values ('job-1', 'ws1', 'wf', 'question', 'job-1', '')"
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, log_path, finished_at)"
            " values ('job-1', 'node-a', 'completed', %s, %s)",
            (make_data_relative(log_path, data_dir), old_finished),
        )

    real_read = db._connect_read
    recorder: list[str] = []

    @contextmanager
    def _recording_read():
        with real_read() as conn:
            proxy = _RecordingConn(conn)
            yield proxy
            recorder.extend(proxy.statements)

    monkeypatch.setattr(db, "_connect_read", _recording_read)

    cutoff = datetime.now(UTC) - timedelta(days=30)
    logs, _dirs = cleanup_sweep.sweep_expired_node_runs(db, data_dir, cutoff, cutoff, {})

    # The expired log was still removed under the forced-index page read.
    assert logs == 1
    assert not log_path.exists()
    # Every page query is immediately preceded by the planner-pin setup.
    page_sql = " ".join(cleanup_sweep._EXPIRED_NODE_RUNS_SQL.split())
    normalized = [" ".join(statement.split()) for statement in recorder]
    page_indices = [i for i, statement in enumerate(normalized) if statement == page_sql]
    assert page_indices, "sweep never issued the expired-rows page query"
    for index in page_indices:
        assert normalized[index - 2] == "set local enable_seqscan = off"
        assert normalized[index - 1] == "set local statement_timeout = '30s'"


def test_sweep_skips_unresolvable_log_path_and_continues(tmp_path, monkeypatch):
    """#204 窄化：log_path 无法映射进 data 根（ManagedPathError）→ 该行跳过
    （warning），同一 pass 里其余行照常清理，游标照常推进。"""
    data_dir, db = _make_db(tmp_path)
    good_log = data_dir / "logs" / "jobs" / "job-2-node-a.log"
    good_log.parent.mkdir(parents=True, exist_ok=True)
    good_log.write_text("old log")
    old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws1', 'ws1', 'demo_workflow')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir)"
            " values ('job-1', 'ws1', 'wf', 'question', 'job-1', ''),"
            " ('job-2', 'ws1', 'wf', 'question', 'job-2', '')"
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, log_path, finished_at)"
            " values ('job-1', 'node-a', 'completed', %s, %s),"
            " ('job-2', 'node-a', 'completed', %s, %s)",
            (
                "/absolutely/outside/root.log",
                old_finished,
                make_data_relative(good_log, data_dir),
                old_finished,
            ),
        )

    cutoff = datetime.now(UTC) - timedelta(days=30)
    logs, _dirs = cleanup_sweep.sweep_expired_node_runs(db, data_dir, cutoff, cutoff, {})

    # 不可解析路径的行被跳过；可解析的行照常删除
    assert logs == 1
    assert good_log.exists() is False


def test_sweep_unresolvable_run_dir_warns_without_raising(tmp_path):
    """#204 窄化：run_dir 指向 data 根外（ManagedPathError）→ warning + 跳过，
    不再是宽捕获的静默 warning-everything（编程错误会上抛）。"""
    data_dir, db = _make_db(tmp_path)
    old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws1', 'ws1', 'demo_workflow')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir)"
            " values ('job-1', 'ws1', 'wf', 'question', 'job-1', '')"
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, run_dir, finished_at)"
            " values ('job-1', 'node-a', 'completed', '/outside/data/root', %s)",
            (old_finished,),
        )

    cutoff = datetime.now(UTC) - timedelta(days=30)
    # ManagedPathError 被窄捕获 → 不上抛；返回 0（该行跳过）
    logs, dirs = cleanup_sweep.sweep_expired_node_runs(db, data_dir, cutoff, cutoff, {})
    assert (logs, dirs) == (0, 0)


def test_sweep_survives_os_level_resolve_failures(tmp_path, monkeypatch):
    """Codex review on PR #251: resolve_data_path deliberately propagates
    PermissionError / RuntimeError (symlink loops) — one such row must be
    skipped (warning), not wedge the sweep cursor on every pass."""

    def _resolve_raises(path, data_dir, allow_missing=False):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cleanup_sweep, "resolve_data_path", _resolve_raises)
    data_dir, db = _make_db(tmp_path)
    old_finished = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws1', 'ws1', 'demo_workflow')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir)"
            " values ('job-1', 'ws1', 'wf', 'question', 'job-1', '')"
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, log_path, finished_at)"
            " values ('job-1', 'node-a', 'completed', %s, %s)",
            ("logs/jobs/job-1.log", old_finished),
        )

    cutoff = datetime.now(UTC) - timedelta(days=30)
    # PermissionError 被 per-row 吞掉（warning），不上抛、游标推进
    logs, _dirs = cleanup_sweep.sweep_expired_node_runs(db, data_dir, cutoff, cutoff, {})
    assert logs == 0
