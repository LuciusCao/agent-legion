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

from worker.execution.heartbeat import start_lease_heartbeat
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
        self.single_timeouts: list[float | None] = []
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

    def heartbeat(
        self, execution_id: str, lease_id: str, timeout: float | None = None
    ) -> tuple[int, list[str]]:
        self.single_calls.append(execution_id)
        self.single_timeouts.append(timeout)
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
    registry.prune("exec-1", "lease-exec-1")
    client.single_calls.clear()
    stop2 = threading.Event()
    _run_loop_once(client, registry, stop2, runtime=0.15)
    assert set(client.single_calls) == {"exec-2"}


def test_degraded_single_beats_carry_short_timeout() -> None:
    """P2-1 review 钉子：降级路径的每条拍必须带短超时
    （SINGLE_BEAT_TIMEOUT_SECONDS）——慢 Host 的响应在传输层掐断，只丢这一
    拍这一个租约。"""
    from worker.host.heartbeat_ops import SINGLE_BEAT_TIMEOUT_SECONDS

    registry = BatchHeartbeatRegistry()
    for index in range(3):
        _register(registry, f"exec-{index}")
    client = FakeBatchClient(batch_status=404)
    stop = threading.Event()
    _run_loop_once(client, registry, stop)

    assert client.single_calls, "degraded mode never beat"
    assert client.single_timeouts == [SINGLE_BEAT_TIMEOUT_SECONDS] * len(client.single_calls)


def test_degraded_single_beats_do_not_serialize_on_slow_host() -> None:
    """#497 codex P1-2：降级拍不得串行——串行时一拍 5s 超时、20 条就是
    100s > 90s lease TTL，列表后部的健康执行在轮到续期前就被回收。修复后
    每条拍骑自己的短生命周期线程：每条都睡 0.3s 的「慢 Host」下，两拍的墙
    钟必须远小于串行和（并发重合）。"""
    registry = BatchHeartbeatRegistry()
    _register(registry, "exec-1")
    _register(registry, "exec-2")
    client = FakeBatchClient(batch_status=404)

    def slow_beat(execution_id: str, lease_id: str, timeout: float | None = None):
        time.sleep(0.3)
        return 204, []

    client.heartbeat = slow_beat  # type: ignore[method-assign]
    started = time.monotonic()
    from worker.execution.heartbeat_degraded import beat_single

    beat_single(client, registry.snapshot())
    # Both requests overlapped: serialized they would cost 0.6s+; concurrent
    # they cannot finish before the first (longest) 0.3s response does.
    assert time.monotonic() - started < 0.55, "degraded beats serialized on a slow Host"


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
    registry.prune("exec-1", "lease-exec-1")
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

    registry.quiesce("exec-1", "lease-exec-1")
    thread = threading.Thread(
        target=batch_heartbeat_loop, args=(client, registry, stop, 0.02), daemon=True
    )
    thread.start()
    time.sleep(0.06)
    registry.resume("exec-1", "lease-exec-1")
    time.sleep(0.06)
    stop.set()
    thread.join(timeout=2)

    assert client.batch_calls, "no beat while one lease was quiesced"
    assert any(
        "exec-1" in [execution_id for execution_id, _ in call] for call in client.batch_calls
    ), "quiesced lease never resumed"


# ---------------------------------------------------------------------------
# #497 codex P1-1: attempt-identity discipline — lease-scoped registry
# mutations match on (execution_id, lease_id), so an old attempt's late
# cleanup cannot drop or silence the entry of a re-claimed new attempt.


def test_old_attempt_prune_spares_reclaimed_entry() -> None:
    """Host 重排后被本 Worker 重新 claim 的 execution：旧 attempt 的
    shutdown（prune 只带旧 lease）不得删掉新 attempt 的 entry——否则新执行
    无心跳、租约到期被 Host 再回收。"""
    registry = BatchHeartbeatRegistry()
    _register(registry, "exec-1", lease_id="lease-old")
    # The Worker re-claims the requeued execution before the old attempt's
    # cleanup arrives: register overwrites the entry with the NEW lease.
    new_lost = _register(registry, "exec-1", lease_id="lease-new")

    registry.prune("exec-1", "lease-old")  # the old attempt's shutdown

    snapshot = registry.snapshot()
    assert [entry.lease_id for entry in snapshot] == ["lease-new"], (
        "the old attempt's prune dropped the new attempt's beats"
    )
    # The matching prune (the new attempt's own cleanup) still removes it.
    registry.prune("exec-1", "lease-new")
    assert registry.snapshot() == []
    assert not new_lost.is_set()


