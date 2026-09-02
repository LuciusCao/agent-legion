"""Execution-plane terminal-row retention sweep (issue #354, plan item 2/4).

Covers the acceptance surface: retention disabled deletes nothing (default
safety), enabled retention deletes only terminal rows past the window
(boundary inclusive/exclusive), the online paths are untouched (active
leases / queued-claimed-reporting requests survive), and the sweep is
resumable — an interrupted pass continues from the persisted cursor without
re-deleting committed batches or skipping rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.app.db.transaction import write_transaction
from server.app.services.execution_retention import (
    BATCH_SIZE,
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    execution_retention_days,
    sweep_expired_executions,
)
from server.app.services.execution_retention_sweeper import ExecutionRetentionThread
from server.app.services.instance_settings_store import InstanceSettingsStore
from tests.postgres_support import TEST_DATABASE_URL

_WORKSPACE = "test-workspace"
_NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _put_settings(days: int) -> None:
    store = InstanceSettingsStore(TEST_DATABASE_URL)
    store.put({"execution_retention_days": days})


def _clear_settings() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from global_settings where key='instance'")


def _seed_workspace_and_job(job_db, *, job_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'Test', 'demo_workflow') on conflict(id) do nothing",
            (_WORKSPACE,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values (%s, %s, 'question', %s)",
            (job_id, _WORKSPACE, job_id),
        )


def _insert_request(
    job_db,
    *,
    execution_id: str,
    state: str,
    finished_at: datetime | None,
    manifest: str = "{}",
    node_key: str = "generate",
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into agent_execution_requests(
              execution_id, workspace_id, job_id, node_key, kind, agent_id,
              agent_definition_hash, node_concurrency_limit, state, queued_at,
              finished_at, manifest_json
            ) values (%s, %s, %s, %s, 'agent', 'agent-1', 'hash', 1, %s, %s, %s, %s)
            """,
            (
                execution_id,
                _WORKSPACE,
                "job-1",
                node_key,
                state,
                _NOW - timedelta(days=40),
                finished_at,
                manifest,
            ),
        )


def _insert_lease(
    job_db,
    *,
    lease_id: str,
    status: str,
    expires_at: datetime,
) -> None:
    with job_db.connect() as conn:
        run = conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, finished_at)
            values ('job-1', 'generate', 'completed', %s, %s) returning id
            """,
            (_NOW - timedelta(days=40), expires_at),
        ).fetchone()
        conn.execute(
            """
            insert into executor_leases(
              id, execution_id, executor_id, workspace_id, job_id, node_key,
              node_run_id, status, acquired_at, heartbeat_at, expires_at
            ) values (%s, %s, 'executor-1', %s, 'job-1', 'generate', %s, %s, %s, %s, %s)
            """,
            (
                lease_id,
                f"exec-{lease_id}",
                _WORKSPACE,
                run["id"],
                status,
                _NOW - timedelta(days=40),
                _NOW - timedelta(days=40),
                expires_at,
            ),
        )


def _insert_token_usage(
    job_db,
    *,
    node_run_finished: datetime | None,
    created_at: datetime,
    total_tokens: int = 10,
) -> int:
    with job_db.connect() as conn:
        run = conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, finished_at)
            values ('job-1', 'generate', 'completed', %s, %s) returning id
            """,
            (created_at, node_run_finished),
        ).fetchone()
        row = conn.execute(
            """
            insert into node_run_token_usage(
              node_run_id, job_id, workspace_id, node_key, total_tokens, created_at
            ) values (%s, 'job-1', %s, 'generate', %s, %s) returning id
            """,
            (run["id"], _WORKSPACE, total_tokens, created_at),
        ).fetchone()
        return int(row["id"])


def _counts(job_db) -> dict[str, int]:
    with job_db._connect_read() as conn:
        return {
            "requests": conn.execute(
                "select count(*) as c from agent_execution_requests"
            ).fetchone()["c"],
            "leases": conn.execute("select count(*) as c from executor_leases").fetchone()["c"],
            "usage": conn.execute("select count(*) as c from node_run_token_usage").fetchone()["c"],
        }


