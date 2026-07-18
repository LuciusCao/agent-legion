from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from server.app.executors import _lease_write_paths
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from tests.executors.leases.helpers import _claim_request, _setup_workspace


def test_try_claim_retries_transient_lock_error(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, monkeypatch
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "ws-retry", "local-default", workspace_limit=2)
    real_claim_lease = _lease_write_paths.claim_lease
    calls = {"count": 0}

    def flaky_claim_lease(conn, request, data_dir=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_claim_lease(conn, request, data_dir)

    monkeypatch.setattr(_lease_write_paths, "claim_lease", flaky_claim_lease)

    claim = repo_a.try_claim(_claim_request(workspace_id, job_id))

    assert claim is not None
    assert calls["count"] == 2


def test_expire_stale_retries_transient_lock_error(
    repo_a: ExecutorLeaseRepository, monkeypatch
) -> None:
    real_expire_stale_leases = _lease_write_paths.expire_stale_leases
    calls = {"count": 0}

    def flaky_expire_stale_leases(conn, now):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("database table is locked")
        return real_expire_stale_leases(conn, now)

    monkeypatch.setattr(_lease_write_paths, "expire_stale_leases", flaky_expire_stale_leases)

    expired = repo_a.expire_stale(datetime.now(UTC))

    assert expired == []
    assert calls["count"] == 2
