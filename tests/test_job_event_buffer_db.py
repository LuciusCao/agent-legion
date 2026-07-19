from __future__ import annotations

from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.job_events import JobEventBuffer


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.sqlite"
    init_db(path)
    return path


def test_revision_continues_across_instances(db_path):
    a = JobEventBuffer(db_path=db_path)
    revisions = [a.record("ws1", f"job{i}", "updated") for i in range(5)]
    assert revisions == [1, 2, 3, 4, 5]

    b = JobEventBuffer(db_path=db_path)  # 模拟进程重启
    assert b.record("ws1", "job5", "updated") == 6
    compacted = b.drain_compacted()
    assert compacted.latest_revision == 6


def test_revision_unique_across_concurrent_instances(db_path):
    import threading

    barriers = threading.Barrier(4)
    issued: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        buf = JobEventBuffer(db_path=db_path)
        barriers.wait(5)
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
    assert len(set(issued)) == 40  # 无重复


def test_memory_only_buffer_unchanged():
    buf = JobEventBuffer()  # db_path=None：旧行为
    assert buf.record("ws1", "job1", "updated") == 1
    assert buf.record("ws1", "job2", "created") == 2
    assert buf.drain_compacted().latest_revision == 2


def test_overflow_resync_semantics_unchanged(db_path):
    buf = JobEventBuffer(db_path=db_path, max_events=2)
    buf.record("ws1", "job1", "updated")
    buf.record("ws2", "job2", "updated")
    buf.record("ws3", "job3", "updated")  # 挤掉 ws1 的事件
    compacted = buf.drain_compacted()
    assert compacted.resync_workspace_ids == {"ws1"}
    assert compacted.latest_revision == 3
