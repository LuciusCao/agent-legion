"""Unit tests for execution-dir ownership (worker/execution/ownership.py, #564).

The dual-attempt race: the Host requeues an execution whose lease it judged
expired while the old attempt thread is still alive, and the same Worker
immediately re-claims it. Two layers are pinned here — the on-disk lease
marker (the discard tail only rmtree's a dir it can prove is still its own)
and the per-execution in-process mutex (old teardown and new prepare
serialize). The threaded end-to-end reproduction lives in
tests/workers/test_execution_run.py.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from worker.execution import ownership
from worker.execution.heartbeat import start_lease_heartbeat
from worker.execution.ownership import (
    OWNER_FILENAME,
    discard_owned_dir,
    execution_mutex,
    write_owner_marker,
)
from worker.execution.run import deliver_result
from worker.status import ExecutionStatusReporter
from worker.upload.queue import PENDING_FILENAME, UploadQueue


def _claim(lease_id: str) -> dict:
    return {"execution_id": "exec-1", "lease_id": lease_id}


def test_discard_allowed_when_marker_names_own_lease(tmp_path: Path) -> None:
    execution_dir = tmp_path / "exec-1"
    execution_dir.mkdir()
    write_owner_marker(execution_dir, _claim("lease-1"))

    assert discard_owned_dir(execution_dir, "lease-1")


def test_discard_refused_when_marker_names_another_lease(tmp_path: Path) -> None:
    """目录已易主：标记是新 attempt 的 lease，旧 attempt 不得 rmtree。"""
    execution_dir = tmp_path / "exec-1"
    execution_dir.mkdir()
    write_owner_marker(execution_dir, _claim("lease-2"))

    assert not discard_owned_dir(execution_dir, "lease-1")


def test_discard_refused_when_marker_missing_or_unreadable(tmp_path: Path) -> None:
    """缺失/损坏的标记不是归属证明——宁可留下孤儿目录给 stale sweeper，
    也不删一个无法证明仍归自己的目录。"""
    execution_dir = tmp_path / "exec-1"
    execution_dir.mkdir()

    assert not discard_owned_dir(execution_dir, "lease-1")

    (execution_dir / OWNER_FILENAME).write_text("not json{", encoding="utf-8")
    assert not discard_owned_dir(execution_dir, "lease-1")

    (execution_dir / OWNER_FILENAME).write_text('{"version": 1}', encoding="utf-8")
    assert not discard_owned_dir(execution_dir, "lease-1")


def test_discard_refused_when_pending_upload_marker_present(tmp_path: Path) -> None:
    """#203 否决优先：目录归 UploadQueue 所有时即使归属标记匹配也不删。"""
    execution_dir = tmp_path / "exec-1"
    execution_dir.mkdir()
    write_owner_marker(execution_dir, _claim("lease-1"))
    (execution_dir / PENDING_FILENAME).write_text(
        '{"version": 1, "execution_id": "exec-1", "lease_id": "lease-1"}', encoding="utf-8"
    )

    assert not discard_owned_dir(execution_dir, "lease-1")


def _deliver_discard(execution_dir: Path, lease_id: str) -> None:
    """Drive deliver_result's local-discard tail (task=None = lease lost)."""
    heartbeat = start_lease_heartbeat(None, "exec-1", lease_id, 0.05, threading.Event())
    uploads = UploadQueue(
        None,
        ExecutionStatusReporter(None),
        max_concurrency=1,
        heartbeat_interval=0.05,
        stop=threading.Event(),
    )
    deliver_result(
        None, uploads, ExecutionStatusReporter(None), None, heartbeat, execution_dir, "exec-1"
    )
    uploads.shutdown()


def test_discard_tail_preserves_dir_reclaimed_by_new_attempt(tmp_path: Path) -> None:
    """#564 复现钉：旧 attempt（lease-1）的丢弃收尾撞上目录已易主——新
    attempt（lease-2）已重建目录、写好归属标记与 prompt.md。修复前收尾
    仅查 pending marker 就 rmtree，删掉新 attempt 正在使用的目录（新
    attempt 随后写 prompt.md 时 FileNotFoundError）；修复后跳过删除。"""
    execution_dir = tmp_path / "exec-1"
    prompt = execution_dir / "job" / "runs" / "node_a" / "worker" / "prompt.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("prompt of attempt 2", encoding="utf-8")
    write_owner_marker(execution_dir, _claim("lease-2"))

    _deliver_discard(execution_dir, "lease-1")

    assert prompt.read_text(encoding="utf-8") == "prompt of attempt 2"
    assert (execution_dir / OWNER_FILENAME).is_file()