def test_retention_days_reads_instance_document(job_db) -> None:
    _clear_settings()
    assert execution_retention_days(TEST_DATABASE_URL) == 0
    _put_settings(30)
    assert execution_retention_days(TEST_DATABASE_URL) == 30
    # Defensive degradation: a non-int or negative value means disabled.
    InstanceSettingsStore(TEST_DATABASE_URL).put({"execution_retention_days": "30"})
    assert execution_retention_days(TEST_DATABASE_URL) == 0


def test_disabled_retention_deletes_nothing(job_db) -> None:
    _seed_workspace_and_job(job_db, job_id="job-1")
    _insert_request(
        job_db, execution_id="old-done", state="done", finished_at=_NOW - timedelta(days=400)
    )
    _clear_settings()

    totals = sweep_expired_executions(TEST_DATABASE_URL, now=_NOW)

    assert totals == {
        "agent_execution_requests": 0,
        "executor_leases": 0,
        "node_run_token_usage": 0,
    }
    assert _counts(job_db)["requests"] == 1


def test_sweep_deletes_only_terminal_rows_past_window(job_db) -> None:
    _seed_workspace_and_job(job_db, job_id="job-1")
    retention_days = 30
    cutoff = _NOW - timedelta(days=retention_days)
    _put_settings(retention_days)
    # Past-window terminal rows (both terminal states) — deleted.
    _insert_request(
        job_db, execution_id="old-done", state="done", finished_at=cutoff - timedelta(seconds=1)
    )
    _insert_request(
        job_db,
        execution_id="old-cancelled",
        state="cancelled",
        finished_at=cutoff - timedelta(days=1),
    )
    # Exactly-on-the-boundary row — kept (cutoff is exclusive: finished_at < cutoff).
    _insert_request(job_db, execution_id="edge-done", state="done", finished_at=cutoff)
    # Fresh terminal row — kept.
    _insert_request(
        job_db, execution_id="new-done", state="done", finished_at=_NOW - timedelta(days=1)
    )
    # Old but NOT terminal — kept (the online claim path owns it). Distinct
    # node keys: the one-active-request-per-node unique index covers
    # queued/claimed/reporting.
    _insert_request(job_db, execution_id="old-queued", state="queued", finished_at=None)
    _insert_request(
        job_db,
        execution_id="old-claimed",
        state="claimed",
        finished_at=cutoff - timedelta(days=2),
        node_key="generate-2",
    )

    totals = sweep_expired_executions(TEST_DATABASE_URL, now=_NOW)

    assert totals["agent_execution_requests"] == 2
    with job_db._connect_read() as conn:
        remaining = {
            str(row["execution_id"])
            for row in conn.execute("select execution_id from agent_execution_requests").fetchall()
        }
    assert remaining == {"edge-done", "new-done", "old-queued", "old-claimed"}


def test_sweep_deletes_non_active_leases_and_keeps_active(job_db) -> None:
    _seed_workspace_and_job(job_db, job_id="job-1")
    retention_days = 30
    cutoff = _NOW - timedelta(days=retention_days)
    _put_settings(retention_days)
    _insert_lease(
        job_db, lease_id="old-released", status="released", expires_at=cutoff - timedelta(days=1)
    )
    _insert_lease(
        job_db, lease_id="old-expired", status="expired", expires_at=cutoff - timedelta(days=2)
    )
    # Old AND non-active is deleted; active is never touched, however old.
    _insert_lease(
        job_db, lease_id="old-active", status="active", expires_at=cutoff - timedelta(days=3)
    )
    _insert_lease(
        job_db, lease_id="new-released", status="released", expires_at=_NOW - timedelta(days=1)
    )

    totals = sweep_expired_executions(TEST_DATABASE_URL, now=_NOW)

    assert totals["executor_leases"] == 2
    with job_db._connect_read() as conn:
        remaining = {
            str(row["id"]) for row in conn.execute("select id from executor_leases").fetchall()
        }
    assert remaining == {"old-active", "new-released"}


