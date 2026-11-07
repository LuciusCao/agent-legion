"""#1449: batch heartbeat × grouped result finish lease-row deadlock.

This is deliberately NOT the #690 node-counter three-party ring. Two real
write transactions suffice: heartbeat walks leases in execution_id order,
while the old finish batch walked them in job-mutation order. With opposite
orders, each side updates its first lease and then waits for the other side's
first lease — PostgreSQL aborts one side with SQLSTATE 40P01 while updating
executor_leases.

The driver pauses heartbeat after its first real lease UPDATE. Finish then
either completes its first real finish (the old opposite order) or is observed
blocked on heartbeat's first lease (the fixed shared order). Only then is
heartbeat released. The interleave is signal/lock driven, never sleep guessed.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker import heartbeat_batch as heartbeat_module
from server.app.agent_broker.mark_done_batch import mark_done_many
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db import transaction as transaction_module
from server.app.executors import _lease_finish_batch as finish_module
from server.app.executors import _lease_lifecycle as lifecycle_module
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult
from tests.helpers.agent_worker_api import seed_request

_WORKER = "heartbeat-deadlock-worker"


def _start(name: str, fn: Callable[[], Any], outcome: dict[str, Any]) -> threading.Thread:
    def run() -> None:
        try:
            outcome["result"] = fn()
        except Exception as exc:  # the racing 40P01 is the assertion's evidence
            outcome["error"] = exc

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=30)
    assert not thread.is_alive(), f"{thread.name} never resolved"


def _wait_finish_first_or_blocked(
    finish_first: threading.Event,
    finish_thread: threading.Thread,
    pids: dict[str, int],
    job_db,
) -> None:
    """Wait for the old first finish OR the fixed order's first lock wait."""
    deadline = time.monotonic() + 10
    with job_db._connect_read() as monitor:
        while time.monotonic() < deadline:
            if finish_first.is_set():
                return
            if not finish_thread.is_alive():
                raise AssertionError("finish ended before its first lease write")
            if "finish" in pids and "heartbeat" in pids:
                row = monitor.execute(
                    "select %s = any(pg_blocking_pids(%s)) as blocked",
                    (pids["heartbeat"], pids["finish"]),
                ).fetchone()
                if row is not None and row["blocked"]:
                    return
            time.sleep(0.005)
    raise AssertionError("finish neither wrote its first lease nor blocked on heartbeat")


def _seed_opposite_orders(
    job_db,
) -> tuple[AgentExecutionBroker, ExecutorLeaseRepository, list[dict]]:
    for job_id in ("heartbeat-job-a", "heartbeat-job-b"):
        seed_request(job_db, job_id=job_id, limit=10)
    AgentWorkerRegistry(job_db.dsn_identity).issue_token(
        worker_id=_WORKER,
        name=_WORKER,
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )
    broker = AgentExecutionBroker(job_db, data_dir=job_db.jobs_dir.parent)
    claims = [broker.claim(_WORKER) for _ in range(2)]
    assert all(claim is not None for claim in claims)
    repo = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    with job_db.connect() as conn:
        rows = conn.execute(
            "select l.id as lease_id, l.job_id,"
            " hashtext('job-mutation:' || l.job_id)::int as job_lock_key"
            " from executor_leases l order by l.job_id"
        ).fetchall()
        key_by_lease = {
            str(row["lease_id"]): (int(row["job_lock_key"]), str(row["job_id"])) for row in rows
        }
    # finish_many's old lease order was job-mutation order. Rename BOTH sides'
    # execution identity so heartbeat's execution_id order is exactly opposite.
    finish_order = sorted(claims, key=lambda claim: key_by_lease[claim.lease_id])
    records: list[dict[str, Any]] = []
    with job_db.connect() as conn:
        for index, claim in enumerate(finish_order):
            execution_id = "heartbeat-exec-z" if index == 0 else "heartbeat-exec-a"
            conn.execute(
                "update agent_execution_requests set execution_id=%s where execution_id=%s",
                (execution_id, claim.execution_id),
            )
            conn.execute(
                "update executor_leases set execution_id=%s where id=%s",
                (execution_id, claim.lease_id),
            )
            records.append(
                {
                    "execution_id": execution_id,
                    "lease_id": claim.lease_id,
                    "job_id": claim.job_id,
                }
            )
    return broker, repo, records


def _assert_all_still_in_flight(job_db) -> None:
    with job_db._connect_read() as conn:
        leases = conn.execute(
            "select status, count(*) as count from executor_leases group by status"
        ).fetchall()
        requests = conn.execute(
            "select state, count(*) as count from agent_execution_requests group by state"
        ).fetchall()
        runs = conn.execute(
            "select status, count(*) as count from node_runs group by status"
        ).fetchall()
    assert {str(row["status"]): int(row["count"]) for row in leases} == {"active": 2}
    assert {str(row["state"]): int(row["count"]) for row in requests} == {"claimed": 2}
    assert {str(row["status"]): int(row["count"]) for row in runs} == {"running": 2}