def test_discard_tail_removes_dir_still_owned_by_own_lease(tmp_path: Path) -> None:
    """既有语义回归：无并发重 claim 时（标记仍归本 lease），租约丢失 /
    release 409 的丢弃收尾照常清理目录。"""
    execution_dir = tmp_path / "exec-1"
    execution_dir.mkdir()
    (execution_dir / "junk").write_text("x", encoding="utf-8")
    write_owner_marker(execution_dir, _claim("lease-1"))

    _deliver_discard(execution_dir, "lease-1")

    assert not execution_dir.exists()


def test_discard_tail_leaves_orphan_marker_dir_for_sweeper(tmp_path: Path) -> None:
    """worker 重启残留场景：标记属于已死 incarnation 的 lease（无任何活
    attempt 持有）。丢弃收尾不认识它（不是自己的 lease）→ 不删；孤儿目录
    的清理归 startup clean_work_root / stale sweeper，不归丢弃收尾。"""
    execution_dir = tmp_path / "exec-1"
    execution_dir.mkdir()
    write_owner_marker(execution_dir, _claim("lease-dead"))

    _deliver_discard(execution_dir, "lease-1")

    assert execution_dir.exists()


def test_execution_mutex_serializes_same_execution_id() -> None:
    """同一 execution_id 的两个 holder 严格串行（共享计数器峰值 ≤ 1）。"""
    active = 0
    max_active = 0
    counter_lock = threading.Lock()
    entered = [threading.Event(), threading.Event()]

    def holder(index: int) -> None:
        nonlocal active, max_active
        with execution_mutex("exec-1"):
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            entered[index].set()
            time.sleep(0.05)
            with counter_lock:
                active -= 1

    threads = [threading.Thread(target=holder, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert max_active == 1
    assert all(event.is_set() for event in entered)


def test_execution_mutex_allows_different_execution_ids_in_parallel() -> None:
    """不同 execution_id 互不阻塞：两个 holder 必须同时在临界区内（屏障
    会合证明并行度），否则即串行化误伤。"""
    barrier = threading.Barrier(2, timeout=10)

    def holder(execution_id: str) -> None:
        with execution_mutex(execution_id):
            barrier.wait()

    threads = [
        threading.Thread(target=holder, args=("exec-a",)),
        threading.Thread(target=holder, args=("exec-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not any(thread.is_alive() for thread in threads)


def test_execution_mutex_table_entry_dropped_after_last_holder() -> None:
    """锁表按 execution_id 引用计数回收：长跑 worker 不为历史 execution
    各留一把锁。"""
    with execution_mutex("exec-gc"):
        assert "exec-gc" in ownership._MUTEX_TABLE
    assert "exec-gc" not in ownership._MUTEX_TABLE


def test_execution_mutex_wait_timeout_yields_false_and_recycles_entry() -> None:
    """#564 P2：有界等锁——holder 不放锁时 contender 等满 timeout 拿到
    False（不抛异常、快速返回），且 False 路径不误释放 holder 的锁；双方
    退出后表项计数正确回收。"""
    contender_result: list[bool] = []

    def contender() -> None:
        with execution_mutex("exec-busy", timeout=0.2) as acquired:
            contender_result.append(acquired)

    with execution_mutex("exec-busy") as first:
        assert first
        thread = threading.Thread(target=contender)
        started = time.monotonic()
        thread.start()
        thread.join(timeout=10)
        elapsed = time.monotonic() - started
        assert not thread.is_alive()
        # contender 放弃后 holder 仍持有锁：再探一次依旧拿不到。
        with execution_mutex("exec-busy", timeout=0.05) as second:
            assert not second
    assert contender_result == [False]
    assert 0.1 < elapsed < 10
    assert "exec-busy" not in ownership._MUTEX_TABLE