def test_old_attempt_quiesce_resume_and_adopt_spares_reclaimed_entry() -> None:
    """quiesce/resume/set_adopted 同样按 (execution_id, lease_id) 匹配：旧
    attempt 的静默/恢复/接管打到新 entry 上会把新执行静默到租约过期，或让
    新执行的死进程僵尸续命。"""
    registry = BatchHeartbeatRegistry()
    _register(registry, "exec-1", lease_id="lease-old")
    _register(registry, "exec-1", lease_id="lease-new")
    entry = registry._entries["exec-1"]  # type: ignore[reportPrivateUsage]

    registry.quiesce("exec-1", "lease-old")
    assert entry.quiesced is False, "the old attempt silenced the new entry's beats"
    registry.resume("exec-1", "lease-old")
    registry.set_adopted("exec-1", "lease-old")
    assert not entry.adopted.is_set(), "the old attempt adopted the new entry"

    # The new attempt's own calls still land.
    registry.quiesce("exec-1", "lease-new")
    assert entry.quiesced is True
    registry.resume("exec-1", "lease-new")
    assert entry.quiesced is False
    registry.set_adopted("exec-1", "lease-new")
    assert entry.adopted.is_set()


def test_facade_shutdown_prunes_only_own_lease() -> None:
    """经生产 facade（start_lease_heartbeat → ExecutionHeartbeat.shutdown）的
    旧 attempt 清理路径：Host 重排 + 重新 claim 后，旧 facade 的 shutdown 留
    新 entry 一条生路。"""
    registry = BatchHeartbeatRegistry()
    old = start_lease_heartbeat(
        None, "exec-1", "lease-old", 15.0, threading.Event(), registry=registry
    )
    start_lease_heartbeat(None, "exec-1", "lease-new", 15.0, threading.Event(), registry=registry)

    old.shutdown()

    assert [entry.lease_id for entry in registry.snapshot()] == ["lease-new"]
    assert old.stop.is_set()  # facade-level state still flips


def test_upload_prune_heartbeat_pair_matches_lease() -> None:
    """上传侧终点（prune_heartbeat）：task 的 (execution_id, lease_id) 是自己
    的 pair——registry 模式下旧 attempt 的收尾不得删新 entry，legacy 模式照旧
    停线程。"""
    from worker.upload.heartbeat import prune_heartbeat

    registry = BatchHeartbeatRegistry()
    registry.register("exec-1", "lease-new", threading.Event())

    # The old attempt's final stop (its own lease, entry already overwritten).
    prune_heartbeat(registry, threading.Event(), "exec-1", "lease-old")
    assert [entry.lease_id for entry in registry.snapshot()] == ["lease-new"]

    # The owning task's final stop removes its own entry.
    prune_heartbeat(registry, threading.Event(), "exec-1", "lease-new")
    assert registry.snapshot() == []

    # Legacy mode (no registry) keeps the thread-stop semantics.
    legacy_stop = threading.Event()
    prune_heartbeat(None, legacy_stop, "exec-1", "lease-old")
    assert legacy_stop.is_set()


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


def test_facade_wired_proc_ref_reaches_registry_zombie_stop() -> None:
    """P1 review 钉子：zombie 停跳必须经生产 facade 路径连通——run.py /
    code_runner.py 写 facade.proc_ref，registry.snapshot() 读的是同一个
    dict（start_lease_heartbeat 共享 entry.proc_ref/entry.adopted），不许
    再靠直写 _entries 让测试自证。"""
    registry = BatchHeartbeatRegistry()
    ownership_lost = threading.Event()
    facade = start_lease_heartbeat(
        None, "exec-1", "lease-1", 15.0, ownership_lost, registry=registry
    )
    zombie = subprocess.Popen([sys.executable, "-c", "pass"])
    zombie.wait()
    # Executor writes the agent process exactly like run.py does.
    facade.proc_ref["proc"] = zombie

    assert registry.snapshot() == [], "zombie kept being batched via the facade wiring"
    assert facade.stop.is_set() is False  # facade-level state untouched; registry pruned


def test_facade_adopt_keeps_dead_process_beating_via_registry() -> None:
    """经 facade 的 adopt()（上传接管）后，死进程的租约必须继续被批量拍
    ——adopt 转发到 registry entry 的同一 adopted 事件。"""
    registry = BatchHeartbeatRegistry()
    ownership_lost = threading.Event()
    facade = start_lease_heartbeat(
        None, "exec-1", "lease-1", 15.0, ownership_lost, registry=registry
    )
    zombie = subprocess.Popen([sys.executable, "-c", "pass"])
    zombie.wait()
    facade.proc_ref["proc"] = zombie
    facade.adopt()

    assert [item.execution_id for item in registry.snapshot()] == ["exec-1"]


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


def test_batch_over_limit_shards_into_chunks() -> None:
    """P2-2 review 钉子：合法高槽位 Worker（>256 租约）不得触发全租约回收
    悬崖——超限快照按 MAX_BATCH_HEARTBEATS 分片成多个请求，每片完整续
    期，一个不漏。"""
    registry = BatchHeartbeatRegistry()
    total = MAX_BATCH_HEARTBEATS * 2 + 3
    for index in range(total):
        _register(registry, f"exec-{index}")
    client = FakeBatchClient()

    from worker.execution.heartbeat_batch import _beat_batch

    assert _beat_batch(client, registry, registry.snapshot()) is True

    assert len(client.batch_calls) == 3
    assert [len(call) for call in client.batch_calls] == [
        MAX_BATCH_HEARTBEATS,
        MAX_BATCH_HEARTBEATS,
        3,
    ]
    renewed_ids = {execution_id for call in client.batch_calls for execution_id, _ in call}
    assert len(renewed_ids) == total, "a chunk boundary lost a lease"