def test_sweep_deletes_usage_of_finished_runs_only(job_db) -> None:
    _seed_workspace_and_job(job_db, job_id="job-1")
    retention_days = 30
    cutoff = _NOW - timedelta(days=retention_days)
    _put_settings(retention_days)
    # Usage of a finished run past the window — deleted.
    old_usage = _insert_token_usage(
        job_db, node_run_finished=cutoff - timedelta(days=1), created_at=cutoff - timedelta(days=1)
    )
    # Usage of a run still executing (finished_at NULL) — kept even when old.
    _insert_token_usage(job_db, node_run_finished=None, created_at=cutoff - timedelta(days=1))
    # Fresh usage of a finished run — kept.
    _insert_token_usage(
        job_db, node_run_finished=_NOW - timedelta(days=1), created_at=_NOW - timedelta(days=1)
    )

    totals = sweep_expired_executions(TEST_DATABASE_URL, now=_NOW)

    assert totals["node_run_token_usage"] == 1
    with job_db._connect_read() as conn:
        remaining = {
            int(row["id"]) for row in conn.execute("select id from node_run_token_usage").fetchall()
        }
    assert old_usage not in remaining
    assert len(remaining) == 2
    # node_runs rows themselves are never deleted (audit trail).
    with job_db._connect_read() as conn:
        runs = conn.execute("select count(*) as c from node_runs").fetchone()["c"]
    assert runs == 3


def test_sweep_is_resumable_after_partial_pass(job_db, monkeypatch) -> None:
    """An interrupt after the first batch commits loses no work and repeats none.

    Simulates a crash: the second page read raises. The rerun resumes from
    the persisted cursor (the first batch's keyset position), deletes the
    remaining rows, and the grand total equals the full-tail count.
    """
    _seed_workspace_and_job(job_db, job_id="job-1")
    retention_days = 30
    cutoff = _NOW - timedelta(days=retention_days)
    _put_settings(retention_days)
    total_rows = BATCH_SIZE + 10
    for index in range(total_rows):
        _insert_request(
            job_db,
            execution_id=f"done-{index:05d}",
            state="done",
            finished_at=cutoff - timedelta(seconds=total_rows - index),
        )

    from server.app.services import execution_retention as retention_module

    real_queries = retention_module._queries(TEST_DATABASE_URL)
    calls = {"n": 0}

    class _Interrupted(Exception):
        pass

    class _FlakyProxy:
        """Wraps the real queries; the second page read simulates a crash."""

        def page_terminal_agent_requests(self, *args):
            calls["n"] += 1
            if calls["n"] == 2:
                raise _Interrupted("simulated crash between batches")
            return real_queries.page_terminal_agent_requests(*args)

        def __getattr__(self, name):
            return getattr(real_queries, name)

    monkeypatch.setattr(retention_module, "_queries", lambda source: _FlakyProxy())
    with pytest.raises(_Interrupted):
        sweep_expired_executions(TEST_DATABASE_URL, now=_NOW)
    monkeypatch.setattr(retention_module, "_queries", lambda source: real_queries)

    # First pass deleted exactly one batch before dying.
    assert _counts(job_db)["requests"] == total_rows - BATCH_SIZE

    # Rerun finishes the tail: no re-deletes (rowcounts add to the remainder).
    totals = sweep_expired_executions(TEST_DATABASE_URL, now=_NOW)
    assert totals["agent_execution_requests"] == total_rows - BATCH_SIZE
    assert _counts(job_db)["requests"] == 0

    # A third pass with nothing left is a cheap empty page read.
    again = sweep_expired_executions(TEST_DATABASE_URL, now=_NOW)
    assert again["agent_execution_requests"] == 0


def test_sweep_thread_run_once_delegates_and_survives_failure(monkeypatch) -> None:
    """The driver thread's run_once delegates to the sweep; a failure raises
    out of run_once (the _loop catch is what keeps the thread alive)."""
    thread = ExecutionRetentionThread(
        TEST_DATABASE_URL, interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS
    )
    calls = {"n": 0}

    def flaky_sweep(source):
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "server.app.services.execution_retention_sweeper.sweep_expired_executions", flaky_sweep
    )
    with pytest.raises(RuntimeError):
        thread.run_once()
    assert calls["n"] == 1
