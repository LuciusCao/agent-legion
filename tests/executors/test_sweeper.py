"""Standalone lease sweeper (phase 3, task 8).

Covers: the synchronous startup sweep expiring stale leases, interval-loop
recovery of orphaned running jobs, lease renewal for live remote executions
across queued/claimed/requeued states, per-step fault isolation, concurrent
sweepers expiring a lease exactly once, and idempotent stop.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import read_connection
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ClaimedExecution
from server.app.executors.remote_broker import (
    RemoteExecutionBroker,
    RemoteExecutionPayload,
)
from server.app.executors.sweeper import SweeperThread
from server.app.jobs import JobQueries
from tests.executors.leases.helpers import _claim_request, _setup_workspace

EXECUTOR_ID = "pi-remote"
CAPABILITY = "review_keywords"
NODE_KEY = "review_keywords"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.sqlite"


@pytest.fixture
def job_db(db_path: Path, tmp_path: Path) -> JobQueries:
    return JobQueries(db_path, tmp_path / "jobs")


@pytest.fixture
def leases(db_path: Path, job_db: JobQueries) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(db_path, job_db=job_db)


@pytest.fixture
def broker(db_path: Path, tmp_path: Path) -> RemoteExecutionBroker:
    init_db(db_path)
    return RemoteExecutionBroker(db_path, tmp_path / "bundles", claim_timeout_seconds=60.0)


def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _lease_row(job_db: JobQueries, lease_id: str) -> dict[str, Any]:
    with job_db.connect() as conn:
        row = conn.execute("select * from executor_leases where id=?", (lease_id,)).fetchone()
    assert row is not None
    return dict(row)


def _remote_row(db_path: Path, execution_id: str) -> dict[str, Any]:
    with read_connection(db_path) as conn:
        row = conn.execute(
            "select state, requeue_count from remote_executions where execution_id=?",
            (execution_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def _force_expired(job_db: JobQueries, lease_id: str) -> None:
    past = datetime.now(UTC) - timedelta(seconds=10)
    with job_db.connect() as conn:
        conn.execute(
            "update executor_leases set expires_at=? where id=?",
            (past.strftime("%Y-%m-%d %H:%M:%S.%f"), lease_id),
        )


def _submit_remote(broker: RemoteExecutionBroker, claim: ClaimedExecution) -> None:
    broker.submit(
        RemoteExecutionPayload(
            execution_id=claim.execution_id,
            lease_id=claim.lease_id,
            job_id=claim.job_id,
            node_key=claim.node_key,
            capability=CAPABILITY,
            bundle_name=f"{claim.execution_id}.tar.gz",
            manifest={
                "job_id": claim.job_id,
                "node_key": claim.node_key,
                "run_token": "tok",
                "expected_outputs": [],
            },
        )
    )


def _claim(leases: ExecutorLeaseRepository, job_db: JobQueries, ttl: int = 60) -> ClaimedExecution:
    workspace_id, job_id = _setup_workspace(
        job_db, "WS", EXECUTOR_ID, 20, node_key=NODE_KEY, local_limit=None
    )
    claim = leases.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            node_key=NODE_KEY,
            executor_id=EXECUTOR_ID,
            local_node_limit=None,
            ttl=ttl,
        )
    )
    assert claim is not None
    return claim


def test_sweeper_expires_stale_leases(
    leases: ExecutorLeaseRepository, broker: RemoteExecutionBroker, job_db: JobQueries
) -> None:
    claim = _claim(leases, job_db)
    _force_expired(job_db, claim.lease_id)

    sweeper = SweeperThread(leases, broker, interval_seconds=0.1)
    sweeper.start()
    try:
        # start() runs the startup sweep synchronously before returning.
        assert _lease_row(job_db, claim.lease_id)["status"] == "expired"
    finally:
        sweeper.stop()


def test_sweeper_renews_remote_execution_leases(
    db_path: Path, tmp_path: Path, leases: ExecutorLeaseRepository, job_db: JobQueries
) -> None:
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles", claim_timeout_seconds=0.3)
    claim = _claim(leases, job_db, ttl=1)
    claimed_at = time.monotonic()
    _submit_remote(broker, claim)

    sweeper = SweeperThread(leases, broker, interval_seconds=0.1, lease_ttl_seconds=1)
    sweeper.start()
    try:
        expires_before = str(_lease_row(job_db, claim.lease_id)["expires_at"])
        # A sweeper tick renews the lease backing the queued remote execution.
        assert _wait_for(
            lambda: str(_lease_row(job_db, claim.lease_id)["expires_at"]) > expires_before
        )
        # A worker claims the row but never heartbeats; the claim times out and
        # requeues while the sweeper keeps renewing the lease.
        assert broker.dequeue("w1", {CAPABILITY}) is not None
        assert _wait_for(lambda: _remote_row(db_path, claim.execution_id)["state"] == "queued")
        assert _remote_row(db_path, claim.execution_id)["requeue_count"] >= 1
        # Past the original one-second ttl the lease is still active: renewal
        # covered the queued, claimed, and requeued states.
        assert _wait_for(lambda: time.monotonic() - claimed_at > 1.2)
        assert _lease_row(job_db, claim.lease_id)["status"] == "active"
    finally:
        sweeper.stop()


def test_sweeper_recovers_orphaned_jobs(
    leases: ExecutorLeaseRepository, broker: RemoteExecutionBroker, job_db: JobQueries
) -> None:
    sweeper = SweeperThread(leases, broker, interval_seconds=0.1)
    sweeper.start()
    try:
        # The orphan appears after startup, so the interval loop must recover it.
        _, job_id = _setup_workspace(
            job_db, "WS", EXECUTOR_ID, 20, node_key=NODE_KEY, local_limit=None
        )
        with job_db.connect() as conn:
            conn.execute("update job_nodes set status='running' where job_id=?", (job_id,))
            conn.execute("update jobs set status='running' where id=?", (job_id,))

        job = job_db.get_job(job_id)
        assert job is not None
        assert _wait_for(lambda: (job_db.get_job(job_id) or {})["status"] == "queued")
        node = job_db.get_job_node(job_id, NODE_KEY)
        assert node is not None
        assert node["status"] == "pending"
    finally:
        sweeper.stop()


def test_sweeper_step_failure_does_not_block_others(
    leases: ExecutorLeaseRepository,
    broker: RemoteExecutionBroker,
    job_db: JobQueries,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim(leases, job_db)
    _submit_remote(broker, claim)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(broker, "sweep_expired_claims", _boom)
    monkeypatch.setattr(leases, "expire_stale", _boom)

    sweeper = SweeperThread(leases, broker, interval_seconds=0.1, lease_ttl_seconds=60)
    expires_before = str(_lease_row(job_db, claim.lease_id)["expires_at"])
    # The startup sweep hits the failing steps; it must not raise from start().
    sweeper.start()
    try:
        # Renewal (and the loop itself) still work despite the failing steps.
        assert _wait_for(
            lambda: str(_lease_row(job_db, claim.lease_id)["expires_at"]) > expires_before
        )
    finally:
        sweeper.stop()


def test_concurrent_sweepers_no_double_finish(
    leases: ExecutorLeaseRepository, broker: RemoteExecutionBroker, job_db: JobQueries
) -> None:
    claim = _claim(leases, job_db)
    _force_expired(job_db, claim.lease_id)

    expired_returns: list[str] = []
    real_expire_stale = leases.expire_stale

    def spy_expire_stale(now: datetime) -> list[str]:
        expired = real_expire_stale(now)
        expired_returns.extend(expired)
        return expired

    leases.expire_stale = spy_expire_stale  # type: ignore[method-assign]

    # Two sweepers over the same repositories approximate a multi-process
    # deployment: the transactions must make expiry single-winner.
    sweepers = [
        SweeperThread(leases, broker, interval_seconds=0.05),
        SweeperThread(leases, broker, interval_seconds=0.05),
    ]
    for sweeper in sweepers:
        sweeper.start()
    try:
        assert _wait_for(lambda: _lease_row(job_db, claim.lease_id)["status"] == "expired")
        time.sleep(0.3)  # give the loser sweeper ticks a chance to double-process
    finally:
        for sweeper in sweepers:
            sweeper.stop()
    assert expired_returns.count(claim.lease_id) == 1


def test_sweeper_start_stop_idempotent(
    leases: ExecutorLeaseRepository, broker: RemoteExecutionBroker
) -> None:
    sweeper = SweeperThread(leases, broker, interval_seconds=0.1)
    sweeper.start()
    sweeper.stop()
    sweeper.stop()  # second stop must not raise
