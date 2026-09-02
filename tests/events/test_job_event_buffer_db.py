from __future__ import annotations

import threading
from pathlib import Path

import pytest

import server.app.events.buffer as buffer_module
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection
from server.app.events.buffer import JobEventBuffer
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = TEST_DATABASE_URL
    init_db(path)
    return path


def _read_seq_value(db_path) -> int:
    with read_connection(db_path) as conn:
        row = conn.execute("select value from job_event_seq where id = 1").fetchone()
    assert row is not None
    return int(row["value"])


@pytest.fixture
def bump_call_counter(monkeypatch):
    """Spy on ``_bump_seq`` (the job_event_seq UPDATE) while delegating to it."""
    real_bump = buffer_module.JobEventBuffer._bump_seq
    calls: list[int] = []

    def counting_bump(self, db_path, count=1):
        calls.append(count)
        return real_bump(self, db_path, count)

    monkeypatch.setattr(buffer_module.JobEventBuffer, "_bump_seq", counting_bump)
    return calls


def test_revision_continues_across_instances(db_path, monkeypatch):
    # DB seq 值在本测试的并行 xdist worker 间共享且每测后 TRUNCATE 重置，
    # 绝对 revision 值不可假设（conftest #91 纪律）——先读基线再断言增量。
    monkeypatch.setattr(buffer_module, "SEGMENT_SIZE", 8)
    baseline = _read_seq_value(db_path)
    a = JobEventBuffer(db_path=db_path)
    revisions = [a.record("ws1", f"job{i}", "updated") for i in range(5)]
    assert revisions == [baseline + 1, baseline + 2, baseline + 3, baseline + 4, baseline + 5]

    b = JobEventBuffer(db_path=db_path)  # 模拟进程重启
    restarted = b.record("ws1", "job5", "updated")
    # #353 分段语义：a 已把 DB 推进整段（[baseline+1, baseline+8]），b 取下一段。
    # 重启丢弃 a 段内未用完的号（3 个，≤ SEGMENT_SIZE），但发号严格高于重启前。
    assert restarted == baseline + 9
    assert restarted - max(revisions) <= buffer_module.SEGMENT_SIZE
    compacted = b.drain_compacted()
    assert compacted.latest_revision == restarted


def test_restart_waste_bounded_by_segment_size(db_path, monkeypatch):
    """重启后 DB 值已推进整段：浪费的号 ≤ 段大小，且新实例发号严格单调。"""
    monkeypatch.setattr(buffer_module, "SEGMENT_SIZE", 4)
    baseline = _read_seq_value(db_path)
    a = JobEventBuffer(db_path=db_path)
    issued = [a.record("ws1", f"job{i}", "updated") for i in range(3)]
    assert issued == [baseline + 1, baseline + 2, baseline + 3]
    assert _read_seq_value(db_path) == baseline + 4  # a 的段，整段推进

    b = JobEventBuffer(db_path=db_path)
    first = b.record("ws1", "job9", "updated")
    assert first > max(issued)
    assert first - max(issued) <= 4  # 浪费 ≤ 段大小
    assert first == baseline + 5
    # 段内继续消耗，无新 DB 事务
    assert [b.record("ws1", f"job{i}", "updated") for i in range(3)] == [
        baseline + 6,
        baseline + 7,
        baseline + 8,
    ]


def test_segment_allocation_reduces_seq_update_frequency(db_path, monkeypatch, bump_call_counter):
    """削峰断言：段大小 N、M 次 record 只产生 ceil(M/N) 次 job_event_seq UPDATE。"""
    monkeypatch.setattr(buffer_module, "SEGMENT_SIZE", 4)
    baseline = _read_seq_value(db_path)
    buf = JobEventBuffer(db_path=db_path)
    m = 10
    revisions = [buf.record("ws1", f"job{i}", "updated") for i in range(m)]

    assert revisions == [baseline + i for i in range(1, m + 1)]  # 段内连续，全局单调
    assert len(bump_call_counter) == 3  # ceil(10/4)
    assert bump_call_counter == [4, 4, 4]
    assert _read_seq_value(db_path) == baseline + 12  # DB 按整段推进：三段


def test_default_segment_size_amortizes_by_two_orders_of_magnitude(db_path, bump_call_counter):
    """验收 #353-2：默认段大小（1024）下，UPDATE 频率下降 ≥ 两个数量级。"""
    baseline = _read_seq_value(db_path)
    buf = JobEventBuffer(db_path=db_path)
    m = 3000
    for i in range(m):
        buf.record("ws1", f"job{i}", "updated")

    updates = len(bump_call_counter)
    assert updates == 3  # ceil(3000/1024)
    assert updates * 100 <= m  # ≥ 两个数量级（实际 1000x）
    assert buf.current_revision() == baseline + m
    assert _read_seq_value(db_path) == baseline + 3 * buffer_module.SEGMENT_SIZE


