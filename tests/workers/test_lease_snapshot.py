"""Unit tests for the executor↔supervisor lease snapshot IPC (#566 phase 2)."""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from worker.execution.heartbeat_batch import BatchHeartbeatRegistry
from worker.lease_snapshot import (
    RESULT_FILENAME,
    SNAPSHOT_ENV_VAR,
    SNAPSHOT_FILENAME,
    SNAPSHOT_STALE_SECONDS,
    executor_relay_sync,
    open_lease_channel,
    read_beat_result,
    read_snapshot,
    snapshot_stale,
    write_beat_result,
    write_snapshot,
)

pytestmark = pytest.mark.no_db


def test_snapshot_round_trip_is_atomic_and_private(tmp_path: Path) -> None:
    path = tmp_path / SNAPSHOT_FILENAME
    write_snapshot(
        path,
        worker_id="w1",
        token="secret-token",
        pid=1234,
        leases=[("exec-1", "lease-1"), ("exec-2", "lease-2")],
        now=1000.0,
    )
    snapshot = read_snapshot(path)
    assert snapshot is not None
    assert snapshot["worker_id"] == "w1"
    assert snapshot["token"] == "secret-token"
    assert snapshot["pid"] == 1234
    assert snapshot["updated_at"] == 1000.0
    assert snapshot["leases"] == [["exec-1", "lease-1"], ["exec-2", "lease-2"]]
    # The snapshot carries the worker token: owner-only like the register
    # tokens already on disk in the same state dir.
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not snapshot_stale(
        snapshot, now=1000.0 + SNAPSHOT_STALE_SECONDS, stale_seconds=SNAPSHOT_STALE_SECONDS
    )
    assert snapshot_stale(
        snapshot, now=1000.0 + SNAPSHOT_STALE_SECONDS + 1, stale_seconds=SNAPSHOT_STALE_SECONDS
    )


def test_read_snapshot_tolerates_missing_and_corrupt(tmp_path: Path) -> None:
    assert read_snapshot(tmp_path / "missing.json") is None
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert read_snapshot(corrupt) is None
    shapeless = tmp_path / "shapeless.json"
    shapeless.write_text(json.dumps({"leases": "nope"}), encoding="utf-8")
    assert read_snapshot(shapeless) is None


def test_snapshot_without_updated_at_reads_as_stale(tmp_path: Path) -> None:
    assert snapshot_stale({"leases": []}, now=1.0, stale_seconds=60.0) is True


def test_beat_result_round_trip_and_seq_validation(tmp_path: Path) -> None:
    path = tmp_path / RESULT_FILENAME
    write_beat_result(path, seq=3, lost=[("exec-1", "lease-1")], cancelled=["exec-2"])
    result = read_beat_result(path)
    assert result is not None
    assert result["seq"] == 3
    assert result["lost"] == [["exec-1", "lease-1"]]
    assert result["cancelled"] == ["exec-2"]
    path.write_text(json.dumps({"seq": "not-an-int"}), encoding="utf-8")
    assert read_beat_result(path) is None


def test_open_lease_channel_switches_on_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv(SNAPSHOT_ENV_VAR, raising=False)

    class _NoBeatClient:
        pass

    registry, path = open_lease_channel(_NoBeatClient(), 60.0, threading.Event())
    # Bare executor (no env): the in-process beat loop owns the registry.
    assert isinstance(registry, BatchHeartbeatRegistry)
    assert registry is not None and path is None

    monkeypatch.setenv(SNAPSHOT_ENV_VAR, str(tmp_path / SNAPSHOT_FILENAME))
    registry, path = open_lease_channel(_NoBeatClient(), 60.0, threading.Event())
    assert isinstance(registry, BatchHeartbeatRegistry)
    assert path == tmp_path / SNAPSHOT_FILENAME
    # Snapshot mode starts no beat thread: nothing renews in-process.
    assert registry.degraded_to_single is False


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


def test_executor_relay_sync_writes_snapshot_and_applies_result_once(tmp_path: Path) -> None:
    snapshot_path = tmp_path / SNAPSHOT_FILENAME
    write_beat_result(
        tmp_path / RESULT_FILENAME, seq=7, lost=[("exec-1", "lease-1")], cancelled=["exec-9"]
    )
    registry = _FakeRegistry()

    seq = executor_relay_sync(
        registry, snapshot_path, worker_id="w1", token="t", last_result_seq=-1
    )

    assert seq == 7
    assert registry.applied == [([("exec-1", "lease-1")], ["exec-9"])]
    snapshot = read_snapshot(snapshot_path)
    assert snapshot is not None and snapshot["pid"] == os.getpid()
    assert snapshot["leases"] == [["exec-1", "lease-1"]]

    # Same seq is not re-applied.
    again = executor_relay_sync(
        registry, snapshot_path, worker_id="w1", token="t", last_result_seq=seq
    )
    assert again == 7
    assert len(registry.applied) == 1


def test_executor_relay_sync_survives_garbage(tmp_path: Path) -> None:
    registry = _FakeRegistry()
    # Result file with no seq → treated as absent; sync still writes snapshot.
    (tmp_path / RESULT_FILENAME).write_text("[]", encoding="utf-8")
    seq = executor_relay_sync(
        registry, tmp_path / SNAPSHOT_FILENAME, worker_id="w1", token="t", last_result_seq=-1
    )
    assert seq == -1
    assert registry.applied == []
    assert read_snapshot(tmp_path / SNAPSHOT_FILENAME) is not None
