from __future__ import annotations

import random
import threading
from pathlib import Path

import pytest

from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.jobs import JobQueries
from tests.helpers.executor_worker import (
    allocate,
    bind,
    local_def,
    local_node,
    make_definition,
    make_registry,
    make_worker,
)


class BlockingExecutor:
    """Fake local executor that blocks on a single event."""

    kind = "local"

    def __init__(self, executor_id: str, block_event: threading.Event) -> None:
        self.id = executor_id
        self.block_event = block_event
        self.contexts: list[ExecutionContext] = []
        self._cancelled: set[str] = set()

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        self.contexts.append(context)
        if not self.block_event.wait(timeout=30):
            raise RuntimeError("executor was not released in time")
        for output in context.expected_outputs:
            (context.job_dir / output).write_text('{"done": true}', encoding="utf-8")
        return ExecutionResult(
            status="completed",
            exit_code=0,
            produced_artifacts=tuple(context.expected_outputs),
        )

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)


@pytest.mark.ci_extended
@pytest.mark.parametrize("seed", range(25, 50))
def test_fairness_under_randomized_insertion_order(tmp_path: Path, seed: int) -> None:
    """Repeat fairness semantic checks across randomized job insertion order."""
    db_path = tmp_path / "video_hive.sqlite"
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

    block_event = threading.Event()
    executor = BlockingExecutor("local-default", block_event=block_event)
    registry = make_registry(
        {"local-default": executor},
        {"local-default": local_def(10, {"fetch"})},
    )
    definition = make_definition([local_node("fetch")])

    workspaces = {
        "A": ws_a,
        "B": ws_b,
        "C": ws_c,
    }
    limits = {
        ws_a["id"]: 8,
        ws_b["id"]: 6,
        ws_c["id"]: 2,
    }
    for ws in workspaces.values():
        allocate(job_db, ws["id"], "local-default", limits[ws["id"]])
        bind(job_db, ws["id"], "test", "fetch", "local-default")

    jobs_per_workspace = 4
    jobs: list[tuple[str, str]] = []
    for label, ws in workspaces.items():
        for i in range(jobs_per_workspace):
            jobs.append((ws["id"], f"{label}{i}"))

    random.Random(seed).shuffle(jobs)
    for workspace_id, source_id in jobs:
        job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id=source_id,
            batch_id="",
            title=source_id,
            node_keys=["fetch"],
            workspace_id=workspace_id,
        )

    worker = make_worker(tmp_path, db_path, registry, [definition])

    for _ in range(30):
        worker._poll()
        counts = worker.leases.active_counts("local-default")
        assert counts.get("global", 0) <= 10
        for ws in workspaces.values():
            assert counts.get(ws["id"], 0) <= limits[ws["id"]]
        if counts.get("global", 0) == 10:
            break

    counts = worker.leases.active_counts("local-default")
    assert counts.get("global", 0) <= 10
    for ws in workspaces.values():
        assert counts.get(ws["id"], 0) <= limits[ws["id"]]
        assert counts.get(ws["id"], 0) >= 1, f"workspace {ws['id']} was starved with seed {seed}"

    block_event.set()
    worker.stop()