def test_revision_monotonic_across_segment_boundaries(db_path, monkeypatch, bump_call_counter):
    """段边界处（触发多次新段获取）issued revision 仍严格递增，deque 顺序匹配。"""
    monkeypatch.setattr(buffer_module, "SEGMENT_SIZE", 3)
    baseline = _read_seq_value(db_path)
    buf = JobEventBuffer(db_path=db_path)
    revisions = [buf.record("ws1", f"job{i}", "updated") for i in range(8)]

    assert len(bump_call_counter) == 3  # [1,3] [4,6] [7,9]：至少跨两次段边界
    assert revisions == sorted(revisions)
    assert len(set(revisions)) == len(revisions)
    assert revisions == [baseline + i for i in range(1, 9)]
    drained = [event.revision for event in buf.drain()]
    assert drained == sorted(drained)


def test_concurrent_records_on_shared_buffer_unique_revisions(
    db_path, monkeypatch, bump_call_counter
):
    """并发回归：同一 buffer 多线程并发 record，段计数器在 self._lock 内更新，
    revision 无重复且 deque append 顺序始终匹配 revision 顺序。"""
    monkeypatch.setattr(buffer_module, "SEGMENT_SIZE", 4)
    baseline = _read_seq_value(db_path)
    buf = JobEventBuffer(db_path=db_path)
    barrier = threading.Barrier(4)
    issued: list[int] = []
    issued_lock = threading.Lock()

    def worker() -> None:
        barrier.wait(5)
        for _i in range(10):
            rev = buf.record("ws1", "jobx", "updated")
            with issued_lock:
                issued.append(rev)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)

    assert len(issued) == 40
    assert len(set(issued)) == 40  # 无重复
    assert sorted(issued) == [baseline + i for i in range(1, 41)]  # 40 号恰好连续耗尽
    assert len(bump_call_counter) == 10  # ceil(40/4) 次 UPDATE
    revisions = [event.revision for event in buf.drain()]
    assert revisions == sorted(revisions)
    assert buf.current_revision() == baseline + 40


def test_revision_unique_across_concurrent_instances(db_path, monkeypatch):
    """多实例（模拟多副本）并发发号：各自取段，跨实例无重复、单调推进。
    段间发布的乱序窗口 = 段大小（见 buffer.py 注释，单进程形态无此问题）。"""
    monkeypatch.setattr(buffer_module, "SEGMENT_SIZE", 4)
    barrier = threading.Barrier(4)

    issued: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        buf = JobEventBuffer(db_path=db_path)
        barrier.wait(5)
        for _i in range(10):
            rev = buf.record("ws1", "jobx", "updated")
            with lock:
                issued.append(rev)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert len(issued) == 40
    assert len(set(issued)) == 40  # 无重复（每实例各持一段）
    assert min(issued) < max(issued)  # 单调推进（跨段跨实例）


def test_memory_only_buffer_unchanged():
    buf = JobEventBuffer()  # db_path=None：旧行为
    assert buf.record("ws1", "job1", "updated") == 1
    assert buf.record("ws1", "job2", "created") == 2
    assert buf.drain_compacted().latest_revision == 2


def test_overflow_resync_semantics_unchanged(db_path):
    baseline = _read_seq_value(db_path)
    buf = JobEventBuffer(db_path=db_path, max_events=2)
    buf.record("ws1", "job1", "updated")
    buf.record("ws2", "job2", "updated")
    buf.record("ws3", "job3", "updated")  # 挤掉 ws1 的事件
    compacted = buf.drain_compacted()
    assert compacted.resync_workspace_ids == {"ws1"}
    assert compacted.latest_revision == baseline + 3


def test_db_revision_retries_on_transaction_conflict(db_path, monkeypatch):
    from psycopg.errors import SerializationFailure

    from server.app.db.transaction import write_transaction as real_write_transaction

    baseline = _read_seq_value(db_path)
    calls = 0

    def flaky_write_transaction(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SerializationFailure("serialization failure")
        return real_write_transaction(path)

    monkeypatch.setattr(buffer_module, "write_transaction", flaky_write_transaction)
    buf = JobEventBuffer(db_path=db_path)
    assert buf.record("ws1", "job1", "updated") == baseline + 1
    assert calls == 2
