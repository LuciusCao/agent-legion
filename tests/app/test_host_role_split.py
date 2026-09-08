"""Host role split (#521 方案 B): role parsing and factory dispatch.

Unit tests for the env parsing and the create_app guards; the plane
composition (threads on/off, reset gating, sampling ownership) is pinned
by tests/app/test_main.py, and the live PostgreSQL round-trip (NOTIFY
bridge + per-plane probe locks) by tests/db/test_role_split_postgres.py.
"""

from __future__ import annotations

import pytest

from server.app.configuration.host_role import (
    ROLE_COMBINED,
    ROLE_ENV,
    ROLE_HTTP,
    ROLE_SCHEDULER,
    host_role_from_env,
)

pytestmark = pytest.mark.no_db


def test_default_role_is_combined(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ROLE_ENV, raising=False)
    assert host_role_from_env() == ROLE_COMBINED


def test_role_parsing_normalizes_case_and_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ROLE_ENV, "  HTTP ")
    assert host_role_from_env() == ROLE_HTTP


def test_role_parsing_accepts_all_valid_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for role in (ROLE_COMBINED, ROLE_HTTP, ROLE_SCHEDULER):
        monkeypatch.setenv(ROLE_ENV, role)
        assert host_role_from_env() == role


def test_invalid_role_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ROLE_ENV, "api")
    with pytest.raises(ValueError, match=ROLE_ENV):
        host_role_from_env()


def test_create_app_rejects_scheduler_role() -> None:
    from server.app.main import create_app

    with pytest.raises(RuntimeError, match="scheduler_process"):
        create_app(role=ROLE_SCHEDULER)


def test_probe_rejects_unknown_lock_name() -> None:
    from server.app.single_replica_probe import SingleReplicaProbe

    with pytest.raises(ValueError, match="lock name"):
        SingleReplicaProbe("postgresql://127.0.0.1:5432/x", lock_name="bogus")


def test_probe_lock_keys_are_disjoint_per_plane() -> None:
    from server.app.single_replica_probe import SingleReplicaProbe

    http_probe = SingleReplicaProbe("postgresql://127.0.0.1:5432/x", lock_name="control-plane-http")
    scheduler_probe = SingleReplicaProbe("postgresql://127.0.0.1:5432/x", lock_name="scheduler")
    assert http_probe._lock_key != scheduler_probe._lock_key


class TestSchedulerStartFailureGate:
    """codex P1 on #536: a failed workflow worker must exit the scheduler
    process non-zero (supervisor retries) instead of a "ready" deployment
    that schedules nothing; a failed sweeper only degrades."""

    def test_fatal_path_exits_nonzero_with_teardown(self) -> None:
        from pathlib import Path

        source = Path("server/app/scheduler_process.py").read_text(encoding="utf-8")
        # The fatal branch: workflow worker failed → teardown + return 3.
        fatal_block = source.split('worker_status.get("workflow_worker") == "failed"')[1][:400]
        assert "return 3" in fatal_block
        assert "sweeper_thread.stop()" in fatal_block
        assert "replica_probe.close()" in fatal_block
        assert "close_database_pools()" in fatal_block
        # The sweeper failure stays non-fatal (warning only, no return).
        sweeper_block = source.split('worker_status.get("sweeper") == "failed"')[1][:250]
        assert "return" not in sweeper_block
        assert "logger.warning" in sweeper_block
