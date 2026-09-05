"""Unit tests for the per-Worker batch heartbeat (worker/execution/
heartbeat_batch.py, #352).

These drive the coordinator loop against a fake Host client: aggregation,
the degraded pre-v5-Host fallback to per-execution beats, batch-409 loss
propagation, the zombie (exited-unadopted) prune, quiesce/resume, and the
lease-TTL period clamp. No database: the client is an in-memory fake.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

from worker.execution.heartbeat_batch import (
    MAX_BATCH_HEARTBEATS,
    BatchHeartbeatRegistry,
    batch_heartbeat_loop,
    clamp_batch_interval,
)


class FakeBatchClient:
    """Fake Host with both heartbeat endpoints and switchable behaviors."""

    def __init__(
        self,
        *,
        batch_status: int = 200,
        lost: list[str] | None = None,
        cancelled: list[str] | None = None,
    ) -> None:
        self.batch_calls: list[list[tuple[str, str]]] = []
        self.single_calls: list[str] = []
        self.batch_status = batch_status
        self._lost = set(lost or [])
        self._cancelled = cancelled or []
        # Single-beat answers, keyed by execution id.
        self.single_status: dict[str, int] = {}

    def heartbeat_batch(
        self, executions: list[tuple[str, str]]
    ) -> tuple[int, dict[str, list[str]]] | None:
        self.batch_calls.append(list(executions))
        if self.batch_status in (404, 405):
            return None
        lost = [execution_id for execution_id, _ in executions if execution_id in self._lost]
        renewed = [execution_id for execution_id, _ in executions if execution_id not in lost]
        return (
            self.batch_status,
            {"renewed": renewed, "lost": lost, "cancelled_execution_ids": list(self._cancelled)},
        )

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        self.single_calls.append(execution_id)
        return self.single_status.get(execution_id, 204), []


def _register(
    registry: BatchHeartbeatRegistry, execution_id: str, *, lease_id: str | None = None
) -> threading.Event:
    ownership_lost = threading.Event()
    registry.register(execution_id, lease_id or f"lease-{execution_id}", ownership_lost)
    return ownership_lost


def _run_loop_once(
    client: FakeBatchClient,
    registry: BatchHeartbeatRegistry,
    stop: threading.Event,
    interval: float = 0.02,
    *,
    runtime: float = 0.1,
) -> None:
    """Run the loop in a thread for a bounded window, then stop and join it."""
    thread = threading.Thread(
        target=batch_heartbeat_loop, args=(client, registry, stop, interval), daemon=True
    )
    thread.start()
    time.sleep(runtime)
    stop.set()
    thread.join(timeout=2)


def test_batch_loop_aggregates_all_leases_into_one_request() -> None:
    registry = BatchHeartbeatRegistry()
    for index in range(3):
        _register(registry, f"exec-{index}")
    client = FakeBatchClient()
    stop = threading.Event()
    _run_loop_once(client, registry, stop)

    assert client.batch_calls, "no batch beat was sent"
    for call in client.batch_calls:
        assert sorted(execution_id for execution_id, _ in call) == ["exec-0", "exec-1", "exec-2"]
    assert client.single_calls == []


def test_batch_loop_survives_transient_batch_errors() -> None:
    registry = BatchHeartbeatRegistry()
    _register(registry, "exec-1")
    client = FakeBatchClient()

    def boom(executions: list[tuple[str, str]]) -> None:
        raise RuntimeError("host unreachable")

    client.heartbeat_batch = boom  # type: ignore[method-assign]
    stop = threading.Event()
    thread = threading.Thread(
        target=batch_heartbeat_loop, args=(client, registry, stop, 0.02), daemon=True
    )
    thread.start()
    time.sleep(0.1)
    # The loop must still be alive: one transient family cannot kill the
    # machine-wide heartbeat (the lease TTL is the real deadline).
    assert thread.is_alive()
    stop.set()
    thread.join(timeout=2)


def test_batch_loop_degrades_to_single_beats_on_pre_v5_host() -> None:
    """404/405 from the batch endpoint → per-execution beats from the same
    loop; the registry stays authoritative (prune keeps working)."""
    registry = BatchHeartbeatRegistry()
    _register(registry, "exec-1")
    _register(registry, "exec-2")
    client = FakeBatchClient(batch_status=404)
    stop = threading.Event()
    _run_loop_once(client, registry, stop)

    assert len(client.batch_calls) == 1, "the 404 must flip the loop permanently"
    assert set(client.single_calls) == {"exec-1", "exec-2"}
    assert registry.degraded_to_single is True

    # Prune still removes a lease from the degraded beats.
    registry.prune("exec-1")
    client.single_calls.clear()
    stop2 = threading.Event()
    _run_loop_once(client, registry, stop2, runtime=0.15)
    assert set(client.single_calls) == {"exec-2"}


def test_batch_loop_single_beat_409_fires_ownership_lost() -> None:
    registry = BatchHeartbeatRegistry()
    lost_event = _register(registry, "exec-1")
    kept_event = _register(registry, "exec-2")
    client = FakeBatchClient(batch_status=404)
    client.single_status = {"exec-1": 409, "exec-2": 204}
    stop = threading.Event()
    _run_loop_once(client, registry, stop)

    assert lost_event.is_set()
    assert not kept_event.is_set()
    # A lost lease stops being beaten in the degraded path too.
    registry.prune("exec-1")
    client.single_calls.clear()
    stop2 = threading.Event()
    _run_loop_once(client, registry, stop2, runtime=0.15)
    assert set(client.single_calls) == {"exec-2"}


def test_batch_lost_items_fire_ownership_lost_and_keep_renewing() -> None:
    registry = BatchHeartbeatRegistry()
    lost_event = _register(registry, "exec-lost")
    kept_event = _register(registry, "exec-kept")
    client = FakeBatchClient(lost=["exec-lost"])
    stop = threading.Event()
    _run_loop_once(client, registry, stop)

    assert lost_event.is_set()
    assert not kept_event.is_set()
    # The lost lease stops being batched; the sibling keeps renewing.
    assert all(
        "exec-lost" not in [execution_id for execution_id, _ in call]
        for call in client.batch_calls[1:]
    )
    assert any("exec-kept" in [e for e, _ in call] for call in client.batch_calls[1:])


def test_batch_loop_delivers_cancel_body_to_registered_callback() -> None:
    registry = BatchHeartbeatRegistry()
    seen: list[list[str]] = []
    registry.register(
        "exec-1", "lease-1", threading.Event(), on_cancelled=lambda ids: seen.append(ids)
    )
    client = FakeBatchClient(cancelled=["exec-1"])
    stop = threading.Event()
    _run_loop_once(client, registry, stop)

    assert seen and seen[0] == ["exec-1"]


def test_registry_quiesce_excludes_lease_and_resume_re_includes() -> None:
    registry = BatchHeartbeatRegistry()
    _register(registry, "exec-1")
    _register(registry, "exec-2")
    client = FakeBatchClient()
    stop = threading.Event()

    registry.quiesce("exec-1")
    thread = threading.Thread(
        target=batch_heartbeat_loop, args=(client, registry, stop, 0.02), daemon=True
    )
    thread.start()
    time.sleep(0.06)
    registry.resume("exec-1")
    time.sleep(0.06)
    stop.set()
    thread.join(timeout=2)

    assert client.batch_calls, "no beat while one lease was quiesced"
    assert any(
        "exec-1" in [execution_id for execution_id, _ in call] for call in client.batch_calls
    ), "quiesced lease never resumed"


def test_registry_prunes_zombie_entry_on_snapshot() -> None:
    """A dead, unadopted agent process must stop being batched — the Host's
    orphan sweeper has to be able to reclaim the lease."""
    registry = BatchHeartbeatRegistry()
    ownership_lost = threading.Event()
    registry.register("exec-1", "lease-1", ownership_lost)
    zombie = subprocess.Popen([sys.executable, "-c", "pass"])
    zombie.wait()
    registry._entries["exec-1"].proc_ref["proc"] = zombie  # type: ignore[reportPrivateUsage]

    assert registry.snapshot() == []
    assert "exec-1" not in registry._entries  # type: ignore[reportPrivateUsage]


def test_registry_keeps_beating_for_adopted_dead_process() -> None:
    registry = BatchHeartbeatRegistry()
    ownership_lost = threading.Event()
    registry.register("exec-1", "lease-1", ownership_lost)
    zombie = subprocess.Popen([sys.executable, "-c", "pass"])
    zombie.wait()
    entry = registry._entries["exec-1"]  # type: ignore[reportPrivateUsage]
    entry.proc_ref["proc"] = zombie
    entry.adopted.set()

    assert [item.execution_id for item in registry.snapshot()] == ["exec-1"]


def test_batch_over_limit_refuses_to_beat() -> None:
    """A bookkeeping blow-up (prunes stopped) must not silently renew a
    truncated prefix — the sweeper reclaims everything instead."""
    registry = BatchHeartbeatRegistry()
    for index in range(MAX_BATCH_HEARTBEATS + 1):
        _register(registry, f"exec-{index}")
    client = FakeBatchClient()

    from worker.execution.heartbeat_batch import _beat_batch

    assert _beat_batch(client, registry, registry.snapshot()) is True
    assert client.batch_calls == []


def test_clamp_batch_interval_keeps_ttl_margin() -> None:
    assert clamp_batch_interval(0) == 15.0
    assert clamp_batch_interval(-3) == 15.0
    assert clamp_batch_interval(15) == 15
    assert clamp_batch_interval(30) == 30
    # Above half the 90s lease TTL: clamped back to the #349 fleet baseline.
    assert clamp_batch_interval(60) == 30
    assert clamp_batch_interval(120) == 30
