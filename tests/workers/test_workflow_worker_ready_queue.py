from __future__ import annotations

import threading
from pathlib import Path

import pytest

from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.jobs import JobQueries
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.worker_control import WorkspaceWorkerControl
from server.app.workflow_worker import ready_cache as workflow_worker_ready_cache
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
    node_keys: list[str],
    capacity: int,
    job_count: int,
):
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")
    block_event = threading.Event()
    executor = BlockingExecutor("code", block_event)
    definition = make_definition([local_node(key) for key in node_keys])
    snapshot_json = serialize_definition(definition)
    snapshot_hash = definition_hash(snapshot_json)
    for i in range(job_count):
        job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id=f"Q{i}",
            run_id="",
            title=f"Q{i}",
            node_keys=node_keys,
            workspace_id=ws["id"],
            workflow_definition_hash=snapshot_hash,
            workflow_definition_snapshot_json=snapshot_json,
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


def test_evaluate_once_per_job_per_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One pass claims 3 nodes while each job is evaluated at most once.

    Snapshot parsing is cached by workflow_definition_hash, so the three jobs
    (sharing one definition) trigger exactly one parse per pass.
    """
    worker, _ws, block_event = _setup(tmp_path, ["fetch"], capacity=3, job_count=3)

    loader_calls = _count_calls(monkeypatch, worker.job_db, "list_job_nodes_for_jobs")
    snapshot_calls = _count_calls(
        monkeypatch, workflow_worker_ready_cache, "definition_from_job_snapshot"
    )

    assert worker._poll() is True
    assert worker.leases.active_counts("code")["global"] == 3
    assert len(worker.state.futures) == 3
    assert loader_calls["count"] == 1
    assert snapshot_calls["count"] == 1

    block_event.set()
    worker.stop()


def test_execution_control_error_yields_no_candidates_without_killing_pass(
    tmp_path: Path, caplog
) -> None:
    """#204: ExecutionControlError is the expected business failure — the job
    yields no ready nodes (WARNING with the control snapshot) and the pass
    keeps going. A programming error in allowed_nodes, by contrast, is NOT
    swallowed here: it propagates to the poll-loop safety net (next test)."""
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")
    block_event = threading.Event()
    executor = BlockingExecutor("code", block_event)
    definition = make_definition([local_node("fetch")])
    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    # until_node with a target that is not in the definition: the business
    # failure the ready evaluation must contain per job.
    job_db.set_job_execution_mode(job["id"], "until_node", target_node_key="no-such-node")
    worker = make_worker(tmp_path, db_path, executor, [definition], code_capacity=2)

    with caplog.at_level("WARNING", logger="server.app.workflow_worker.ready_cache"):
        processed = worker._poll()

    # The pass completed (no raise) and the job produced no claims.
    assert processed is False
    assert len(worker.state.futures) == 0
    assert any("invalid execution control" in rec.message for rec in caplog.records)
    block_event.set()
    worker.stop()


def test_programming_error_in_ready_evaluation_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#204 layering guard: only the ExecutionControlError business failure
    is contained per job. A genuine programming error from allowed_nodes
    propagates — it must reach the poll-loop safety net instead of silently
    dropping the job."""
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")
    block_event = threading.Event()
    executor = BlockingExecutor("code", block_event)
    definition = make_definition([local_node("fetch")])
    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    worker = make_worker(tmp_path, db_path, executor, [definition], code_capacity=2)

    def exploding_allowed(*args, **kwargs):
        raise TypeError("programmer mistake")

    monkeypatch.setattr("server.app.workflow_worker.ready_cache.allowed_nodes", exploding_allowed)

    with pytest.raises(TypeError, match="programmer mistake"):
        worker._poll()

    block_event.set()
    worker.stop()


def test_no_per_job_node_status_queries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The poll pass batches node-status loads instead of one query per job."""
    worker, _ws, block_event = _setup(tmp_path, ["fetch"], capacity=2, job_count=3)
    node_calls = _count_calls(monkeypatch, worker.job_db, "list_job_nodes")

    assert worker._poll() is True
    assert worker.leases.active_counts("code")["global"] == 2
    assert node_calls["count"] == 0

    block_event.set()
    worker.stop()


def test_paused_workspace_skipped_at_scan(tmp_path: Path) -> None:
    """Paused workspaces contribute no jobs; other workspaces still schedule."""
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws_a = job_db.create_workspace("WS A", default_workflow_key="demo_workflow")
    ws_b = job_db.create_workspace("WS B", default_workflow_key="demo_workflow")
    block_event = threading.Event()
    executor = BlockingExecutor("code", block_event)
    definition = make_definition([local_node("fetch")])
    for ws, prefix in ((ws_a, "A"), (ws_b, "B")):
        job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id=f"{prefix}0",
            run_id="",
            title=f"{prefix}0",
            node_keys=["fetch"],
            workspace_id=ws["id"],
        )
    worker = make_worker(tmp_path, db_path, executor, [definition], code_capacity=4)
    control = WorkspaceWorkerControl()
    control.pause(ws_a["id"])
    control.resume(ws_b["id"])
    worker.workspace_worker_control = control

    workspace_ids, jobs_by_workspace = worker._runnable_workspaces()
    assert workspace_ids == [ws_b["id"]]
    assert [job["source_id"] for _, job in jobs_by_workspace[ws_b["id"]]] == ["B0"]

    assert worker._poll() is True
    counts = worker.leases.active_counts("code")
    assert counts.get(ws_a["id"], 0) == 0
    assert counts.get(ws_b["id"], 0) == 1

    block_event.set()
    worker.stop()


def test_multi_ready_node_job_claimed_within_one_pass(tmp_path: Path) -> None:
    """A job with two independent ready nodes has both claimed in one pass."""
    worker, ws, block_event = _setup(tmp_path, ["left", "right"], capacity=2, job_count=1)

    assert worker._poll() is True
    assert worker.leases.active_counts("code")["global"] == 2
    assert len(worker.state.futures) == 2
    job = worker.job_db.list_jobs(workspace_id=ws["id"], workflow_key="test")[0]
    statuses = {
        node["node_key"]: node["status"] for node in worker.job_db.list_job_nodes(job["id"])
    }
    assert statuses == {"left": "running", "right": "running"}

    block_event.set()
    worker.stop()
