"""Tests for #647 (#578 phases 2/3): event-driven exit watching + idle-dying
execution lane.

``test_execution_exit_watch.py`` covers the watcher's contract against the
legacy ``process_lifecycle.poll_wait_locally`` semantics (same verdicts, same
priority order), the fail-closed death path, and the singleton replacement;
``test_execution_lane.py`` (sibling file) covers the pool. The #564 mutex
equivalence under the event model lives here because the waiter's verdict
re-derivation is what the old attempt's teardown interacts with.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from worker.execution import exit_watch
from worker.execution.exit_watch import ExitWatchReactor, wait_for_exit
from worker.execution.ownership import execution_mutex

pytestmark = pytest.mark.no_db


def _spawn_sleep(seconds: float) -> subprocess.Popen[bytes]:
    # start_new_session 对齐生产 spawn 契约（run.py / code_runner.py）：
    # terminate 的 killpg(proc.pid) 语义依赖 pgid == pid。
    return subprocess.Popen(["sleep", str(seconds)], start_new_session=True)


@pytest.fixture()
def reactor():
    """A private reactor per test (never the process singleton)."""
    instance = ExitWatchReactor("auto")
    yield instance
    instance.shutdown()


def _wait(
    instance: ExitWatchReactor,
    proc: subprocess.Popen[bytes],
    timeout: float,
    shutdown: threading.Event | None = None,
    grace: float = 1,
    ownership_lost: threading.Event | None = None,
    cancelled: threading.Event | None = None,
) -> tuple[int, bool]:
    return instance.wait_for_exit(
        proc,
        timeout,
        shutdown or threading.Event(),
        grace,
        ownership_lost or threading.Event(),
        cancelled,
    )


def test_exit_returns_code_and_report(reactor: ExitWatchReactor) -> None:
    """正常退出：返回真实 exit code 与 report=True（对齐旧 wait_for_exit）。"""
    proc = _spawn_sleep(0.1)
    started = time.monotonic()
    code, report = _wait(reactor, proc, 10)
    assert (code, report) == (0, True)
    # 事件驱动唤醒不得显著晚于进程退出（tick 上界 ~1.5s，kqueue 即时）。
    assert time.monotonic() - started < 2.5


def test_nonzero_exit_code_propagates(reactor: ExitWatchReactor) -> None:
    proc = subprocess.Popen(["bash", "-c", "exit 7"], start_new_session=True)
    code, report = _wait(reactor, proc, 10)
    assert (code, report) == (7, True)


def test_timeout_terminates_and_reports_124(reactor: ExitWatchReactor) -> None:
    """超时：SIGTERM 收尾 + 124/cancelled（旧语义），且按 deadline 触发
    （tick 收紧到最近 deadline，不是整 tick 后才生效）。"""
    proc = _spawn_sleep(30)
    started = time.monotonic()
    code, report = _wait(reactor, proc, 0.5)
    assert (code, report) == (124, True)
    assert time.monotonic() - started < 8  # 0.5s deadline + terminate(5) 上界


def test_shutdown_reports_130_with_grace(reactor: ExitWatchReactor) -> None:
    proc = _spawn_sleep(30)
    shutdown = threading.Event()
    threading.Timer(0.3, shutdown.set).start()
    started = time.monotonic()
    code, report = _wait(reactor, proc, 30, shutdown=shutdown, grace=1)
    assert (code, report) == (130, True)
    assert time.monotonic() - started < 10  # 0.3s 触发 + tick + grace 收尾


def test_ownership_lost_kills_and_discards(reactor: ExitWatchReactor) -> None:
    """租约丢失：杀进程、不 report（结果作废，Host 重新调度）——#564 语义。"""
    proc = _spawn_sleep(30)
    lost = threading.Event()
    threading.Timer(0.3, lost.set).start()
    started = time.monotonic()
    code, report = _wait(reactor, proc, 30, ownership_lost=lost)
    assert (code, report) == (1, False)
    assert time.monotonic() - started < 10


def test_cancelled_reports_130(reactor: ExitWatchReactor) -> None:
    """Host 取消（code 路径的 heartbeat body cancel）：130 + report=True。"""
    proc = _spawn_sleep(30)
    cancelled = threading.Event()
    threading.Timer(0.3, cancelled.set).start()
    code, report = _wait(reactor, proc, 30, cancelled=cancelled)
    assert (code, report) == (130, True)


