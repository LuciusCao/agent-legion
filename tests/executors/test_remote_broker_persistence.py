from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.executors.remote_broker import (
    RemoteExecutionBroker,
    RemoteExecutionPayload,
    RemoteOutcome,
)


def _payload(execution_id: str, capability: str = "cap_a") -> RemoteExecutionPayload:
    return RemoteExecutionPayload(
        execution_id=execution_id,
        lease_id=f"lease-{execution_id}",
        job_id="job1",
        node_key="node_a",
        capability=capability,
        bundle_name=f"{execution_id}.tar.gz",
        manifest={"job_id": "job1"},
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.sqlite"
    init_db(path)
    return path


def _broker(db_path: Path, tmp_path: Path, **kwargs) -> RemoteExecutionBroker:
    kwargs.setdefault("claim_timeout_seconds", 60.0)
    return RemoteExecutionBroker(db_path, tmp_path / "bundles", **kwargs)


def test_queue_survives_broker_restart(db_path, tmp_path):
    broker = _broker(db_path, tmp_path)
    broker.submit(_payload("e1"))
    broker.submit(_payload("e2"))
    assert broker.dequeue("w1", {"cap_a"}).execution_id == "e1"

    restarted = _broker(db_path, tmp_path)
    # claimed 的 e1 未超时，不可被重抢；queued 的 e2 可见。
    assert restarted.dequeue("w2", {"cap_a"}).execution_id == "e2"
    assert restarted.dequeue("w2", {"cap_a"}) is None


def test_completed_outcome_survives_restart(db_path, tmp_path):
    broker = _broker(db_path, tmp_path)
    broker.submit(_payload("e1"))
    broker.dequeue("w1", {"cap_a"})
    outcome = RemoteOutcome(status="completed", exit_code=0)
    assert broker.complete("e1", "w1", outcome)

    restarted = _broker(db_path, tmp_path)
    assert restarted.wait_result("e1", poll_seconds=0.05).status == "completed"


def test_concurrent_dequeue_only_one_wins(db_path, tmp_path):
    broker = _broker(db_path, tmp_path)
    broker.submit(_payload("e1"))
    winners: list[str] = []
    barrier = threading.Barrier(2)

    def try_dequeue(worker_id: str) -> None:
        barrier.wait(5)
        # 每个 worker 用独立 broker 实例模拟多进程/多连接并发。
        claim = _broker(db_path, tmp_path).dequeue(worker_id, {"cap_a"})
        if claim is not None:
            winners.append(worker_id)

    threads = [threading.Thread(target=try_dequeue, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(winners) == 1


def test_dequeue_respects_worker_slots(db_path, tmp_path):
    broker = _broker(db_path, tmp_path)
    broker.register_worker("w1", "w1", ["cap_a"], slots=1)
    broker.submit(_payload("e1"))
    broker.submit(_payload("e2"))
    assert broker.dequeue("w1", {"cap_a"}).execution_id == "e1"
    # 已持 1 个 claim = slots 上限，不再派发。
    assert broker.dequeue("w1", {"cap_a"}) is None
    # 完成后释放额度。
    broker.complete("e1", "w1", RemoteOutcome(status="completed", exit_code=0))
    assert broker.dequeue("w1", {"cap_a"}).execution_id == "e2"


def test_dequeue_without_registration_treated_as_one_slot(db_path, tmp_path):
    # 兼容路径：测试/旧流程不注册 worker 也能 dequeue（现行行为无 slots 检查）。
    broker = _broker(db_path, tmp_path)
    broker.submit(_payload("e1"))
    assert broker.dequeue("ghost", {"cap_a"}) is not None


def test_stale_claim_requeued_from_db(db_path, tmp_path):
    now = datetime.now(UTC)
    clock = {"t": now}
    broker = _broker(db_path, tmp_path, time_source=lambda: clock["t"])
    broker.submit(_payload("e1"))
    broker.dequeue("w1", {"cap_a"})
    clock["t"] = now + timedelta(seconds=120)
    # 另一实例 sweep 后可见 e1 重新可抢。
    other = _broker(db_path, tmp_path, time_source=lambda: clock["t"])
    assert other.dequeue("w2", {"cap_a"}).execution_id == "e1"


def test_done_entries_cleaned_up(db_path, tmp_path):
    now = datetime.now(UTC)
    clock = {"t": now}
    broker = _broker(db_path, tmp_path, time_source=lambda: clock["t"])
    broker.submit(_payload("e1"))
    broker.dequeue("w1", {"cap_a"})
    broker.complete("e1", "w1", RemoteOutcome(status="completed", exit_code=0))
    clock["t"] = now + timedelta(hours=25)
    broker.submit(_payload("e2"))  # 任意写操作触发清理
    from server.app.db.transaction import read_connection

    with read_connection(db_path) as conn:
        rows = conn.execute("select execution_id from remote_executions").fetchall()
    assert [r["execution_id"] for r in rows] == ["e2"]
