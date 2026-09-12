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
        self.batch_timeouts: list[float | None] = []
        # Failure injection keyed on chunk CONTENT (first execution_id), not
        # on append order — shards fly in parallel threads, so len(batches)-1
        # is scheduling order and a loaded CI runner could mis-target the
        # scripted failure (PR #617 review: test determinism).
        self.failing_shards: set[str] = set()
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

    def heartbeat_batch(
        self, executions: list[tuple[str, str]], timeout: float | None = None
    ) -> Any:
        self.batches.append(list(executions))
        self.batch_timeouts.append(timeout)
        key = executions[0][0] if executions else ""
        if self.batch_error is not None and (not self.failing_shards or key in self.failing_shards):
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
    """PR #572 P2-4 + #591 止血：relay 按 RELAY_BEAT_SHARD（64，比 executor 侧
    256 更紧）并行分片——一次 Host HTTP 停摆只丢一个分片的拍；跨分片 lost
    判定仍合并进结果。"""
    from worker.relay_beats import RELAY_BEAT_SHARD

    client, logs = _FakeClient(), []
    leases = [(f"exec-{i}", f"lease-{i}") for i in range(RELAY_BEAT_SHARD + 1)]
    client.lost = [f"exec-{RELAY_BEAT_SHARD}"]  # the tail chunk's lease
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(tmp_path, leases=leases)

    relay.tick()

    assert [len(chunk) for chunk in client.batches] == [RELAY_BEAT_SHARD, 1]
    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None
    assert result["lost"] == [[f"exec-{RELAY_BEAT_SHARD}", f"lease-{RELAY_BEAT_SHARD}"]]
    # #591: every batch beat rides the tightened 10s deadline, not the 30s
    # client default — a stalled Host fails fast into the next tick.
    assert client.batch_timeouts == [10.0, 10.0]


def test_failing_shard_does_not_sink_later_shards(tmp_path: Path) -> None:
    """#591 codex: a failed shard loses only its own leases' tick — later
    shards still fly (no head-of-line starvation), the failed shard's leases
    are unknown-not-lost, and the round still reports what survived."""
    from worker.relay_beats import RELAY_BEAT_SHARD

    client, logs = _FakeClient(), []
    client.batch_error = ConnectionError("boom")
    client.failing_shards = {"exec-0"}  # the FIRST shard (content-keyed)
    client.lost = [f"exec-{RELAY_BEAT_SHARD}"]  # the SECOND shard's lease
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(
        tmp_path,
        leases=[(f"exec-{i}", f"lease-{i}") for i in range(2 * RELAY_BEAT_SHARD)],
    )

    relay.tick()

    # Both shards flew despite the first one failing.
    assert [len(chunk) for chunk in client.batches] == [RELAY_BEAT_SHARD, RELAY_BEAT_SHARD]
    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None
    # The failed shard's leases (exec-0..63) are unknown, NOT lost; the
    # surviving shard's lost verdict landed.
    assert result["lost"] == [[f"exec-{RELAY_BEAT_SHARD}", f"lease-{RELAY_BEAT_SHARD}"]]


def test_all_shards_failing_is_transient(tmp_path: Path) -> None:
    """Every shard failing reports (None, None) — the transient signal: the
    liveness result write still lands, verdicts stay empty, next tick
    retries everything (PR #572 P2-1 semantics under sharding)."""
    from worker.relay_beats import RELAY_BEAT_SHARD

    client, logs = _FakeClient(), []
    client.batch_error = ConnectionError("boom")
    # Content-keyed failure for every shard's first lease (exec-0 and
    # exec-64 head the two chunks of RELAY_BEAT_SHARD + 1 leases).
    client.failing_shards = {"exec-0", f"exec-{RELAY_BEAT_SHARD}"}
    relay = _relay(tmp_path, client, logs)
    _write_snapshot(
        tmp_path,
        leases=[(f"exec-{i}", f"lease-{i}") for i in range(RELAY_BEAT_SHARD + 1)],
    )

    relay.tick()

    assert len(client.batches) == 2, "every shard must still have flown"
    result = read_beat_result(tmp_path / RESULT_FILENAME)
    assert result is not None and result["seq"] == 1 and result["lost"] == []


