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


def test_sequential_submits_reuse_the_idle_thread() -> None:
    """codex P2（PR #648）：submit 只在无空闲线程时新建——顺序 submit/
    wait/submit（并发 1、提交速率高于空闲超时）不得把线程数爬到上限；
    修复前 submit 只看存活总数，逐个提交也逐个新建线程。"""
    pool = ExecutionLanePool(8, idle_timeout=30)
    try:
        for _ in range(6):
            assert pool.submit(lambda: "done").result(timeout=5) == "done"
            # 等线程回到 park（idle 位归还）再提交下一单，模拟稳定的
            # 低并发节流。轮询等待而非 sleep 固定时长，快机器不拖慢。
            deadline = time.monotonic() + 5
            while pool.live_threads() > 1 and time.monotonic() < deadline:
                time.sleep(0.01)
        assert pool.live_threads() == 1, (
            f"sequential workload must reuse one lane thread, got {pool.live_threads()}"
        )
    finally:
        pool.shutdown(wait=True)


def test_burst_submissions_grow_to_concurrency_not_max() -> None:
    """codex P2 对照钉：并发 3 的持续负载（每轮 submit 后等完成再提交下一
    轮）线程数钉在并发数 3，而不是逐轮爬到 max_workers=8。"""
    pool = ExecutionLanePool(8, idle_timeout=30)
    try:
        for _round in range(6):
            futures = [pool.submit(lambda r=r: r) for r in range(3)]
            for future in futures:
                future.result(timeout=5)
            deadline = time.monotonic() + 5
            while pool.live_threads() > 3 and time.monotonic() < deadline:
                time.sleep(0.01)
        assert pool.live_threads() == 3, (
            f"steady concurrency 3 must hold 3 lane threads, got {pool.live_threads()}"
        )
    finally:
        pool.shutdown(wait=True)


def test_queued_demand_beyond_idle_spawns() -> None:
    """spawn 判定的需求侧：已有 1 线程忙 + 队列积压 2 未取时，新 submit
    不得因「存活数 < max」以外的宽松判定少 spawn——排队需求必须被覆盖。"""
    pool = ExecutionLanePool(8, idle_timeout=30)
    try:
        release = threading.Event()

        def blocker() -> None:
            release.wait(10)

        # 第一单占住唯一线程（spawn 1 根，忙）。
        first = pool.submit(blocker)
        deadline = time.monotonic() + 5
        while pool.live_threads() < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        # 突发 4 单：1 根忙线程吃不掉，demand 4 > idle 0 → 逐单 spawn 到 4。
        more = [pool.submit(blocker) for _ in range(3)]
        deadline = time.monotonic() + 5
        while pool.live_threads() < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pool.live_threads() == 4
        release.set()
        first.result(timeout=5)
        for future in more:
            future.result(timeout=5)
    finally:
        pool.shutdown(wait=True)


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


def test_cancel_during_run_is_rejected_and_lane_survives() -> None:
    """subagent review P1：任务运行中 Future.cancel() 必须被拒绝（池调用
    set_running_or_notify_cancel 后 running 态不可取消，与
    ThreadPoolExecutor 一致）。旧形态（无 claim）cancel 成功 → 收尾
    set_result 抛 InvalidStateError 逃逸遏制 → lane 线程死亡、_busy 泄漏、
    _live 幽灵化、shutdown 永挂——修后三层钉：拒绝取消、线程存活、
    busy 归零。"""
    pool = ExecutionLanePool(2, idle_timeout=30)
    try:
        started = threading.Event()
        release = threading.Event()

        def slow() -> str:
            started.set()
            release.wait(10)
            return "late"

        future = pool.submit(slow)
        assert started.wait(5)
        assert future.cancel() is False  # running 态：取消被拒绝（TPE 语义）
        release.set()
        # 任务正常完成（没被取消破坏），线程存活继续服务，记账归零。
        assert future.result(timeout=5) == "late"
        assert pool.submit(lambda: "alive").result(timeout=5) == "alive"
        with pool._guard:
            assert pool._busy == 0
        assert pool.live_threads() == 1
    finally:
        pool.shutdown(wait=True)


def test_cancel_before_claim_skips_task_and_releases_slot() -> None:
    """claim 失败分支（排队中被取消）：任务被跳过，busy 记账不泄漏，
    线程继续服务后续任务。"""
    pool = ExecutionLanePool(1, idle_timeout=30)
    try:
        gate = threading.Event()
        holder = pool.submit(gate.wait, 10)  # 占住唯一线程
        # 排队中的第二个任务在 worker 取到之前被取消。
        queued = pool.submit(lambda: "never-run")
        assert queued.cancel() is True  # PENDING 态：取消成功
        gate.set()
        assert holder.result(timeout=5) is True  # gate.wait 的返回值
        # 后续任务照常服务（没被取消项卡住）。
        assert pool.submit(lambda: "next").result(timeout=5) == "next"
        with pool._guard:
            assert pool._busy == 0
    finally:
        pool.shutdown(wait=True)


def test_shutdown_does_not_strand_late_submitted_task() -> None:
    """subagent review P1：submit 的 flag 检查与入队必须原子——shutdown
    的哨兵不得排到晚到任务后面（任务滞留死队列而 shutdown(wait=True) 已
    返回）。钉法：shutdown 后队列里不得残留任务项。"""
    pool = ExecutionLanePool(2, idle_timeout=30)
    release = threading.Event()

    def blocker() -> None:
        release.wait(10)

    future = pool.submit(blocker)
    finished = threading.Event()

    def shutdown_thread() -> None:
        pool.shutdown(wait=True)
        finished.set()

    thread = threading.Thread(target=shutdown_thread)
    thread.start()
    release.set()
    thread.join(timeout=10)
    assert finished.is_set()
    # 无滞留任务：哨兵之外的队列项必为空。
    assert pool._queue.qsize() == 0
    assert future.result(timeout=1) is None or future.done()