@pytest.mark.parametrize("stale_generation", [False, True])
def test_batch_heartbeat_and_grouped_finish_cannot_deadlock(
    job_db, tmp_path, monkeypatch: pytest.MonkeyPatch, stale_generation: bool
) -> None:
    broker, repo, records = _seed_opposite_orders(job_db)
    if stale_generation:
        with job_db.connect() as conn:
            conn.execute("update jobs set execution_generation=execution_generation+1")
            conn.execute(
                "update job_nodes set status='pending', execution_generation=1,"
                " started_at=null, finished_at=null"
            )

    # Bound a reintroduced ring without changing the production call path.
    real_write_transaction = transaction_module.write_transaction
    pids: dict[str, int] = {}

    @contextlib.contextmanager
    def timed_transaction(source):
        with real_write_transaction(source) as conn:
            conn.execute("set local deadlock_timeout='100ms'")
            conn.execute("set local lock_timeout='5s'")
            pids[threading.current_thread().name] = int(conn._raw.info.backend_pid)
            yield conn

    monkeypatch.setattr(heartbeat_module, "write_transaction", timed_transaction)
    monkeypatch.setattr(finish_module, "write_transaction", timed_transaction)

    heartbeat_first = threading.Event()
    release_heartbeat = threading.Event()
    finish_first = threading.Event()
    real_heartbeat_lease = lifecycle_module.heartbeat_lease
    real_finish_lease = finish_module.finish_lease
    heartbeat_calls = 0
    finish_calls = 0

    def heartbeat_after_first(conn, lease_id, ttl_seconds):
        nonlocal heartbeat_calls
        renewed = real_heartbeat_lease(conn, lease_id, ttl_seconds)
        heartbeat_calls += 1
        if heartbeat_calls == 1:
            heartbeat_first.set()
            assert release_heartbeat.wait(timeout=10), "heartbeat was never released"
        return renewed

    def finish_after_first(conn, lease_id, result, data_dir=None):
        nonlocal finish_calls
        verdict = real_finish_lease(conn, lease_id, result, data_dir)
        finish_calls += 1
        if finish_calls == 1:
            finish_first.set()
        return verdict

    monkeypatch.setattr(lifecycle_module, "heartbeat_lease", heartbeat_after_first)
    monkeypatch.setattr(finish_module, "finish_lease", finish_after_first)

    heartbeat_outcome: dict[str, Any] = {}
    finish_outcome: dict[str, Any] = {}
    result = ExecutionResult(status="completed", exit_code=0)

    def finish_results() -> None:
        writes = [(row["lease_id"], result, None) for row in records]
        verdicts, _callbacks = finish_module.finish_many(repo, writes)
        finish_outcome["verdicts"] = verdicts
        closed = mark_done_many(
            broker,
            [
                (
                    row["execution_id"],
                    _WORKER,
                    row["lease_id"],
                    {"status": "completed", "exit_code": 0},
                )
                for row in records
            ],
        )
        finish_outcome["closed"] = closed

    heartbeat_thread = _start(
        "heartbeat",
        lambda: heartbeat_module.batch_heartbeat(
            broker,
            _WORKER,
            [{"execution_id": row["execution_id"], "lease_id": row["lease_id"]} for row in records],
        ),
        heartbeat_outcome,
    )
    assert heartbeat_first.wait(timeout=10), "heartbeat never updated its first lease"
    finish_thread = _start("finish", finish_results, finish_outcome)
    try:
        _wait_finish_first_or_blocked(finish_first, finish_thread, pids, job_db)
        _assert_all_still_in_flight(job_db)
        release_heartbeat.set()
    finally:
        release_heartbeat.set()
        _join(heartbeat_thread)
        _join(finish_thread)

    # Before the fix this assertion fails with one real psycopg 40P01. After
    # the fix both sides commit and every terminal/verdict invariant holds.
    assert heartbeat_outcome.get("error") is None, heartbeat_outcome.get("error")
    assert finish_outcome.get("error") is None, finish_outcome.get("error")
    assert finish_outcome["verdicts"] == [True, True]
    assert finish_outcome["closed"] == [row["lease_id"] for row in records]
    renewed = heartbeat_outcome["result"]
    assert renewed["lost"] == []
    assert sorted(renewed["renewed"]) == sorted(row["execution_id"] for row in records)

    with job_db._connect_read() as conn:
        leases = conn.execute("select status from executor_leases order by id").fetchall()
        requests = conn.execute(
            "select execution_id, worker_id, state, outcome_json from agent_execution_requests"
            " order by execution_id"
        ).fetchall()
        runs = conn.execute("select status from node_runs order by id").fetchall()
        nodes = conn.execute(
            "select job_id, status, execution_generation from job_nodes order by job_id"
        ).fetchall()
        jobs = conn.execute("select execution_generation from jobs order by id").fetchall()
    assert [str(row["status"]) for row in leases] == ["released", "released"]
    assert [str(row["state"]) for row in requests] == ["done", "done"]
    assert {str(row["worker_id"]) for row in requests} == {_WORKER}
    assert all('"completed"' in str(row["outcome_json"]) for row in requests)
    assert [str(row["status"]) for row in runs] == ["completed", "completed"]
    if stale_generation:
        assert [str(row["status"]) for row in nodes] == ["pending", "pending"]
        assert [int(row["execution_generation"]) for row in nodes] == [1, 1]
        assert [int(row["execution_generation"]) for row in jobs] == [1, 1]
    else:
        assert [str(row["status"]) for row in nodes] == ["completed", "completed"]

    settled = heartbeat_module.batch_heartbeat(
        broker,
        _WORKER,
        [{"execution_id": row["execution_id"], "lease_id": row["lease_id"]} for row in records],
    )
    assert settled["renewed"] == []
    assert settled["lost"] == []
    assert sorted(settled["settled"]) == sorted(row["execution_id"] for row in records)
