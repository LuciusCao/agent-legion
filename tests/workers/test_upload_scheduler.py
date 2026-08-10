"""Unit tests for the two-lane dynamic-concurrency scheduler (worker/upload_scheduler.py)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from worker.upload_scheduler import LaneScheduler

pytestmark = pytest.mark.no_db


class Recorder:
    """线程安全的执行顺序与并发峰值记录器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.order: list[str] = []
        self.active = 0
        self.peak = 0

    def start(self, name: str) -> None:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.order.append(name)

    def finish(self) -> None:
        with self._lock:
            self.active -= 1


def _parking(
    rec: Recorder, name: str, entered: threading.Event, release: threading.Event
) -> Callable[[], None]:
    def run() -> None:
        rec.start(name)
        entered.set()
        assert release.wait(10)
        rec.finish()

    return run


def _quick(rec: Recorder, name: str, done: threading.Event) -> Callable[[], None]:
    def run() -> None:
        rec.start(name)
        rec.finish()
        done.set()

    return run


def test_priority_lane_drains_before_queued_bulk() -> None:
    scheduler = LaneScheduler(4, 1, thread_name_prefix="test-lane")
    rec = Recorder()
    entered, release = threading.Event(), threading.Event()
    scheduler.submit(_parking(rec, "bulk-1", entered, release))
    assert entered.wait(10)
    scheduler.submit(_quick(rec, "bulk-2", threading.Event()))
    scheduler.submit(_quick(rec, "bulk-3", threading.Event()))
    scheduler.submit(_quick(rec, "report-1", threading.Event()), priority=True)
    scheduler.submit(_quick(rec, "report-2", threading.Event()), priority=True)

    release.set()
    scheduler.shutdown()

    assert rec.order == ["bulk-1", "report-1", "report-2", "bulk-2", "bulk-3"]


def test_raise_limit_backfills_pending_immediately() -> None:
    scheduler = LaneScheduler(4, 1, thread_name_prefix="test-lane")
    rec = Recorder()
    entered, release = threading.Event(), threading.Event()
    scheduler.submit(_parking(rec, "bulk-1", entered, release))
    assert entered.wait(10)
    done = [threading.Event(), threading.Event()]
    scheduler.submit(_quick(rec, "bulk-2", done[0]))
    scheduler.submit(_quick(rec, "bulk-3", done[1]))
    assert scheduler.pending == 2
    assert not done[0].wait(0.2)  # limit=1：排队任务不会被泵入

    scheduler.set_limit(3)

    assert done[0].wait(10) and done[1].wait(10)
    release.set()
    scheduler.shutdown()
    assert rec.order == ["bulk-1", "bulk-2", "bulk-3"]


def test_lower_limit_does_not_preempt_in_flight() -> None:
    scheduler = LaneScheduler(4, 2, thread_name_prefix="test-lane")
    rec = Recorder()
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    scheduler.submit(_parking(rec, "bulk-1", entered[0], release[0]))
    scheduler.submit(_parking(rec, "bulk-2", entered[1], release[1]))
    assert entered[0].wait(10) and entered[1].wait(10)
    third_started = threading.Event()
    scheduler.submit(_quick(rec, "bulk-3", third_started))

    scheduler.set_limit(1)
    release[0].set()

    # 在途 1 = limit：跑完一个也不补位，必须等在途降到 limit 以下。
    assert not third_started.wait(0.3)
    release[1].set()
    assert third_started.wait(10)
    scheduler.shutdown()
    assert rec.order == ["bulk-1", "bulk-2", "bulk-3"]


def test_in_flight_never_exceeds_limit() -> None:
    scheduler = LaneScheduler(8, 3, thread_name_prefix="test-lane")
    rec = Recorder()

    def make(name: str) -> Callable[[], None]:
        def run() -> None:
            rec.start(name)
            time.sleep(0.02)
            rec.finish()

        return run

    for index in range(20):
        scheduler.submit(make(f"bulk-{index}"))
    scheduler.shutdown()

    assert len(rec.order) == 20
    assert rec.peak <= 3


def test_shutdown_drains_pending_tasks() -> None:
    scheduler = LaneScheduler(4, 1, thread_name_prefix="test-lane")
    rec = Recorder()
    entered, release = threading.Event(), threading.Event()
    scheduler.submit(_parking(rec, "bulk-1", entered, release))
    assert entered.wait(10)
    for index in (2, 3, 4):
        scheduler.submit(_quick(rec, f"bulk-{index}", threading.Event()))

    release.set()
    scheduler.shutdown()

    assert sorted(rec.order) == ["bulk-1", "bulk-2", "bulk-3", "bulk-4"]


def test_submit_after_shutdown_raises() -> None:
    scheduler = LaneScheduler(2, 1, thread_name_prefix="test-lane")
    scheduler.shutdown()
    with pytest.raises(RuntimeError, match="shut down"):
        scheduler.submit(lambda: None)


def test_invalid_limit_rejected() -> None:
    with pytest.raises(ValueError):
        LaneScheduler(2, 0, thread_name_prefix="test-lane")
    scheduler = LaneScheduler(2, 1, thread_name_prefix="test-lane")
    with pytest.raises(ValueError):
        scheduler.set_limit(0)
    scheduler.shutdown()
