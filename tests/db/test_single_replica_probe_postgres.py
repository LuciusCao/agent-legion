"""Live-path regression tests for the single-replica probe (issue #433).

The mock suite (test_single_replica_probe.py) pins decision logic only;
nothing there can see connection transaction state, which is exactly how
the #433 bug slipped through: the probe's try-lock SELECT opened a
transaction on the held-forever pooled connection and was never committed,
so the connection sat in idle-in-transaction for the process lifetime,
pinning backend_xmin at the startup snapshot and freezing autovacuum out
of every table the app updates until restart.

These tests run the probe against the real per-worktree test database and
assert the connection-level invariants: transaction closed after probe(),
pg_stat_activity shows the backend idle (not idle in transaction), the
session lock still held (it must survive the commit — that is why the
session-level variant was chosen), lock released on close().

The lock key is monkeypatched to a per-process unique value: the stock key
is shared with every TestClient lifespan in the postgres tier, and an
xdist sibling running one of those concurrently would make acquisition
non-deterministic. Note the observer connection below must explicitly
unlock what it acquires: session locks survive the observer's rollback,
and a leftover would leak into the pool's next checkout.
"""

from __future__ import annotations

import os

import pytest

from server.app import single_replica_probe
from server.app.db.transaction import read_connection
from server.app.single_replica_probe import SingleReplicaProbe
from tests.postgres_support import TEST_DATABASE_URL


def test_probe_closes_transaction_and_keeps_session_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unique_key = f"issue-433-regression-{os.getpid()}"
    monkeypatch.setattr(single_replica_probe, "_REPLICA_LOCK_KEY", unique_key)
    probe = SingleReplicaProbe(TEST_DATABASE_URL)

    try:
        assert probe.probe() is True
        held = probe._connection
        assert held is not None
        # The #433 regression line: the held connection must not sit in an
        # open transaction (idle-in-transaction pins backend_xmin).
        assert held.in_transaction is False

        # Production symptom from the issue: pg_stat_activity must show the
        # probe's backend as idle, not idle in transaction.
        pid = held.execute("select pg_backend_pid() as pid").fetchone()["pid"]
        held.commit()  # this diagnostic SELECT opens a transaction; close it
        assert held.in_transaction is False

        with read_connection(TEST_DATABASE_URL) as observer:
            state = observer.execute(
                "select state from pg_stat_activity where pid = %s", (pid,)
            ).fetchone()["state"]
            assert state == "idle"
            # The session-level lock survives the commit: nobody else can
            # take it while the probe holds its connection.
            row = observer.execute(single_replica_probe._TRY_LOCK_SQL, (unique_key,)).fetchone()
            assert row["acquired"] is False
    finally:
        probe.close()

    # close() released the lock: a fresh connection can take it now.
    with read_connection(TEST_DATABASE_URL) as observer:
        row = observer.execute(single_replica_probe._TRY_LOCK_SQL, (unique_key,)).fetchone()
        assert row["acquired"] is True
        # Undo the acquisition so the pooled observer connection goes back
        # without a stray session lock.
        observer.execute(single_replica_probe._UNLOCK_SQL, (unique_key,))