def test_control_events_wake_parked_waiter(reactor: ExitWatchReactor) -> None:
    """控制事件（shutdown）在进程未退出的整个窗口内持续可唤醒：park 中的
    等待者必须被 tick 拍醒，而不是只在注册瞬间检查一次。"""
    proc = _spawn_sleep(30)
    shutdown = threading.Event()
    result: list[tuple[int, bool]] = []

    def waiter() -> None:
        result.append(_wait(reactor, proc, 30, shutdown=shutdown))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(1.0)  # 让等待者先 park 一个完整 tick 以上
    shutdown.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert result == [(130, True)]


def test_concurrent_waiters_scale_one_thread(reactor: ExitWatchReactor) -> None:
    """N 个并发等待只占 1 根 watcher 线程：全部按各自退出时刻唤醒。"""
    procs = [_spawn_sleep(0.3) for _ in range(8)]
    results: list[tuple[int, bool]] = []
    lock = threading.Lock()

    def wait_one(proc: subprocess.Popen[bytes]) -> None:
        outcome = _wait(reactor, proc, 15)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=wait_one, args=(p,)) for p in procs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    assert results == [(0, True)] * 8


def test_externally_reaped_child_still_resolves(reactor: ExitWatchReactor) -> None:
    """心跳线程可能先 poll/reap 掉子进程（zombie-stop）：pidfd 可读性与
    kqueue 已入队事件都是 sticky 的，等待者必须仍能定谳而不是悬挂。"""
    proc = _spawn_sleep(0.2)
    time.sleep(1.0)  # 让子进程退出并由本线程 poll 收尸
    assert proc.poll() == 0
    started = time.monotonic()
    code, report = _wait(reactor, proc, 5)
    assert (code, report) == (0, True)
    assert time.monotonic() - started < 6


def test_watcher_death_degrades_to_local_polling(reactor: ExitWatchReactor) -> None:
    """fail-closed：watcher 死亡时已 park 的等待者被立即唤醒并退回
    process_lifecycle.poll_wait_locally（语义与旧版逐字节一致）。"""
    proc = _spawn_sleep(0.3)
    waiter = reactor.register(proc, 10, threading.Event(), threading.Event(), None)
    woke = threading.Event()
    threading.Thread(target=lambda: (waiter.done.wait(), woke.set())).start()
    time.sleep(0.2)
    started = time.monotonic()
    reactor._fail_dead()
    assert woke.wait(2), "parked waiter must be woken on watcher death"
    assert waiter.watcher_dead
    assert time.monotonic() - started < 1
    proc.wait()


def test_dead_reactor_registration_resolves_immediately() -> None:
    """死后的新 register 立即 done + watcher_dead（新等待走本地轮询），
    get() 之后的等待落到新单例。"""
    instance = ExitWatchReactor("scan")
    try:
        instance._fail_dead()
        proc = _spawn_sleep(0.1)
        waiter = instance.register(proc, 5, threading.Event(), threading.Event(), None)
        assert waiter.done.is_set()
        assert waiter.watcher_dead
        proc.wait()
    finally:
        instance.shutdown()
    # 死亡单例被 get() 替换
    fresh = exit_watch.ExitWatchReactor.get()
    try:
        assert fresh is not instance
        assert not fresh.is_dead()
    finally:
        fresh.shutdown()
        exit_watch.ExitWatchReactor._singleton = None


def test_scan_mode_heartbeat_equivalent() -> None:
    """scan 模式（兜底路径）与内核模式判定等价——同一组契约用例。"""
    instance = ExitWatchReactor("scan")
    try:
        proc = _spawn_sleep(0.1)
        code, report = _wait(instance, proc, 10)
        assert (code, report) == (0, True)
        proc = _spawn_sleep(30)
        shutdown = threading.Event()
        threading.Timer(0.2, shutdown.set).start()
        code, report = _wait(instance, proc, 30, shutdown=shutdown)
        assert (code, report) == (130, True)
    finally:
        instance.shutdown()


