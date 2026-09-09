"""Unit tests for the executor↔supervisor lease snapshot IPC (#566 phase 2)."""

from __future__ import annotations

import json
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
