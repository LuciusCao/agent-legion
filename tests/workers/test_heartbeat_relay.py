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
        self.pings = 0
        self.ping_error: Exception | None = None

    def get_self(self) -> dict:
        self.pings += 1
        if self.ping_error is not None:
            raise self.ping_error
        return {"worker_id": "w1"}

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
    # PR #572 P2-1: every beat-stage tick rewrites the result file (empty
    # verdicts included) — the advancing seq is the relay's liveness proof.
    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None and result["seq"] == 1
    assert result["lost"] == [] and result["cancelled"] == []
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
    assert len([line for line in logs if "租约停拍" in line]) == 1


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
    # Transient failure: the liveness write still lands (empty verdicts).
    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None and result["seq"] == 1 and result["lost"] == []
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
    assert relay._beater._client is None

    # A fresh snapshot carries the rotated token; the relay rebuilds.
    client.batch_error = None
    _write_snapshot(tmp_path, token="rotated-token")
    relay.tick()
    assert relay._beater._client is client
    assert client.batches


def test_404_degrades_to_single_beats(tmp_path: Path) -> None:
    client, logs = _FakeClient(), []
    client.batch_status = 404
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)

    relay.tick()

    assert relay._beater.degraded is True
    assert sorted(client.singles) == [("exec-1", "lease-1"), ("exec-2", "lease-2")]
    assert any("降级" in line for line in logs)
    # Degraded singles collect lost (401/409) into the result file.
    client.single_status = 409
    relay.tick()
    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None
    assert result["lost"] == [["exec-1", "lease-1"], ["exec-2", "lease-2"]]


def test_tick_shards_oversized_snapshot_and_merges_chunk_verdicts(tmp_path: Path) -> None:
    """PR #572 P2-4: 257 leases shard into 256+1; a lost verdict from the
    second chunk still lands in the merged result."""
    from worker.execution.heartbeat_batch import MAX_BATCH_HEARTBEATS

    client, logs = _FakeClient(), []
    leases = [(f"exec-{i}", f"lease-{i}") for i in range(MAX_BATCH_HEARTBEATS + 1)]
    client.lost = [f"exec-{MAX_BATCH_HEARTBEATS}"]  # the tail chunk's lease
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path, leases=leases)

    relay.tick()

    assert [len(chunk) for chunk in client.batches] == [MAX_BATCH_HEARTBEATS, 1]
    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None
    assert result["lost"] == [[f"exec-{MAX_BATCH_HEARTBEATS}", f"lease-{MAX_BATCH_HEARTBEATS}"]]


def test_first_chunk_transient_aborts_tick_but_keeps_liveness(tmp_path: Path) -> None:
    """A failing first chunk skips the tail chunk (next tick retries from the
    top), and the liveness result write still lands (PR #572 P2-1)."""
    from worker.execution.heartbeat_batch import MAX_BATCH_HEARTBEATS

    client, logs = _FakeClient(), []
    client.batch_error = ConnectionError("boom")
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(
        tmp_path,
        leases=[(f"exec-{i}", f"lease-{i}") for i in range(MAX_BATCH_HEARTBEATS + 1)],
    )

    relay.tick()

    assert len(client.batches) == 1, "the tail chunk must not follow a failed first chunk"
    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None and result["seq"] == 1 and result["lost"] == []


def test_stale_recovery_rearms_the_stall_log(tmp_path: Path) -> None:
    """PR #572 P2-4: the stall log fires once per episode — a recovered
    executor that stalls AGAIN logs again."""
    client, logs = _FakeClient(), []
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)

    import json

    def backdate() -> None:
        path = tmp_path / SNAPSHOT_FILENAME
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["updated_at"] = time.time() - 3600
        path.write_text(json.dumps(payload), encoding="utf-8")

    backdate()
    relay.tick()
    _write_snapshot(tmp_path)  # executor recovers
    relay.tick()
    backdate()
    relay.tick()  # stalls again

    assert len([line for line in logs if "租约停拍" in line]) == 2


def test_degraded_mode_resets_on_executor_generation_change(tmp_path: Path) -> None:
    """PR #572 P2-3: a new executor pid re-probes the batch endpoint."""
    client, logs = _FakeClient(), []
    client.batch_status = 404
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)
    relay.tick()
    assert relay._beater.degraded is True
    singles_before = len(client.singles)

    # Executor restarted: same leases under a new pid; Host now has v5.
    client.batch_status = 200
    _write_snapshot(tmp_path, pid=1)  # pid 1 always exists
    relay.tick()

    assert relay._beater.degraded is False
    assert len(client.batches) == 2, "the new generation must re-probe the batch endpoint"
    assert len(client.singles) == singles_before


def _backdate_snapshot(tmp_path: Path) -> None:
    import json

    path = tmp_path / SNAPSHOT_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["updated_at"] = time.time() - 3600
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_stale_snapshot_stops_renewal_but_pings_control_plane(tmp_path: Path) -> None:
    """PR #572 codex P1: 停拍=放弃租约，ping=自证存活——快照过期后租约
    不再续期（Host 2×TTL 硬兜底回收），但控制面 ping 每拍继续。"""
    client, logs = _FakeClient(), []
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)
    _backdate_snapshot(tmp_path)

    relay.tick()
    relay.tick()

    # No lease renewal of any kind (batch or single)…
    assert client.batches == [] and client.singles == []
    # …but the control plane stays warm: one ping per tick…
    assert client.pings == 2
    # …and the relay liveness seq still advances (the executor watchdog keys
    # on it; an intentional stall must not trip it).
    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None and result["seq"] == 2 and result["lost"] == []
    assert any("控制面 ping 继续" in line for line in logs)


def test_control_plane_ping_401_drops_cached_client(tmp_path: Path) -> None:
    """Ping 的 401 语义与发拍路径一致：丢缓存 client，等快照带新 token。"""
    client, logs = _FakeClient(), []
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path)
    _backdate_snapshot(tmp_path)
    client.ping_error = RuntimeError("HTTP 401: unauthorized")

    relay.tick()

    assert relay._beater._client is None
    assert any("控制面 ping 失败" in line for line in logs)
    # Recovery: next tick rebuilds and the error note re-arms silently.
    client.ping_error = None
    relay.tick()
    assert relay._beater._client is client
    assert client.pings == 2


def _relay_threads() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == "lease-heartbeat-relay" and thread.is_alive()
    ]


def test_service_lifespan_owns_relay_thread_lifecycle(tmp_path: Path) -> None:
    """PR #572 P2: the relay thread starts with the service lifespan, is
    joined on shutdown (no accumulation, no post-close sink writes), and a
    new lifespan builds a fresh thread."""
    from fastapi.testclient import TestClient

    from worker.config_store import WorkerConfigStore
    from worker.service import create_app
    from worker.supervisor import WorkerSupervisor

    store = WorkerConfigStore(tmp_path / "state")
    supervisor = WorkerSupervisor(store, tmp_path / "executor.py")
    app = create_app(supervisor, tmp_path)

    baseline = len(_relay_threads())
    with TestClient(app):
        assert len(_relay_threads()) == baseline + 1
    deadline = time.monotonic() + 5
    while len(_relay_threads()) > baseline and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(_relay_threads()) == baseline, "relay thread survived service shutdown"

    with TestClient(app):  # a second lifecycle builds a fresh thread
        assert len(_relay_threads()) == baseline + 1
    deadline = time.monotonic() + 5
    while len(_relay_threads()) > baseline and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(_relay_threads()) == baseline
