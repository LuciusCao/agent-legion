"""Regression tests for lease-expiry races under READ COMMITTED.

``expire_stale_leases`` selects stale rows and updates them in separate
statements. These tests replay the harmful interleavings deterministically:
conn1 runs the stale SELECT, a concurrent finish/heartbeat commits on another
connection, then conn1's guarded per-row update must skip the row instead of
clobbering it to 'expired'.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.db.transaction import read_connection, write_transaction
from server.app.executors._lease_lifecycle import _expire_lease_row
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult
from server.app.jobs import JobQueries
from tests.executors.leases.helpers import _claim_request, _setup_workspace
from tests.postgres_support import TEST_DATABASE_URL

_STALE_SELECT = """
    select id, job_id, node_key, node_run_id, execution_id
    from executor_leases
    where status='active' and expires_at<=%s
"""


def _claimed_stale_lease(job_db: JobQueries, repo: ExecutorLeaseRepository, name: str):
    workspace_id, job_id = _setup_workspace(job_db, name, "pi-default", 2, local_limit=None)
    claim = repo.try_claim(
        _claim_request(workspace_id, job_id, executor_id="pi-default", local_node_limit=None)
    )
    assert claim is not None
    past = database_timestamp(datetime.now(UTC) - timedelta(seconds=10))
    with write_transaction(job_db.path) as conn:
        conn.execute(
            "update executor_leases set expires_at=%s where id=%s",
            (past, claim.lease_id),
        )
    return workspace_id, job_id, claim


def _lease_status(job_db: JobQueries, lease_id: str) -> str:
    with read_connection(job_db.path) as conn:
        row = conn.execute("select status from executor_leases where id=%s", (lease_id,)).fetchone()
    assert row is not None
    return str(row["status"])


def test_expire_skips_lease_finished_concurrently(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    repo = ExecutorLeaseRepository(job_db.path, job_db=job_db, data_dir=tmp_path)
    _, job_id, claim = _claimed_stale_lease(job_db, repo, "ws-expire-finish")
    now_str = database_timestamp(datetime.now(UTC))

    with write_transaction(job_db.path) as conn1:
        stale = conn1.execute(_STALE_SELECT, (now_str,)).fetchall()
        assert [str(row["id"]) for row in stale] == [claim.lease_id]
        # A concurrent finish commits between the SELECT and the UPDATE.
        assert repo.finish(claim.lease_id, ExecutionResult(status="completed", exit_code=0))
        assert _expire_lease_row(conn1, stale[0], now_str) is False

    assert _lease_status(job_db, claim.lease_id) == "released"
    node = job_db.get_job_node(job_id, "review_keywords")
    assert node is not None and node["status"] == "completed"
    job = job_db.get_job(job_id)
    assert job is not None and job["status"] == "completed"


def test_expire_skips_lease_renewed_concurrently(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    repo = ExecutorLeaseRepository(job_db.path, job_db=job_db, data_dir=tmp_path)
    _, job_id, claim = _claimed_stale_lease(job_db, repo, "ws-expire-renew")
    now_str = database_timestamp(datetime.now(UTC))

    with write_transaction(job_db.path) as conn1:
        stale = conn1.execute(_STALE_SELECT, (now_str,)).fetchall()
        assert [str(row["id"]) for row in stale] == [claim.lease_id]
        # A concurrent heartbeat renews the lease past the expiry cutoff.
        assert repo.heartbeat(claim.lease_id, 3600) is True
        assert _expire_lease_row(conn1, stale[0], now_str) is False

    assert _lease_status(job_db, claim.lease_id) == "active"
    node = job_db.get_job_node(job_id, "review_keywords")
    assert node is not None and node["status"] == "running"


def test_expire_still_expires_genuinely_stale_lease(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    repo = ExecutorLeaseRepository(job_db.path, job_db=job_db, data_dir=tmp_path)
    _, job_id, claim = _claimed_stale_lease(job_db, repo, "ws-expire-real")

    assert repo.expire_stale(datetime.now(UTC)) == [claim.lease_id]
    assert _lease_status(job_db, claim.lease_id) == "expired"
    node = job_db.get_job_node(job_id, "review_keywords")
    assert node is not None and node["status"] == "failed"
    assert node["error_message"] == "lease expired"
    job = job_db.get_job(job_id)
    assert job is not None and job["status"] == "failed"