def test_batch_chunk_error_keeps_next_chunk_trying_next_tick() -> None:
    """分片下一片失败中止本拍：后续片跳过、不留半续期状态，下一拍从
    头全量重来（Host 侧逐项谓词幂等，已续期前缀重复续期无副作用）。"""
    registry = BatchHeartbeatRegistry()
    for index in range(MAX_BATCH_HEARTBEATS + 1):
        _register(registry, f"exec-{index}")
    client = FakeBatchClient()
    impl = client.heartbeat_batch
    calls = 0

    def flaky(executions: list[tuple[str, str]]) -> tuple[int, dict[str, list[str]]] | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("host unreachable mid-batch")
        return impl(executions)

    client.heartbeat_batch = flaky  # type: ignore[method-assign]

    from worker.execution.heartbeat_batch import _beat_batch

    entries = registry.snapshot()
    # Transient family: the tick is over (True — NOT a degrade verdict), the
    # later chunks are skipped, and the next tick retries from the top.
    assert _beat_batch(client, registry, entries) is True
    assert registry.degraded_to_single is False, "a network blip must not flip the mode"
    assert calls == 1, "later chunks should be skipped after a chunk error"
    assert len(client.batch_calls) == 0, "the failing chunk logged no successful request"

    # Next tick retries from the top: both chunks go out again.
    client.heartbeat_batch = impl  # type: ignore[method-assign]
    assert _beat_batch(client, registry, entries) is True
    assert len(client.batch_calls) == 2, "the retry tick must resend chunk 1 then chunk 2"
    assert {execution_id for call in client.batch_calls for execution_id, _ in call} >= {
        entry.execution_id for entry in entries
    }


def test_batch_transient_error_does_not_degrade_the_loop() -> None:
    """transient 与 404 的语义钉子：只有「批量端点不存在」（404/405）才把
    循环永久降级为逐执行心跳；传输错误/5xx 只丢这一拍，批量模式必须保
    持——否则一次网络闪断就永久失去 Worker 仍在用的批量端点。"""
    registry = BatchHeartbeatRegistry()
    _register(registry, "exec-1")
    client = FakeBatchClient()
    state = {"failing": True}
    impl = client.heartbeat_batch

    def flaky(executions: list[tuple[str, str]]) -> tuple[int, dict[str, list[str]]] | None:
        if state["failing"]:
            raise RuntimeError("connection reset")
        return impl(executions)

    client.heartbeat_batch = flaky  # type: ignore[method-assign]
    stop = threading.Event()
    _run_loop_once(client, registry, stop)

    # Degraded flag untouched while every request failed; nothing fell back
    # to single beats.
    assert registry.degraded_to_single is False
    assert client.single_calls == []

    # The Host recovers: the very next tick is batch traffic again.
    state["failing"] = False
    client.batch_calls.clear()
    stop2 = threading.Event()
    _run_loop_once(client, registry, stop2)
    assert client.batch_calls, "batch mode was silently lost after a transient error"


def test_clamp_batch_interval_keeps_ttl_margin() -> None:
    assert clamp_batch_interval(0) == 15.0
    assert clamp_batch_interval(-3) == 15.0
    assert clamp_batch_interval(15) == 15
    assert clamp_batch_interval(30) == 30
    # Above half the 90s lease TTL: clamped back to the #349 fleet baseline.
    assert clamp_batch_interval(60) == 30
    assert clamp_batch_interval(120) == 30


def test_apply_beat_result_pair_matches_lost_and_dedups_cancelled() -> None:
    """#566 phase 2: the relay write-back path applies lost verdicts
    pair-matched (a re-claimed execution's NEW lease survives an old lease's
    lost verdict) and fans cancelled out once per distinct callback."""
    registry = BatchHeartbeatRegistry()
    # exec-1 was re-claimed: the registry holds the NEW lease; the relay's
    # lost verdict names the OLD lease.
    new_lease_event = _register(registry, "exec-1", lease_id="lease-new")
    gone_event = _register(registry, "exec-2", lease_id="lease-2")
    cancelled_calls: list[list[str]] = []

    def on_cancelled(execution_ids: list[str]) -> None:
        cancelled_calls.append(list(execution_ids))

    entry = registry.register("exec-3", "lease-3", threading.Event(), on_cancelled=on_cancelled)
    assert entry is not None

    registry.apply_beat_result(
        lost=[("exec-1", "lease-old"), ("exec-2", "lease-2")],
        cancelled=["exec-9"],
    )

    assert not new_lease_event.is_set(), "old lease's lost verdict must not touch the new attempt"
    assert gone_event.is_set()
    assert cancelled_calls == [["exec-9"]]
