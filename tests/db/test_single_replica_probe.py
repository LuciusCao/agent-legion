"""Single-replica probe (issue #277): advisory-lock conflict detection.

The probe is exercised with a fake connection object: the real advisory
lock path runs in every postgres-tier TestClient lifespan already (the
probe fires on app startup); these tests pin the decision logic —
warning vs acknowledged, skip env, unlock-on-close, no connection leak
when the probe fails.
"""

from __future__ import annotations

import logging

import pytest

from server.app import single_replica_probe
from server.app.single_replica_probe import SingleReplicaProbe

pytestmark = pytest.mark.no_db

_DSN = "postgresql://127.0.0.1:5432/agent_legion_probe_test"


class FakeResult:
    def __init__(self, acquired: bool) -> None:
        self._acquired = acquired

    def fetchone(self) -> dict[str, bool]:
        return {"acquired": self._acquired}


class FakeConnection:
    """Records SQL and mimics the two statements the probe issues."""

    def __init__(self, *, acquired: bool = True, fail: bool = False) -> None:
        self.acquired = acquired
        self.fail = fail
        self.executed: list[str] = []
        self.closed = False

    def execute(self, sql: str, params: object = None) -> FakeResult:
        self.executed.append(sql)
        if self.fail:
            raise RuntimeError("database unavailable")
        if "pg_try_advisory_lock" in sql:
            return FakeResult(self.acquired)
        return FakeResult(True)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_connect(monkeypatch: pytest.MonkeyPatch):
    """Patch connect_database in the probe module; yields a probe factory."""

    def install(*, acquired: bool = True, fail: bool = False):
        fake = FakeConnection(acquired=acquired, fail=fail)
        monkeypatch.setattr(single_replica_probe, "connect_database", lambda dsn: fake)
        return fake

    return install


def test_probe_acquires_lock_and_holds_connection(fake_connect) -> None:
    fake = fake_connect(acquired=True)
    probe = SingleReplicaProbe(_DSN)

    assert probe.probe() is True
    assert probe.lock_acquired is True
    # The connection must stay checked out for the probe's lifetime.
    assert fake.closed is False
    assert "pg_try_advisory_lock" in fake.executed[0]

    probe.close()
    assert fake.closed is True
    assert "pg_advisory_unlock" in fake.executed[-1]


def test_probe_conflict_logs_warning(fake_connect, caplog: pytest.LogCaptureFixture) -> None:
    fake = fake_connect(acquired=False)
    probe = SingleReplicaProbe(_DSN)

    with caplog.at_level(logging.WARNING, logger="server.app.single_replica_probe"):
        assert probe.probe() is False
    assert probe.lock_acquired is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "another Host replica" in message
    assert "AGENT_LEGION_ALLOW_MULTI_REPLICA=1" in message
    # Conflict or not, the connection stays held (the loser also probes).
    assert fake.closed is False
    probe.close()


def test_probe_conflict_with_escape_hatch_logs_info(
    fake_connect, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_connect(acquired=False)
    monkeypatch.setenv("AGENT_LEGION_ALLOW_MULTI_REPLICA", "1")
    probe = SingleReplicaProbe(_DSN)

    with caplog.at_level(logging.INFO, logger="server.app.single_replica_probe"):
        assert probe.probe() is False
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert any(r.levelno == logging.INFO for r in caplog.records)


def test_probe_skip_env_takes_no_connection(fake_connect, monkeypatch: pytest.MonkeyPatch) -> None:
    called = fake_connect()
    monkeypatch.setenv("AGENT_LEGION_SKIP_SINGLE_REPLICA_PROBE", "1")
    probe = SingleReplicaProbe(_DSN)

    assert probe.probe() is True
    assert probe.lock_acquired is None
    assert called.executed == []  # no checkout, no SQL
    probe.close()  # no-op, must not raise


def test_probe_failure_never_blocks_startup(fake_connect, caplog: pytest.LogCaptureFixture) -> None:
    fake = fake_connect(fail=True)
    probe = SingleReplicaProbe(_DSN)

    with caplog.at_level(logging.DEBUG, logger="server.app.single_replica_probe"):
        assert probe.probe() is True  # degraded to "sole replica"
    assert probe.lock_acquired is None
    # The failed checkout is released instead of leaked.
    assert fake.closed is True


def test_close_without_probe_is_a_noop() -> None:
    probe = SingleReplicaProbe(_DSN)
    probe.close()  # must not raise


def test_close_releases_lock_only_when_held(fake_connect) -> None:
    fake = fake_connect(acquired=False)
    probe = SingleReplicaProbe(_DSN)
    probe.probe()

    probe.close()
    # The loser never took the lock, so close must not attempt an unlock.
    assert not any("pg_advisory_unlock" in sql for sql in fake.executed)


def test_lock_key_is_scoped_to_database() -> None:
    """The SQL must hash the key together with current_database() so two
    worktree instances against two databases on one cluster do not collide."""
    assert "current_database()" in single_replica_probe._TRY_LOCK_SQL
    assert "current_database()" in single_replica_probe._UNLOCK_SQL
