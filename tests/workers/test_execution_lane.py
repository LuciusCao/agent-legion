"""Tests for the idle-dying execution lane (#647 phase 3).

``worker/execution/execution_lane.py``: the executor's ThreadPoolExecutor
replacement — submit/shutdown/Future contract identical, but idle threads
exit after the idle timeout so the pool tracks live executions instead of
the historical peak (#647 measured 1177 never-shrinking idle workers).
"""

from __future__ import annotations

import threading
import time

import pytest

from worker.execution.execution_lane import ExecutionLanePool

pytestmark = pytest.mark.no_db


def test_submit_returns_future_with_result() -> None:
    pool = ExecutionLanePool(4, idle_timeout=5)
    try:
        assert pool.submit(lambda a, b: a + b, 1, 2).result(timeout=5) == 3
    finally:
        pool.shutdown(wait=True)


def test_submit_propagates_task_exception_without_killing_thread() -> None:
    """任务异常经 Future 传播（executor 的 reap 语义不变），lane 线程存活。"""
    pool = ExecutionLanePool(2, idle_timeout=5)
    try:

        def boom() -> None:
            raise ValueError("task failure")

        future = pool.submit(boom)
        with pytest.raises(ValueError, match="task failure"):
            future.result(timeout=5)
        # 同一线程继续服务后续任务。
        assert pool.submit(lambda: "still-alive").result(timeout=5) == "still-alive"
    finally:
        pool.shutdown(wait=True)


def test_idle_threads_die_after_timeout() -> None:
    """#647 三期核心钉：空闲线程超时退出，线程数不驻留历史峰值。"""
    pool = ExecutionLanePool(16, idle_timeout=0.2)
    release = threading.Event()

    def blocker() -> None:
        release.wait(10)

    futures = [pool.submit(blocker) for _ in range(4)]
    deadline = time.monotonic() + 5
    while pool.live_threads() < 4 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert pool.live_threads() == 4
    release.set()
    for future in futures:
        future.result(timeout=5)
    # 空闲超时后全部退出 → 常数 0（而不是 ThreadPoolExecutor 的永久 4）。
    deadline = time.monotonic() + 5
    while pool.live_threads() > 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pool.live_threads() == 0


def test_threads_respawn_after_idle_death() -> None:
    """空闲死亡后的新 submit 重新拉起线程（不因池「冷却」而丢任务）。"""
    pool = ExecutionLanePool(4, idle_timeout=0.2)
    try:
        assert pool.submit(lambda: 1).result(timeout=5) == 1
        deadline = time.monotonic() + 5
        while pool.live_threads() > 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pool.live_threads() == 0
        assert pool.submit(lambda: 2).result(timeout=5) == 2
    finally:
        pool.shutdown(wait=True)


def test_max_workers_caps_live_threads() -> None:
    """上限语义与 ThreadPoolExecutor 一致：最多 max_workers 根线程。"""
    pool = ExecutionLanePool(3, idle_timeout=5)
    try:
        release = threading.Event()

        def blocker() -> None:
            release.wait(10)

        futures = [pool.submit(blocker) for _ in range(10)]
        deadline = time.monotonic() + 5
        while pool.live_threads() < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.3)
        assert pool.live_threads() == 3
        release.set()
        for future in futures:
            future.result(timeout=10)
    finally:
        pool.shutdown(wait=True)


def test_submit_after_shutdown_raises() -> None:
    pool = ExecutionLanePool(2, idle_timeout=0.1)
    pool.shutdown(wait=True)
    with pytest.raises(RuntimeError, match="shutdown"):
        pool.submit(lambda: None)


def test_shutdown_waits_for_in_flight_tasks() -> None:
    """shutdown(wait=True)：在飞任务跑完（future 有结果）、哨兵立即唤醒
    空闲线程（不等满 idle timeout）。"""
    pool = ExecutionLanePool(2, idle_timeout=30)
    release = threading.Event()

    def blocker() -> None:
        release.wait(10)
        return "done"

    future = pool.submit(blocker)
    finished = threading.Event()

    def shutdown_thread() -> None:
        pool.shutdown(wait=True)
        finished.set()

    thread = threading.Thread(target=shutdown_thread)
    thread.start()
    time.sleep(0.3)  # shutdown 已进入等待，任务仍在跑
    assert not finished.is_set()
    release.set()
    thread.join(timeout=10)
    assert finished.is_set(), "shutdown must unblock once in-flight work drains"
    assert future.result(timeout=1) == "done"


def test_shutdown_sentinel_beats_idle_timeout() -> None:
    """哨兵 shutdown 的时序钉：空闲线程被立即唤醒退出（不等 idle_timeout）。
    idle_timeout=30s 的池，shutdown 必须在秒级完成——这正是 ThreadPoolExecutor
    对齐项（executor 收尾 join 不能等 30s）。"""
    pool = ExecutionLanePool(4, idle_timeout=30)
    pool.submit(lambda: None).result(timeout=5)
    assert pool.live_threads() >= 1
    started = time.monotonic()
    pool.shutdown(wait=True)
    assert time.monotonic() - started < 10
    assert pool.live_threads() == 0


def test_parallel_tasks_all_complete() -> None:
    """并发正确性：N 个并行任务全部完成且结果正确（任务不丢、不错配）。"""
    pool = ExecutionLanePool(8, idle_timeout=5)
    try:
        futures = [pool.submit(lambda i=i: i * i, i) for i in range(24)]
        results = sorted(future.result(timeout=10) for future in futures)
        assert results == [i * i for i in range(24)]
    finally:
        pool.shutdown(wait=True)
