"""Idle-dying execution lane pool (#647 phase 3 — the pool-semantics half).

Why not ``ThreadPoolExecutor``: its threads NEVER shrink — a fleet that once
peaked at 1600 submissions keeps ~1024 idle workers forever (#647 measured
1177 never-shrinking idle workers on top of the 793 wait loops). Phase 2
(``exit_watch.py``) removed the per-execution wakeup tax; this module removes
the residency tax: threads spawn on demand up to ``max_workers`` and die
after ``idle_timeout`` without work, so the pool tracks live executions
instead of the historical peak.

Deliberately NOT a constant core-count pool: ``run_execution`` is
straight-line synchronous code whose wait phases park on events; each live
execution still holds a thread while parked. Sizing the lane below
``max_concurrency`` would clamp local concurrency (the 800-slot fleet needs
800 parkable threads in flight) — the constant-~50-threads end state needs
the queue-driven continuation rewrite of ``run_execution`` (tracked as the
phase-3 remainder in ``exit_watch.py``'s docstring) and is out of scope here.

Contract: ``submit`` / ``shutdown`` are the only entry points (the executor
loop and ``claim_batch`` use nothing else); futures are plain
``concurrent.futures.Future`` objects so ``done()``/``result()``/exception
propagation behave identically to a ThreadPoolExecutor. A worker thread that
dies unexpectedly (task raised OUTSIDE the run — a bare ``BaseException``
like KeyboardInterrupt escaping the containment boundary) is replaced on the
next submit: submitted-but-unpicked tasks are picked up by whichever thread
takes them, never dropped.
"""

from __future__ import annotations

import contextlib
import os
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future, InvalidStateError
from typing import Any, cast

# Idle threads exit after this long without work (seconds). Generous: the
# claim loop's backoff/pacing windows are tens of seconds, and a fleet-wide
# burst ending must not churn threads for the next burst. Env override
# AGENT_WORKER_LANE_IDLE_TIMEOUT supports ops tuning without a redeploy.
_DEFAULT_IDLE_TIMEOUT = 30.0


def _lane_idle_timeout() -> float:
    raw = os.environ.get("AGENT_WORKER_LANE_IDLE_TIMEOUT")
    if not raw:
        return _DEFAULT_IDLE_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        print(
            f"AGENT_WORKER_LANE_IDLE_TIMEOUT={raw} 非法（须为秒数），"
            f"使用默认 {_DEFAULT_IDLE_TIMEOUT}s",
            flush=True,
        )
        return _DEFAULT_IDLE_TIMEOUT
    return value if value > 0 else _DEFAULT_IDLE_TIMEOUT


