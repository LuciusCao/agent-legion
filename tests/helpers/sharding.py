"""Shared shard fan-out end-to-end scaffolding.

The unit tier (``tests/workflows/test_sharding.py``) and the full-gate
evidence (``tests/full/test_shard_fanout_e2e.py``) drive the same
fake-executor + real-leases + real-worker setup. It lives here so the
full-gate file does not import a unit test module.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from server.app.db.transaction import read_connection
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.jobs import JobQueries
from server.app.jobs.storage_layout import job_storage_dir
from server.app.workflows.definition import WorkflowNode
from server.app.workflows.schema import WorkflowReduceSpec, WorkflowShardSpec
from tests.helpers.executor_worker import make_definition, make_worker
from tests.postgres_support import TEST_DATABASE_URL


class FakeShardExecutor:
    """Records contexts; writes parse inputs; returns per-shard output_json."""

    kind = "code"

    def __init__(
        self,
        executor_id: str = "code-default",
        gate: threading.Event | None = None,
        fail_shards: set[int] | None = None,
        parse_items: list | None = None,
    ) -> None:
        self.id = executor_id
        self._gate = gate
        self._fail_shards = fail_shards or set()
        self._parse_items = parse_items
        self.contexts: list[ExecutionContext] = []
        self.merged_shards: list | None = None
        self._lock = threading.Lock()

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        with self._lock:
            self.contexts.append(context)
        if context.node_key == "parse":
            items = (
                self._parse_items if self._parse_items is not None else [{"q": i} for i in range(4)]
            )
            (context.job_dir / "questions.json").write_text(json.dumps(items), encoding="utf-8")
            return ExecutionResult(status="completed", exit_code=0)
        if context.node_key == "aggregate":
            shards_path = context.job_dir / f"{context.node_key}.shards.json"
            with self._lock:
                self.merged_shards = json.loads(shards_path.read_text(encoding="utf-8"))
            return ExecutionResult(status="completed", exit_code=0)
        shard_index = int(context.runtime["shard_index"])
        if self._gate is not None and not self._gate.wait(timeout=30):
            raise RuntimeError("gate was not released in time")
        if shard_index in self._fail_shards:
            return ExecutionResult(
                status="failed", exit_code=1, error_message=f"shard {shard_index} failed"
            )
        return ExecutionResult(
            status="completed",
            exit_code=0,
            output_json=json.dumps({"shard": shard_index, "input": context.runtime["shard_input"]}),
        )

    def cancel(self, execution_id: str) -> None:
        pass


def over_definition():
    return make_definition(
        [
            WorkflowNode(
                key="parse", label="parse", capability="parse", outputs=["questions.json"]
            ),
            WorkflowNode(
                key="review",
                label="review",
                capability="review",
                after=["parse"],
                inputs=["questions.json"],
                outputs=["review.json"],
                shard=WorkflowShardSpec(over="inputs.questions.json"),
            ),
            WorkflowNode(
                key="aggregate",
                label="aggregate",
                capability="merge",
                after=["review"],
                outputs=["merged.json"],
                reduce=WorkflowReduceSpec(from_node="review"),
            ),
        ]
    )


def make_e2e(tmp_path: Path, definition, executor, *, capacity: int = 10):
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    workspace = job_db.create_workspace("ws", default_workflow_key="test")
    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="src-1",
        run_id="",
        title="t",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )
    worker = make_worker(tmp_path, db_path, executor, [definition], code_capacity=capacity)
    job_dir = job_storage_dir(tmp_path / "jobs", workspace["id"], str(job["id"]))
    return worker, job_db, job, job_dir


def poll_until(worker, predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        worker._poll()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def node_status(job_db: JobQueries, job_id: str, node_key: str) -> str | None:
    node = job_db.get_job_node(job_id, node_key)
    return str(node["status"]) if node else None


def node_shards(db_path: Path, job_id: str, node_key: str) -> list[dict]:
    with read_connection(db_path) as conn:
        rows = conn.execute(
            "select * from node_shards where job_id=%s and node_key=%s order by shard_index",
            (job_id, node_key),
        ).fetchall()
    return [dict(row) for row in rows]