def test_overstayed_shard_cannot_mutate_returned_verdicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #617 review P1-1: a shard daemon that overstays its join can
    still append to the merge lists — beat_sharded must return copies, so
    the late append never reaches the lists the caller already holds
    (write_beat_result iterates them; a racing append raised "list changed
    size during iteration" and stalled the seq advance)."""
    from worker import relay_shards
    from worker.relay_thread_limiter import ShardThreadLimiter

    # One lease per shard (the "slow" lease must head its OWN chunk to park
    # its own thread) and a join budget of milliseconds so the straggler
    # measurably overstays it.
    monkeypatch.setattr(relay_shards, "RELAY_BEAT_SHARD", 1)
    monkeypatch.setattr(relay_shards, "BATCH_BEAT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(relay_shards, "_JOIN_MARGIN_SECONDS", 0.1)

    release = threading.Event()

    class _OverstayingClient:
        """Fast shard answers at once; the slow shard parks until released,
        landing its verdict append only after beat_sharded has returned."""

        def heartbeat_batch(
            self, executions: list[tuple[str, str]], timeout: float | None = None
        ) -> tuple[int, dict[str, list[str]]]:
            if executions[0][0] == "exec-slow":
                release.wait(timeout=5)
                return 200, {"lost": ["exec-slow"], "cancelled_execution_ids": ["exec-x"]}
            return 200, {"lost": ["exec-fast"], "cancelled_execution_ids": ["exec-fast-c"]}

    client = _OverstayingClient()
    baseline = set(threading.enumerate())
    outcome = relay_shards.beat_sharded(
        client,
        [("exec-fast", "lease-fast"), ("exec-slow", "lease-slow")],
        lambda message: None,
        ShardThreadLimiter(),
    )

    assert outcome.verdicts is not None
    lost, cancelled = outcome.verdicts
    assert lost == [("exec-fast", "lease-fast")]
    assert cancelled == ["exec-fast-c"]

    # Let the overstayed shard land its append AFTER beat_sharded returned;
    # joining the straggler means its append has fully executed by the time
    # the assertions below run — no sleep-based hope.
    release.set()
    stragglers = [thread for thread in threading.enumerate() if thread not in baseline]
    for thread in stragglers:
        thread.join(timeout=5)
    assert lost == [("exec-fast", "lease-fast")], "late shard append leaked into returned verdicts"
    assert cancelled == ["exec-fast-c"], "late shard append leaked into returned verdicts"


def test_join_deadline_is_shared_across_shards(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR #617 review P1-2: the fan-out join spends ONE beat-timeout budget
    in total, not one per shard — a slow-drip Host that keeps every shard
    request alive past its own join must not serialise N × (timeout +
    margin) of tick wall time (the smaller twin of the #591 expiry stall)."""
    from worker import relay_shards
    from worker.relay_thread_limiter import ShardThreadLimiter

    monkeypatch.setattr(relay_shards, "RELAY_BEAT_SHARD", 1)  # every lease = its own shard
    monkeypatch.setattr(relay_shards, "BATCH_BEAT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(relay_shards, "_JOIN_MARGIN_SECONDS", 0.1)

    release = threading.Event()

    class _SlowDripClient:
        """Every shard parks past its own join budget — a slow-drip Host
        does exactly this (requests' timeout is per socket-read-op, so the
        wire never goes quiet long enough to trip it)."""

        def heartbeat_batch(
            self, executions: list[tuple[str, str]], timeout: float | None = None
        ) -> tuple[int, dict[str, list[str]]]:
            release.wait(timeout=5)
            return 200, {"lost": [], "cancelled_execution_ids": []}

    budget = 0.2 + 0.1  # BATCH_BEAT_TIMEOUT_SECONDS + _JOIN_MARGIN_SECONDS
    shards = 4  # a per-thread join would serialise 4 × budget = 1.2s
    client = _SlowDripClient()

    started = time.monotonic()
    outcome = relay_shards.beat_sharded(
        client,
        [(f"exec-{i}", f"lease-{i}") for i in range(shards)],
        lambda message: None,
        ShardThreadLimiter(),
    )
    elapsed = time.monotonic() - started
    release.set()  # let the abandoned daemon shards drain

    # 2× slack: a shared deadline lands at ~budget, per-thread stacking at
    # shards × budget — the bound sits squarely between the two regimes.
    assert elapsed < 2 * budget, (
        f"fan-out join spent {elapsed:.2f}s — per-thread budget stacking is back"
    )
    # The round still completed: nothing learned, verdicts intact.
    assert outcome.verdicts is not None and outcome.verdicts == ([], [])


def test_overstayed_shards_are_bounded_across_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR #621 Codex P1: slow-drip requests that outlive the join budget
    keep their slots, so later ticks cannot accumulate another thread wave."""
    from worker import relay_shards
    from worker.relay_thread_limiter import ShardThreadLimiter

    monkeypatch.setattr(relay_shards, "RELAY_BEAT_SHARD", 1)
    monkeypatch.setattr(relay_shards, "BATCH_BEAT_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(relay_shards, "_JOIN_MARGIN_SECONDS", 0.01)

    release = threading.Event()
    calls: list[str] = []
    calls_lock = threading.Lock()

    class _BlockedClient:
        def heartbeat_batch(
            self, executions: list[tuple[str, str]], timeout: float | None = None
        ) -> tuple[int, dict[str, list[str]]]:
            with calls_lock:
                calls.append(executions[0][0])
            release.wait(timeout=5)
            return 200, {"lost": [], "cancelled_execution_ids": []}

    limiter = ShardThreadLimiter(max_inflight=2)
    leases = [("exec-1", "lease-1"), ("exec-2", "lease-2")]
    baseline = set(threading.enumerate())
    try:
        first = relay_shards.beat_sharded(_BlockedClient(), leases, lambda message: None, limiter)
        second = relay_shards.beat_sharded(_BlockedClient(), leases, lambda message: None, limiter)
        assert first.verdicts == ([], [])
        assert second.verdicts is None
        assert calls == ["exec-1", "exec-2"], "a later tick spawned duplicate shard threads"
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread not in baseline:
                thread.join(timeout=5)

    recovered = relay_shards.beat_sharded(_BlockedClient(), leases, lambda message: None, limiter)
    assert recovered.verdicts == ([], [])
    assert calls == ["exec-1", "exec-2", "exec-1", "exec-2"]


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
