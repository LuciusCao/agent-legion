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
    queue; exit after one idle-timeout round.

    Spawn policy ledger: ``_busy`` counts taken-but-unfinished tasks. A
    spawn is warranted exactly when demand (busy + queued + this submit)
    exceeds the live threads — parked threads absorb work, busy threads
    will pick from the queue on completion, and only genuine excess demand
    grows the pool (codex P2, PR #648: submit must not grow the pool past
    live executions — sequential submit/wait/submit must reuse threads)."""

    def __init__(self, pool: ExecutionLanePool, serial: int) -> None:
        super().__init__(daemon=True, name=f"agent-execution-{serial:d}")
        self._pool = pool

    def _retire(self) -> None:
        with self._pool._guard:
            self._pool._live.discard(self)

    def _rebalance_after_take(self) -> None:
        """Mark busy, then the consumer-side spawn belt: a racing submit's
        demand check can miss a needed spawn in the get()→here window (this
        thread's take is not yet in ``_busy``), so the next take re-checks
        with the fresh ledger. The stall ceiling of a miss is one
        park-to-take handoff (microseconds), not an execution's runtime."""
        with self._pool._guard:
            self._pool._busy += 1
            # Post-shutdown spawn is forbidden: shutdown already queued its
            # sentinels per the then-live count, and a fresh thread would
            # park on the queue with NO sentinel to consume — shutdown's
            # join then waits out the full idle timeout for it.
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
                # Idle death: deregister first so shutdown's join-set can
                # never contain a dead thread, then exit.
                self._retire()
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
        # Serial/name assignment and the live-set insert share one critical
        # section; worker.start() stays OUTSIDE the lock (the new thread's
        # first idle-death deregister must not wait on our guard — and the
        # constructor must never take the same non-reentrant lock we hold).
        with self._guard:
            self._serial += 1
            worker = _LaneWorker(self, self._serial)
            self._live.add(worker)
        worker.start()

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
        with self._guard:
            if self._shutdown:
                raise RuntimeError("cannot submit to a shutdown ExecutionLanePool")
            demand = self._busy + self._queue.qsize() + 1
            spawn = demand > len(self._live) and len(self._live) < self._max_workers
        if spawn:
            self._spawn()
        future: Future = Future()
        self._queue.put((future, fn, args))
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
            live = len(self._live)
        if not wait:
            return
        if first:
            for _ in range(live):
                self._queue.put(None)
            # Threads spawned between the flag and now (raced submits fail
            # the flag check, so this cannot happen — but a sentinel count
            # below live would park shutdown; loop the join with the idle
            # ceiling regardless, as the belt).
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
