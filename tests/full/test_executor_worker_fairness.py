from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.jobs import JobQueries
from server.app.workflow_worker.thread import WorkflowWorkerThread
from tests.helpers.executor_worker import (
    local_node,
    make_definition,
    make_worker,
)
from tests.postgres_support import TEST_DATABASE_URL


class PerWorkspaceBlockingExecutor:
    """Fake code executor that blocks on a per-workspace event."""

    kind = "code"

    def __init__(self, executor_id: str, events: dict[str, threading.Event]) -> None:
        self.id = executor_id
        self._events = events
        self.contexts: list[ExecutionContext] = []
        self._cancelled: set[str] = set()

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        self.contexts.append(context)
        event = self._events[context.workspace_id]
        if not event.wait(timeout=30):
            raise RuntimeError(f"workspace {context.workspace_id} was not released in time")
        for output in context.expected_outputs:
            (context.job_dir / output).write_text('{"done": true}', encoding="utf-8")
        return ExecutionResult(
            status="completed",
            exit_code=0,
            produced_artifacts=tuple(context.expected_outputs),
        )

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)


def _active_counts(worker: WorkflowWorkerThread, executor_id: str) -> dict[str, int]:
    return worker.leases.active_counts(executor_id)


def _poll_counts_until(worker, predicate, timeout: float = 15.0) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    counts: dict[str, int] = {}
    while time.monotonic() < deadline:
        worker._poll()
        counts = _active_counts(worker, "code")
        if predicate(counts):
            return counts
        time.sleep(0.01)
    raise AssertionError(f"condition not met within {timeout}s; last counts: {counts}")


@pytest.mark.full_gate
def test_shared_capacity_and_round_robin_fairness(tmp_path: Path) -> None:
    """EXEC-FAIRNESS-001（P-0.5 语义收窄）：单一隐含 code 池下，workspace 间
    公平只靠调度 round-robin —— 不再有 per-workspace 容量隔离，一个 backlog
    深的 workspace 可以吃满全局容量；round-robin 保证新到的 workspace 在少量
    pass 内拿到认领，全局容量永不被突破。
    """
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")

    ws_a = job_db.create_workspace("Workspace A", default_workflow_key="demo_workflow")
    ws_b = job_db.create_workspace("Workspace B", default_workflow_key="demo_workflow")
    ws_c = job_db.create_workspace("Workspace C", default_workflow_key="demo_workflow")

    events = {
        ws_a["id"]: threading.Event(),
        ws_b["id"]: threading.Event(),
        ws_c["id"]: threading.Event(),
    }
    executor = PerWorkspaceBlockingExecutor("code", events=events)
    definition = make_definition([local_node("fetch")])

    def _add_jobs(ws: dict, count: int, prefix: str) -> None:
        for i in range(count):
            job_db.create_job(
                workflow_key="test",
                source_type="question",
                source_id=f"{prefix}{i}",
                batch_id="",
                title=f"{prefix}{i}",
                node_keys=["fetch"],
                workspace_id=ws["id"],
            )

    _add_jobs(ws_a, 8, "A")
    worker = make_worker(tmp_path, db_path, executor, [definition], code_capacity=10)

    # 只有 A 在 backlog：单池下 A 吃满自己的 8 个 job（无 workspace 上限）。
    counts = _poll_counts_until(worker, lambda c: c.get(ws_a["id"], 0) == 8)
    assert counts.get("global", 0) == 8

    _add_jobs(ws_b, 10, "B")
    _add_jobs(ws_c, 10, "C")

    c_started = False
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        worker._poll()
        counts = _active_counts(worker, "code")
        # 全局容量永不突破；A 的租约未释放前一直占着 8 个。
        assert counts.get("global", 0) <= 10
        if counts.get(ws_c["id"], 0) >= 1:
            c_started = True
        if counts.get("global", 0) == 10:
            break
        time.sleep(0.01)

    assert c_started, "round-robin 必须让 workspace C 在少量 pass 内拿到认领"
    counts = _active_counts(worker, "code")
    assert counts.get(ws_a["id"], 0) == 8
    assert counts.get("global", 0) == 10

    # 放行 A：租约释放后 B/C 经 round-robin 填满全局容量。
    events[ws_a["id"]].set()

    def refilled(counts: dict[str, int]) -> bool:
        assert counts.get("global", 0) <= 10
        return (
            counts.get(ws_a["id"], 0) == 0
            and counts.get("global", 0) == 10
            and counts.get(ws_b["id"], 0) >= 1
            and counts.get(ws_c["id"], 0) >= 1
        )

    counts = _poll_counts_until(worker, refilled)
    assert counts.get(ws_b["id"], 0) >= 1
    assert counts.get(ws_c["id"], 0) >= 1

    events[ws_b["id"]].set()
    events[ws_c["id"]].set()
    worker.stop()