class _LaneWorker(threading.Thread):
    """One pool thread: loop taking (future, fn, args) triples off the shared
    queue; exit after one idle-timeout round. Spawn policy ledger (codex P2,
    PR #648): ``_busy`` counts taken-but-unfinished tasks; spawn only when
    demand (busy + queued + submit) exceeds live — sequential submit/wait
    must reuse threads, not climb to max_workers."""

    def __init__(self, pool: ExecutionLanePool, serial: int) -> None:
        super().__init__(daemon=True, name=f"agent-execution-{serial:d}")
        self._pool = pool

    def _retire(self) -> None:
        """Unconditional retirement (the shutdown-sentinel exit)."""
        with self._pool._guard:
            self._pool._live.discard(self)

    def _retire_if_idle(self) -> bool:
        """Idle-death retirement with the handoff re-check (codex P1 round
        2): a submit may queue a task in the get→lock window while this
        thread still counts as live — retiring anyway strands that task
        with no consumer (max_workers=1: the Worker stops claiming forever).
        Queue non-empty inside the retirement critical section → abort
        retirement (False; the next get() returns immediately); else retire."""
        with self._pool._guard:
            if self._pool._queue.qsize() > 0:
                return False
            self._pool._live.discard(self)
            return True

    def _rebalance_after_take(self) -> None:
        """Mark busy, then the consumer-side spawn belt: a racing submit's
        demand check can miss a needed spawn in the get()→here window (the
        take is not yet in ``_busy``), so the next take re-checks with the
        fresh ledger — a miss stalls one park-to-take handoff, not an
        execution's runtime. Post-shutdown spawn is forbidden (no sentinel
        would ever reach it)."""
        with self._pool._guard:
            self._pool._busy += 1
            undersupplied = (
                not self._pool._shutdown
                and self._pool._busy + self._pool._queue.qsize() > len(self._pool._live)
                and len(self._pool._live) < self._pool._max_workers
            )
        if undersupplied:
            self._pool._spawn()

    def run(self) -> None:
        while True:
            try:
                item = self._pool._queue.get(timeout=self._pool._idle_timeout)
            except queue.Empty:
                # Idle death — but only after the handoff re-check: a task
                # queued during the get→lock window must not be stranded.
                if not self._retire_if_idle():
                    continue
                return
            if item is None:
                # Shutdown sentinel: wake immediately instead of idling out
                # (executor teardown must not wait out the idle timeout).
                self._retire()
                return
            future, fn, args = item
            # Claim the future before running (ThreadPoolExecutor semantics):
            # without set_running_or_notify_cancel, Future.cancel() succeeds
            # mid-run and the set_result/set_exception below raises
            # InvalidStateError OUTSIDE the task containment — killing this
            # lane thread, leaking _busy, and ghosting _live (subagent review
            # P1). False = already cancelled: nothing to report, skip the
            # task. The whole take→finish region sits in one try/finally so
            # ANY escape (a BaseException from the claim, thread kill) still
            # releases the busy slot and retires the ghost thread instead of
            # hanging shutdown(wait=True) in a join spin.
            if not future.set_running_or_notify_cancel():
                continue
            try:
                self._rebalance_after_take()
                result = cast("Callable[..., Any]", fn)(*args)
            except BaseException as exc:
                # #204 broad-except audit: lane 线程的存活语义——同
                # ThreadPoolExecutor 的 worker：任何任务异常（含 BaseException
                # 族，解释器关闭期偶发）只进 Future（executor 的 reap 打印
                # traceback、租约过期后 Host 重调度兜底），不得杀死 lane 线程
                # 缩小池子。吞是对的：任务体的遏制边界（run_execution 的宽
                # 捕获）已拦下常规失败，能到这里的是 deliver 收尾逃逸族；
                # 线程死了后续 claim 就少一个执行位。日志保全：异常对象经
                # Future 交给 reap 打印，不静默丢弃。
                # The executor's reap treats a failed future exactly like a
                # ThreadPoolExecutor one (traceback + Host requeue on lease
                # expiry); the lane thread itself stays alive.
                # Cancel raced the finish: InvalidStateError would escape the
                # task containment and kill this thread (subagent review P1).
                with contextlib.suppress(InvalidStateError):
                    future.set_exception(exc)
            else:
                with contextlib.suppress(InvalidStateError):
                    future.set_result(result)
            finally:
                # Task done (or the containment itself failed): the busy
                # slot releases on every path out of the region, so no
                # escape can leak the ledger and starve later spawns.
                with self._pool._guard:
                    self._pool._busy = max(0, self._pool._busy - 1)