def test_mutex_equivalence_under_event_model(tmp_path: Path) -> None:
    """#647 三期第 3 点：#564 的 per-execution 互斥锁在事件模型下的等价性。

    事件化后等待者 park 在自己的 Event 上、唤醒后再 derive 判定——旧
    attempt 的丢弃收尾（rmtree + 退出临界区）与新 attempt 的 prepare
    仍必须被 execution_mutex 串行化。这里直接复刻 #564 的双 attempt 场景
    骨架：attempt A 持锁 park（子进程在跑），attempt B 等锁有界——A 的
    完整退出（含子进程收割）先于 B 的 prepare 发生。"""
    instance = ExitWatchReactor("scan")
    try:
        order: list[str] = []
        proc = _spawn_sleep(0.3)
        ready = threading.Event()
        b_prepare_started = threading.Event()

        def attempt_a() -> None:
            with execution_mutex("exec-mutex-1"):
                order.append("a-locked")
                ready.set()
                _wait(instance, proc, 10)  # park 直到子进程退出（事件驱动）
                order.append("a-child-exited")
            order.append("a-unlocked")

        def attempt_b() -> None:
            ready.wait(5)
            with execution_mutex("exec-mutex-1", timeout=10) as acquired:
                assert acquired
                b_prepare_started.set()
                order.append("b-locked")

        thread_a = threading.Thread(target=attempt_a)
        thread_b = threading.Thread(target=attempt_b)
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)
        assert not thread_a.is_alive() and not thread_b.is_alive()
        # 串行化钉：B 的 prepare 只能发生在 A 完全退出（锁释放）之后。
        assert order.index("a-unlocked") < order.index("b-locked")
        assert "a-child-exited" in order
    finally:
        instance.shutdown()


def test_module_facade_matches_legacy_signature() -> None:
    """wait_for_exit 门面与旧 process_lifecycle.wait_for_exit 签名逐参一致
    （run.py / code_runner.py 的调用面零改动换接）。"""
    import inspect

    legacy = inspect.signature(
        __import__("worker.process_lifecycle", fromlist=["poll_wait_locally"]).poll_wait_locally
    )
    modern = inspect.signature(wait_for_exit)
    assert list(legacy.parameters) == list(modern.parameters)
    assert legacy.return_annotation == modern.return_annotation


def test_resolve_mode_auto_prefers_first_available_kernel() -> None:
    """auto：首个可用内核后端（本机 macOS = kqueue；无内核平台 = scan）。
    codex P2（PR #648）：只有 auto 才做可用性挑选。"""
    mode = exit_watch._resolve_mode("auto")
    if exit_watch._kqueue_available():
        assert mode == "kqueue"
    elif exit_watch._pidfd_available():
        assert mode == "pidfd"
    else:
        assert mode == "scan"


def test_resolve_mode_explicit_request_is_honored_or_scan() -> None:
    """codex P2（PR #648）：显式请求是灰度/回退通道——可用即遵守，不可用
    回落 scan 并打印原因；不得静默替换成另一个后端（旧 bug：Linux 显式
    kqueue 打印 scan 却返回 pidfd；macOS 显式 pidfd 被强转 kqueue）。"""
    if exit_watch._kqueue_available():
        assert exit_watch._resolve_mode("kqueue") == "kqueue"
        # macOS 有 kqueue 无 pidfd：显式 pidfd 必须回落 scan，不是 kqueue。
        if not exit_watch._pidfd_available():
            assert exit_watch._resolve_mode("pidfd") == "scan"
    if exit_watch._pidfd_available():
        assert exit_watch._resolve_mode("pidfd") == "pidfd"
        # Linux 有 pidfd 无 kqueue：显式 kqueue 必须回落 scan，不是 pidfd。
        if not exit_watch._kqueue_available():
            assert exit_watch._resolve_mode("kqueue") == "scan"
    assert exit_watch._resolve_mode("scan") == "scan"


def test_resolve_mode_unknown_value_falls_back_to_auto() -> None:
    """非法 env 值按 auto 处理（宽容：监督模式 typo 不该炸执行）。"""
    mode = exit_watch._resolve_mode("bogus")
    assert mode in ("kqueue", "pidfd", "scan")


