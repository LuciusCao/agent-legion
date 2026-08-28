"""Generic two-lane scheduler with a dynamic in-flight concurrency limit.

Split out of ``queue.py`` so the queue module stays within its size
budget. Two pending lanes share one thread pool: the priority lane is drained
strictly before the bulk lane, and the two lanes together never exceed the
current limit in flight. Raising the limit backfills immediately; lowering it
lets in-flight work drain without preemption. The pool is sized at
``MAX_DYNAMIC_CONCURRENCY`` but creates threads lazily, so the real thread
count never exceeds the historical peak limit.

Priority inflow must be bounded — here reports are produced 1:1 by finished
bulk work. Generic reuse with unbounded priority inflow risks starving the
bulk lane under strict priority.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor


class LaneScheduler:
    """Pending 双 deque + in_flight 计数 + 动态 limit 的 pump 调度器。"""

    def __init__(self, max_workers: int, limit: int, *, thread_name_prefix: str) -> None:
        if limit < 1:
            raise ValueError("limit 必须是正整数")
        self._pool = ThreadPoolExecutor(max_workers, thread_name_prefix=thread_name_prefix)
        self._idle = threading.Condition()
        self._bulk: deque[Callable[[], None]] = deque()
        self._priority: deque[Callable[[], None]] = deque()
        self._in_flight = 0
        self._limit = limit
        self._shutdown = False

    @property
    def in_flight(self) -> int:
        with self._idle:
            return self._in_flight

    @property
    def pending(self) -> int:
        """两 lane 合计的排队任务数（测试观测用）。"""
        with self._idle:
            return len(self._bulk) + len(self._priority)

    def set_limit(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit 必须是正整数")
        with self._idle:
            self._limit = limit
            self._pump_locked()

    def submit(self, fn: Callable[[], None], *, priority: bool = False) -> None:
        with self._idle:
            if self._shutdown:
                raise RuntimeError("scheduler is shut down")
            (self._priority if priority else self._bulk).append(fn)
            self._pump_locked()

    def shutdown(self) -> None:
        # 排空语义与 ThreadPoolExecutor.shutdown(wait=True) 一致，但要等
        # 在途任务把后续工作（report 交接）也排进来：先等 pending/in_flight
        # 归零再关池——关池后 pump 的 pool.submit 会抛 RuntimeError。
        with self._idle:
            while self._in_flight or self._bulk or self._priority:
                self._idle.wait()
            self._shutdown = True
        self._pool.shutdown(wait=True)

    def _pump_locked(self) -> None:
        while self._in_flight < self._limit:
            if self._priority:
                lane, fn = self._priority, self._priority.popleft()
            elif self._bulk:
                lane, fn = self._bulk, self._bulk.popleft()
            else:
                return
            self._in_flight += 1
            try:
                self._pool.submit(self._run, fn)
            except RuntimeError:
                # 池已关闭（关停竞态）：in_flight 回退、任务放回原 lane
                # 队首，不丢任务。
                self._in_flight -= 1
                lane.appendleft(fn)
                return

    def _run(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        finally:
            with self._idle:
                self._in_flight -= 1
                self._pump_locked()
                self._idle.notify_all()
