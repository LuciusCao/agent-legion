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
