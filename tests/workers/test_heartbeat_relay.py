"""Unit tests for the supervisor-side lease heartbeat relay (#566 phase 2)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from worker.heartbeat_relay import HeartbeatRelay
from worker.lease_snapshot import (
    RESULT_FILENAME,
    SNAPSHOT_FILENAME,
    read_beat_result,
    write_snapshot,
)

pytestmark = pytest.mark.no_db


class _FakeClient:
    """Records batch beats; per-test hooks script failures/degradation."""

    def __init__(self) -> None:
        self.batches: list[list[tuple[str, str]]] = []
        self.singles: list[tuple[str, str]] = []
        self.batch_error: Exception | None = None
        self.batch_status: int = 200
        self.lost: list[str] = []
        self.cancelled: list[str] = []
        self.single_status = 204

    def heartbeat_batch(self, executions: list[tuple[str, str]]) -> Any:
        self.batches.append(list(executions))
        if self.batch_error is not None:
            raise self.batch_error
        if self.batch_status in (404, 405):
            return None
        return 200, {"lost": self.lost, "cancelled_execution_ids": self.cancelled}

    def heartbeat(self, execution_id: str, lease_id: str, timeout: float | None = None) -> Any:
        self.singles.append((execution_id, lease_id))
        cancelled = self.cancelled if self.single_status == 200 else []
        return self.single_status, cancelled


def _relay(
    tmp_path: Path, client: _FakeClient, logs: list[str], **overrides: Any
) -> HeartbeatRelay:
    kwargs: dict[str, Any] = {
        "state_dir": tmp_path,
        "get_config": lambda: {"host_url": "http://host", "heartbeat_interval_seconds": 15},
        "stop": threading.Event(),
        "log": logs.append,
        "client_factory": lambda host, token: client,
    }
    kwargs.update(overrides)
    return HeartbeatRelay(**kwargs)


def _write_snapshot(tmp_path: Path, **overrides: Any) -> None:
    payload: dict[str, Any] = {
        "worker_id": "w1",
        "token": "worker-token",
        "pid": os.getpid(),
        "leases": [("exec-1", "lease-1"), ("exec-2", "lease-2")],
    }
    payload.update(overrides)
    write_snapshot(
        tmp_path / SNAPSHOT_FILENAME,
        worker_id=payload["worker_id"],
        token=payload["token"],
        pid=payload["pid"],
        leases=payload["leases"],
    )


def test_tick_beats_snapshot_leases(tmp_path: Path) -> None:
    client, logs = _FakeClient(), []
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)

    relay.tick()

    assert client.batches == [[("exec-1", "lease-1"), ("exec-2", "lease-2")]]
    # No lost/cancelled verdicts → no result file (nothing for the executor).
    assert read_beat_result(tmp_path / RESULT_FILENAME) is None
    assert logs == []


def test_tick_writes_beat_result_with_lost_pairs_and_cancelled(tmp_path: Path) -> None:
    client, logs = _FakeClient(), []
    client.lost = ["exec-2"]
    client.cancelled = ["exec-9"]
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)

    relay.tick()

    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None
    assert result["seq"] == 1
    # lost echoes the (execution_id, lease_id) pair so the executor's
    # pair-matched apply cannot hit a re-claimed execution's new lease.
    assert result["lost"] == [["exec-2", "lease-2"]]
    assert result["cancelled"] == ["exec-9"]


def test_tick_skips_stale_snapshot_and_logs_once(tmp_path: Path) -> None:
    client, logs = _FakeClient(), []
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)
    stale_snapshot = tmp_path / SNAPSHOT_FILENAME
    # Backdate beyond the staleness bound without touching the rest.
    import json

    payload = json.loads(stale_snapshot.read_text(encoding="utf-8"))
    payload["updated_at"] = time.time() - 3600
    stale_snapshot.write_text(json.dumps(payload), encoding="utf-8")

    relay.tick()
    relay.tick()

    assert client.batches == []
    assert len([line for line in logs if "暂停心跳 relay" in line]) == 1


def test_tick_skips_dead_executor_pid(tmp_path: Path) -> None:
    client, logs = _FakeClient(), []
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path, pid=2**22 + 12345)  # no such process

    relay.tick()

    assert client.batches == []


def test_tick_skips_empty_or_tokenless_snapshot(tmp_path: Path) -> None:
    client, logs = _FakeClient(), []
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path, leases=[])
    relay.tick()
    _write_snapshot(tmp_path, token="")
    relay.tick()

    assert client.batches == []


def test_transient_batch_error_keeps_client_and_retries(tmp_path: Path) -> None:
    client, logs = _FakeClient(), []
    client.batch_error = ConnectionError("host unreachable")
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)

    relay.tick()
    assert read_beat_result(tmp_path / RESULT_FILENAME) is None
    assert logs and "批量拍失败" in logs[0]

    client.batch_error = None
    relay.tick()
    assert len(client.batches) == 2


def test_401_drops_cached_client_for_token_rotation(tmp_path: Path) -> None:
    client, logs = _FakeClient(), []
    client.batch_error = RuntimeError("batch heartbeat failed: HTTP 401: b'unauthorized'")
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)

    relay.tick()
    assert relay._client is None

    # A fresh snapshot carries the rotated token; the relay rebuilds.
    client.batch_error = None
    _write_snapshot(tmp_path, token="rotated-token")
    relay.tick()
    assert relay._client is client
    assert client.batches


def test_404_degrades_to_single_beats(tmp_path: Path) -> None:
    client, logs = _FakeClient(), []
    client.batch_status = 404
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)

    relay.tick()

    assert relay._degraded is True
    assert sorted(client.singles) == [("exec-1", "lease-1"), ("exec-2", "lease-2")]
    assert any("降级" in line for line in logs)
    # Degraded singles collect lost (401/409) into the result file.
    client.single_status = 409
    relay.tick()
    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None
    assert result["lost"] == [["exec-1", "lease-1"], ["exec-2", "lease-2"]]
