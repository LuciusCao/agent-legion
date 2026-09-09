"""Unit tests for the executor-side relay sync: watchdog, state, sync round
(PR #572 P2-1 + the original #566 phase-2 sync semantics)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from worker.lease_snapshot import (
    RESULT_FILENAME,
    SNAPSHOT_FILENAME,
    read_snapshot,
    write_beat_result,
)
from worker.relay_sync import (
    RelayLivenessWatchdog,
    RelaySyncState,
    executor_relay_sync,
    relay_watchdog_threshold,
)

pytestmark = pytest.mark.no_db


def test_threshold_scales_with_relay_interval() -> None:
    assert relay_watchdog_threshold(15.0) == 60.0  # floor covers slow ticks
    assert relay_watchdog_threshold(30.0) == 90.0


def test_watchdog_warns_once_per_stall_episode() -> None:
    clock = [1000.0]
    logs: list[str] = []
    watchdog = RelayLivenessWatchdog(60.0, clock=lambda: clock[0])

    # Steady beats: no warning.
    watchdog.note(1, seq_changed=True, leases_pending=True, log=logs.append)
    clock[0] += 30
    watchdog.note(1, seq_changed=False, leases_pending=True, log=logs.append)
    assert logs == []

    # Stall beyond the threshold: one warning, then silence per episode.
    clock[0] += 61
    watchdog.note(1, seq_changed=False, leases_pending=True, log=logs.append)
    watchdog.note(1, seq_changed=False, leases_pending=True, log=logs.append)
    assert len(logs) == 1 and "relay" in logs[0]

    # Recovery re-arms; a second stall warns again.
    watchdog.note(2, seq_changed=True, leases_pending=True, log=logs.append)
    clock[0] += 61
    watchdog.note(2, seq_changed=False, leases_pending=True, log=logs.append)
    assert len(logs) == 2


def test_watchdog_silent_without_pending_leases() -> None:
    clock = [1000.0]
    logs: list[str] = []
    watchdog = RelayLivenessWatchdog(60.0, clock=lambda: clock[0])
    clock[0] += 3600
    watchdog.note(0, seq_changed=False, leases_pending=False, log=logs.append)
    assert logs == []


class _FakeEntry:
    def __init__(self, execution_id: str, lease_id: str) -> None:
        self.execution_id = execution_id
        self.lease_id = lease_id


class _FakeRegistry:
    def __init__(self) -> None:
        self.applied: list[tuple[list, list]] = []

    def snapshot(self) -> list[_FakeEntry]:
        return [_FakeEntry("exec-1", "lease-1")]

    def apply_beat_result(self, lost: list, cancelled: list) -> None:
        self.applied.append((lost, cancelled))


def test_relay_sync_writes_snapshot_and_applies_result_once(tmp_path: Path) -> None:
    snapshot_path = tmp_path / SNAPSHOT_FILENAME
    write_beat_result(
        tmp_path / RESULT_FILENAME, seq=7, lost=[("exec-1", "lease-1")], cancelled=["exec-9"]
    )
    registry = _FakeRegistry()
    state = RelaySyncState(15.0)

    executor_relay_sync(registry, snapshot_path, "w1", "t", state)

    assert state.last_seq == 7
    assert registry.applied == [([("exec-1", "lease-1")], ["exec-9"])]
    snapshot = read_snapshot(snapshot_path)
    assert snapshot is not None and snapshot["pid"] == os.getpid()
    assert snapshot["leases"] == [["exec-1", "lease-1"]]

    # Same seq is not re-applied.
    executor_relay_sync(registry, snapshot_path, "w1", "t", state)
    assert len(registry.applied) == 1


def test_relay_sync_throttles_and_survives_garbage(tmp_path: Path) -> None:
    registry = _FakeRegistry()
    state = RelaySyncState(15.0)
    # Result file with no seq → treated as absent; sync still writes snapshot.
    (tmp_path / RESULT_FILENAME).write_text("[]", encoding="utf-8")
    executor_relay_sync(registry, tmp_path / SNAPSHOT_FILENAME, "w1", "t", state)
    assert state.last_seq == -1
    assert registry.applied == []
    assert read_snapshot(tmp_path / SNAPSHOT_FILENAME) is not None

    # The throttle gates the next immediate call.
    (tmp_path / SNAPSHOT_FILENAME).unlink()
    executor_relay_sync(registry, tmp_path / SNAPSHOT_FILENAME, "w1", "t", state)
    assert not (tmp_path / SNAPSHOT_FILENAME).exists(), "second call inside the throttle window"