def test_watcher_does_not_busy_spin_during_terminate_window(reactor: ExitWatchReactor) -> None:
    """subagent review P1：超时判定后、调用方还在 terminate() 宽限窗内时，
    waiter 仍在注册表且 deadline 已过——_tick_timeout 不得被它钳到 0
    （实测 58.6 万次/秒空转，重新引入本 PR 要消灭的监督税）。"""
    reactor2 = ExitWatchReactor("scan")
    try:
        # 模拟超时判定后的状态：waiter 已定谳（done 已置）但调用方仍在
        # terminate() 里等 SIGKILL 宽限——它留在注册表、deadline 在过去。
        proc = _spawn_sleep(60)
        waiter = reactor2.register(proc, 0.1, threading.Event(), threading.Event(), None)
        waiter.timed_out = True
        waiter.done.set()
        time.sleep(1.2)  # 跨过 deadline，确认它确在"过去"
        # 修复前：min(过去时 deadline - now, 0.5) → 0 → watcher 空转。
        # 修复后：done-set waiter 被排除，无其他 waiter 时返回整 tick。
        timeout = reactor2._tick_timeout()
        assert timeout > 0.1, f"watcher would busy-spin: tick timeout {timeout}"
        proc.kill()
        proc.wait()
    finally:
        reactor2.shutdown()


def test_double_register_resolves_displaced_waiter(reactor: ExitWatchReactor) -> None:
    """subagent review P1-latent：同 pid 二次注册不得让首个 waiter 永久
    悬挂（字典覆盖后没有任何唤醒源能到达它）——被顶掉的 waiter 应立即以
    watcher_dead 定谳（调用方退回本地轮询）。"""
    proc = _spawn_sleep(30)
    first = reactor.register(proc, 30, threading.Event(), threading.Event(), None)
    second = reactor.register(proc, 30, threading.Event(), threading.Event(), None)
    assert first.done.is_set()
    assert first.watcher_dead
    assert not second.done.is_set()
    proc.kill()
    proc.wait()


def test_degradation_path_returns_real_verdict() -> None:
    """subagent review P2：fail-closed 降级分支的端到端钉——watcher 死亡
    后 wait_for_exit 走 poll_wait_locally 必须返回真实判定（不是只验证
    标志位）。子进程已退出 + watcher 已死 → 本地轮询立即定谳 exit code。"""
    instance = ExitWatchReactor("scan")
    try:
        instance._fail_dead()
        proc = subprocess.Popen(["true"], start_new_session=True)
        code, report = _wait(instance, proc, 5)
        assert (code, report) == (0, True)
    finally:
        instance.shutdown()


def test_orderly_shutdown_resolves_parked_waiters() -> None:
    """subagent review P2：reactor 的 shutdown() 必须先唤醒全部 parked
    waiter（以 watcher_dead 定谳），不得让它们悬挂——fail-closed 不变量
    不应被唯一的停止路径绕过。"""
    instance = ExitWatchReactor("scan")
    proc = _spawn_sleep(30)
    waiter = instance.register(proc, 30, threading.Event(), threading.Event(), None)
    instance.shutdown()
    assert waiter.done.is_set()
    assert waiter.watcher_dead
    proc.kill()
    proc.wait()


def test_fail_dead_closes_reactor_owned_fds() -> None:
    """codex P2 round 2：_fail_dead 也要关 reactor 自身资源（kqueue/selector
    + wakeup pipe）——持续故障下每次重建泄漏至少 2 个 fd，直到耗尽 Worker
    的 fd 上限使监督初始化失败。修后：死亡路径句柄关闭并置空（防二次
    close 复用 fd）。"""
    import os

    # kqueue/pidfd 模式才持有 wakeup pipe；scan 无内核 fd，用本机可用模式。
    mode = "kqueue" if exit_watch._kqueue_available() else "pidfd"
    if not (exit_watch._kqueue_available() or exit_watch._pidfd_available()):
        pytest.skip("no kernel backend on this platform")
    instance = ExitWatchReactor(mode)
    try:
        r_before, w_before = instance._wakeup_r, instance._wakeup_w
        assert r_before >= 0 and w_before >= 0
        open_before = os.listdir("/dev/fd") if Path("/dev/fd").is_dir() else None
        instance._fail_dead()
        assert instance._wakeup_r == -1 and instance._wakeup_w == -1
        # fd 确已关闭：对旧 fd 的写应 EBADF（被复用的极小概率不在此断言内）。
        import errno

        try:
            os.write(w_before, b"x")
            raised = False
        except OSError as exc:
            raised = exc.errno == errno.EBADF
        assert raised, "wakeup pipe fd must be closed after _fail_dead"
        if open_before is not None:
            open_after = os.listdir("/dev/fd")
            assert len(open_after) <= len(open_before) + 1
    finally:
        instance.shutdown()