class ExecutionLanePool:
    """Submit/shutdown-compatible idle-dying thread pool."""

    def __init__(self, max_workers: int, idle_timeout: float | None = None) -> None:
        self._max_workers = max(1, int(max_workers))
        self._idle_timeout = (
            _lane_idle_timeout() if idle_timeout is None else max(0.01, float(idle_timeout))
        )
        # Queue items: (future, fn, args) tasks and None shutdown sentinels.
        self._queue: queue.Queue[tuple[Future, Any, tuple] | None] = queue.Queue()
        self._guard = threading.Lock()
        self._live: set[_LaneWorker] = set()
        # Tasks taken off the queue but not finished (see _LaneWorker's
        # ledger note). Invariant: _busy <= len(_live).
        self._busy = 0
        self._serial = 0
        self._shutdown = False

    def live_threads(self) -> int:
        """Current live lane threads (observability: the slots heartbeat line
        prints it — the idle-dying count tracks in-flight executions instead
        of the historical peak)."""
        with self._guard:
            return len(self._live)

    def _spawn(self) -> None:
        """Spawn entry for callers NOT holding ``_guard`` (the rebalance
        belt). Callers holding the guard use ``_spawn_locked`` directly."""
        with self._guard:
            self._spawn_locked()

    def _spawn_locked(self) -> None:
        """Guard-held spawn — the ATOMIC ARBITER of both the shutdown flag
        and the max_workers cap: spawn decisions are made from lock-side
        snapshots (submit under the lock, the rebalance belt from a
        snapshot it releases before calling), and concurrent deciders at
        live == max-1 would each insert a thread past the cap without this
        re-check (codex P2 round 2; the shutdown re-check likewise closes
        the decision→insertion gap — subagent review P2).

        ``worker.start()`` runs under the guard by design: start() itself
        never blocks on it, and the new thread's first guard acquisition
        (a task take, or its first idle retirement ≥ idle_timeout away)
        only ever waits out microseconds of the caller's remainder — no
        deadlock, unlike the constructor path. A start() failure (thread
        budget exhausted) rolls the live-set entry back: a never-started
        thread would ghost in ``_live`` forever (nothing joins it),
        permanently inflating live_threads() and squatting a max_workers
        slot (self-review round 3)."""
        if self._shutdown or len(self._live) >= self._max_workers:
            return
        self._serial += 1
        worker = _LaneWorker(self, self._serial)
        self._live.add(worker)
        try:
            worker.start()
        except BaseException:
            # #204 broad-except audit: spawn 回滚臂（自审 round 3）。逃逸族
            # 是 Thread.start 的资源类 RuntimeError/线程系统异常；吞不是
            # 目的——先回滚 _live 条目再原样 re-raise（submit 的调用方拿
            # 原始异常，任务 future 尚未入队因此无人悬挂）。不回滚则
            # 未启动的 ghost 线程永久虚高 live 计数。日志保全：异常向上
            # 传播，不在此处打印。
            self._live.discard(worker)
            raise

    def submit(self, fn, *args):  # type: ignore[no-untyped-def]
        """Queue one task; spawn a thread only when no idle thread can take
        it and the pool is below max_workers (codex P2, PR #648). Mirrors
        ThreadPoolExecutor.submit's Future contract, including the
        pre-shutdown RuntimeError.

        The demand check is ``busy + queued + this submit > live threads``:
        every live thread either is running a task (and takes the next
        queued one on completion) or is parked on the queue — either way it
        will serve the demand, so the pool only grows when demand exceeds
        ALL live threads, capped at max_workers (beyond which the task
        queues — the ThreadPoolExecutor semantics). Checking idle slots
        instead has a churn flaw: a submit racing the previous task's
        slot-return sees idle=0 and spawns a thread that then parks for the
        full idle timeout — steady sequential traffic kept spawning strays
        (codex P2 round 2)."""
        future: Future = Future()
        with self._guard:
            if self._shutdown:
                raise RuntimeError("cannot submit to a shutdown ExecutionLanePool")
            demand = self._busy + self._queue.qsize() + 1
            # The put stays INSIDE the critical section: releasing between
            # the flag check and the put lets shutdown's sentinels queue
            # first, stranding this task in a dead queue while shutdown
            # (wait=True) has already returned (subagent review P1;
            # ThreadPoolExecutor holds _shutdown_lock across both).
            self._queue.put((future, fn, args))
            if demand > len(self._live) and len(self._live) < self._max_workers:
                self._spawn_locked()
        return future

    def shutdown(self, wait: bool = True) -> None:
        """Stop accepting work. ``wait=True`` queues one None sentinel per
        live thread (each wakes immediately, finishes any task already in
        its hands, and exits — queued-but-unpicked work still runs first,
        same as a ThreadPoolExecutor with no cancel_futures) and joins them
        through the idle timeout as the ceiling."""
        with self._guard:
            first = not self._shutdown
            self._shutdown = True
            # Sentinels queue inside the SAME critical section that set the
            # flag (subagent review P1): submit's put is also inside its
            # section, so a task and the sentinels cannot interleave
            # (task-after-sentinel would strand it; sentinel-before-spawn
            # is prevented by _spawn's flag re-check).
            live = len(self._live)
            if first:
                for _ in range(live):
                    self._queue.put(None)
        if not wait:
            return
        while True:
            with self._guard:
                workers = list(self._live)
            if not workers:
                return
            for worker in workers:
                worker.join(timeout=self._idle_timeout + 5.0)
            with self._guard:
                if not self._live:
                    return
