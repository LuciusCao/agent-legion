"""Throttled last_seen_at writes behind AgentWorkerRegistry.authenticate
(issue #88): one write transaction per worker per 10s, monotonic clock."""

from __future__ import annotations

from datetime import datetime

from server.app.agent_workers import AgentWorkerRegistry
from tests.postgres_support import TEST_DATABASE_URL


def test_authenticate_throttles_last_seen_at_writes(job_db, monkeypatch) -> None:
    """last_seen_at feeds the 30s online threshold; authenticate() throttles
    the write to one per worker per 10s (monotonic clock)."""
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    token = registry.issue_token(
        worker_id="home-mini",
        name="Home Mac mini",
        runtimes=["pi"],
        max_concurrency=1,
    )
    clock = [1000.0]
    monkeypatch.setattr("server.app.agent_worker_liveness.monotonic", lambda: clock[0])

    def db_last_seen() -> datetime:
        with job_db.connect() as conn:
            row = conn.execute(
                "select last_seen_at from agent_workers where worker_id='home-mini'"
            ).fetchone()
        assert row["last_seen_at"] is not None
        return row["last_seen_at"]

    worker = registry.authenticate(token)
    assert worker is not None and worker["online"] is True
    first_write = db_last_seen()

    # Inside the throttle window the row is not rewritten, even though the
    # caller still reads online from the fresh response payload.
    with job_db.connect() as conn:
        conn.execute("update agent_workers set last_seen_at=current_timestamp - interval '1 hour'")
    clock[0] += 5.0
    worker = registry.authenticate(token)
    assert worker is not None and worker["online"] is True
    assert db_last_seen() < first_write

    # Once the interval has passed, the next authenticate writes again.
    clock[0] += 10.0
    assert registry.authenticate(token) is not None
    assert db_last_seen() > first_write

    # Failed authentication never touches the throttle memo or the row.
    registry._liveness._writes.clear()
    assert registry.authenticate("home-mini.wrong-secret") is None
    assert registry.authenticate("unknown-worker.nope") is None
    assert registry._liveness._writes == {}


def test_throttle_boundary_writes_at_exactly_the_interval(job_db, monkeypatch) -> None:
    """The throttle uses `<` against the interval: at exactly 10s the write
    must happen again (a `<=` regression would silently drop liveness)."""
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    token = registry.issue_token(
        worker_id="boundary-worker",
        name="Boundary",
        runtimes=["pi"],
        max_concurrency=1,
    )
    clock = [1000.0]
    monkeypatch.setattr("server.app.agent_worker_liveness.monotonic", lambda: clock[0])

    assert registry.authenticate(token) is not None
    assert registry._liveness._writes["boundary-worker"] == 1000.0

    clock[0] += 10.0  # exactly the interval
    assert registry.authenticate(token) is not None
    assert registry._liveness._writes["boundary-worker"] == 1010.0

    # Deleting the record evicts the memo entry so the dict stays bounded.
    assert registry.delete_worker("boundary-worker") == "deleted"
    assert "boundary-worker" not in registry._liveness._writes
