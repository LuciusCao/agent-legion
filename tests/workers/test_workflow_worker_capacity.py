from __future__ import annotations

import threading
from pathlib import Path

import pytest

from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.jobs import JobQueries
from tests.helpers.executor_worker import (
    local_node,
    make_definition,
    make_worker,
)
from tests.postgres_support import TEST_DATABASE_URL


class BlockingExecutor:
    """Fake code executor that blocks until released."""

    kind = "code"

    def __init__(self, executor_id: str, block_event: threading.Event) -> None:
        self.id = executor_id
        self.block_event = block_event

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        assert self.block_event.wait(timeout=10), "executor was not released in time"
        for output in context.expected_outputs:
            (context.job_dir / output).write_text('{"done": true}', encoding="utf-8")
        return ExecutionResult(status="completed", exit_code=0)

    def cancel(self, execution_id: str) -> None:
        pass


def _setup(
    tmp_path: Path,
    capacity: int,
    job_count: int,
    node_limit: int | None = None,
):
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")
    block_event = threading.Event()
    executor = BlockingExecutor("code", block_event)
    definition = make_definition([local_node("fetch")])
    for i in range(job_count):
        job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id=f"Q{i}",
            run_id="",
            title=f"Q{i}",
            node_keys=["fetch"],
            workspace_id=ws["id"],
        )
    if node_limit is not None:
        with job_db.connect() as conn:
            conn.execute(
                "insert into workspace_node_limits(workspace_id, workflow_key, node_key,"
                " concurrency_limit) values (%s, 'test', 'fetch', %s)",
                (ws["id"], node_limit),
            )
    worker = make_worker(tmp_path, db_path, executor, [definition], code_capacity=capacity)
    return worker, ws, block_event


def _count_calls(monkeypatch: pytest.MonkeyPatch, obj: object, attr: str) -> dict[str, int]:
    calls = {"count": 0}
    real = getattr(obj, attr)

    def spy(*args, **kwargs):
        calls["count"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(obj, attr, spy)
    return calls


def test_saturated_executor_skips_scan_and_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The saturated fast path requires zero agent work anywhere: catalogs are
    # workspace-scoped (schema v46), so no seeded Agents exist at all here.
    worker, _ws, block_event = _setup(tmp_path, capacity=1, job_count=2)

    assert worker._poll() is True
    assert worker.leases.active_counts("code")["global"] == 1

    scan_calls = _count_calls(monkeypatch, worker, "_runnable_workspaces")
    claim_calls = _count_calls(monkeypatch, worker.leases, "try_claim_many")

    assert worker._poll() is False
    assert scan_calls["count"] == 0
    assert claim_calls["count"] == 0
    assert worker.leases.active_counts("code")["global"] == 1

    block_event.set()
    worker.stop()


def test_multi_claim_uses_single_scan_per_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, _ws, block_event = _setup(tmp_path, capacity=2, job_count=2)

    scan_calls = _count_calls(monkeypatch, worker, "_runnable_workspaces")

    assert worker._poll() is True
    assert scan_calls["count"] == 1
    assert worker.leases.active_counts("code")["global"] == 2
    assert len(worker.state.futures) == 2

    block_event.set()
    worker.stop()


def test_global_capacity_precheck_allows_exactly_one_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, ws, block_event = _setup(tmp_path, capacity=1, job_count=3)

    claim_calls = _count_calls(monkeypatch, worker.leases, "try_claim_many")

    assert worker._poll() is True
    assert claim_calls["count"] == 1
    _assert_one_running_two_pending(worker, ws["id"])

    block_event.set()
    worker.stop()


def test_node_limit_precheck_allows_exactly_one_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P-0.5: the per-node concurrency limit is the only sub-pool ceiling."""
    worker, ws, block_event = _setup(tmp_path, capacity=3, job_count=3, node_limit=1)

    claim_calls = _count_calls(monkeypatch, worker.leases, "try_claim_many")

    assert worker._poll() is True
    assert claim_calls["count"] == 1
    _assert_one_running_two_pending(worker, ws["id"])

    block_event.set()
    worker.stop()


def _assert_one_running_two_pending(worker, workspace_id: str) -> None:
    assert worker.leases.active_counts("code")["global"] == 1
    statuses = [
        node["status"]
        for job in worker.job_db.list_jobs(workspace_id=workspace_id, workflow_key="test")
        for node in worker.job_db.list_job_nodes(job["id"])
    ]
    assert statuses.count("running") == 1
    assert statuses.count("pending") == 2
