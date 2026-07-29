from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.jobs import JobQueries
from server.app.workflow_worker.thread import WorkflowWorkerThread
from tests.helpers.executor_worker import (
    allocate,
    bind,
    local_def,
    local_node,
    make_definition,
    make_registry,
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
        counts = _active_counts(worker, "code-default")
        if predicate(counts):
            return counts
        time.sleep(0.01)
    raise AssertionError(f"condition not met within {timeout}s; last counts: {counts}")


@pytest.mark.full_gate
def test_shared_capacity_and_bounded_fairness(tmp_path: Path) -> None:
    """Real worker, real leases, and event-controlled executors prove bounded fairness.

    Three workspaces share one executor with global capacity 10 and workspace
    limits A=8, B=6, C=2.  Workspace A is saturated first, then B and C are
    introduced while A remains backlogged.  The scheduler must:

    * keep total active leases at or below the global capacity;
    * respect each workspace allocation;
    * give workspace C at least one claim within a small number of polls;
    * allow workspace B to grow toward its allocation once A releases capacity.
    """
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")

    ws_a = job_db.create_workspace(
        "Workspace A", default_workflow_key="question_comprehension_info"
    )
    ws_b = job_db.create_workspace(
        "Workspace B", default_workflow_key="question_comprehension_info"
    )
    ws_c = job_db.create_workspace(
        "Workspace C", default_workflow_key="question_comprehension_info"
    )

    events = {
        ws_a["id"]: threading.Event(),
        ws_b["id"]: threading.Event(),
        ws_c["id"]: threading.Event(),
    }
    executor = PerWorkspaceBlockingExecutor("code-default", events=events)
    registry = make_registry(
        {"code-default": executor},
        {"code-default": local_def(10, {"fetch"})},
    )
    definition = make_definition([local_node("fetch")])

    for ws, limit in [(ws_a, 8), (ws_b, 6), (ws_c, 2)]:
        allocate(job_db, ws["id"], "code-default", limit)
        bind(job_db, ws["id"], "test", "fetch", "code-default")

    for i in range(8):
        job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id=f"A{i}",
            batch_id="",
            title=f"A{i}",
            node_keys=["fetch"],
            workspace_id=ws_a["id"],
        )

    worker = make_worker(tmp_path, db_path, registry, [definition])

    counts = _poll_counts_until(worker, lambda c: c.get(ws_a["id"], 0) == 8)

    counts = _active_counts(worker, "code-default")
    assert counts.get("global", 0) == 8
    assert counts.get(ws_a["id"], 0) == 8
    assert counts.get(ws_b["id"], 0) == 0
    assert counts.get(ws_c["id"], 0) == 0

    for i in range(10):
        for ws in (ws_b, ws_c):
            job_db.create_job(
                workflow_key="test",
                source_type="question",
                source_id=f"{ws['id']}_{i}",
                batch_id="",
                title=f"{ws['id']}_{i}",
                node_keys=["fetch"],
                workspace_id=ws["id"],
            )

    c_started = False
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        worker._poll()
        counts = _active_counts(worker, "code-default")
        assert counts.get("global", 0) <= 10
        assert counts.get(ws_a["id"], 0) <= 8
        assert counts.get(ws_b["id"], 0) <= 6
        assert counts.get(ws_c["id"], 0) <= 2
        if counts.get(ws_c["id"], 0) >= 1:
            c_started = True
        if counts.get("global", 0) == 10:
            break
        time.sleep(0.01)

    assert c_started, "workspace C must start within 5 scheduling passes"

    counts = _active_counts(worker, "code-default")
    assert counts.get(ws_a["id"], 0) == 8
    assert counts.get("global", 0) == 10
    assert counts.get(ws_b["id"], 0) <= 2
    assert counts.get(ws_c["id"], 0) <= 2

    events[ws_a["id"]].set()

    def allocations_reached(counts: dict[str, int]) -> bool:
        assert counts.get("global", 0) <= 10
        assert counts.get(ws_a["id"], 0) <= 8
        assert counts.get(ws_b["id"], 0) <= 6
        assert counts.get(ws_c["id"], 0) <= 2
        return (
            counts.get(ws_b["id"], 0) == 6
            and counts.get(ws_c["id"], 0) == 2
            and counts.get(ws_a["id"], 0) == 0
        )

    counts = _poll_counts_until(worker, allocations_reached)

    counts = _active_counts(worker, "code-default")
    assert counts.get(ws_a["id"], 0) == 0
    assert counts.get(ws_b["id"], 0) == 6
    assert counts.get(ws_c["id"], 0) == 2
    assert counts.get("global", 0) == 8

    events[ws_b["id"]].set()
    events[ws_c["id"]].set()
    worker.stop()
