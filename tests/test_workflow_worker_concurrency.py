from __future__ import annotations

import threading
from pathlib import Path

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import (
    ExecutionContext,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)
from tests.postgres_support import TEST_DATABASE_URL


class FakeExecutor:
    """Test executor that blocks on an event and writes declared outputs."""

    def __init__(
        self,
        executor_id: str,
        kind: str = "code",
        *,
        block_event: threading.Event | None = None,
        supports: set[str] | None = None,
    ) -> None:
        self.id = executor_id
        self.kind = kind
        self.block_event = block_event or threading.Event()
        self._supports = supports or set()
        self.contexts: list[ExecutionContext] = []
        self._cancelled: set[str] = set()

    def supports(self, capability: str) -> bool:
        if not self._supports:
            return True
        return capability in self._supports

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        self.contexts.append(context)
        assert self.block_event.wait(timeout=10), "executor was not released in time"
        for output in context.expected_outputs:
            (context.job_dir / output).write_text('{"done": true}', encoding="utf-8")
        return ExecutionResult(
            status="completed",
            exit_code=0,
            produced_artifacts=tuple(context.expected_outputs),
        )

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)


def _make_definition(nodes: list[WorkflowNode]) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={n.key: n for n in nodes},
    )


def _local_node(key: str, outputs: list[str] | None = None) -> WorkflowNode:
    return WorkflowNode(
        key=key,
        label=key,
        capability=key,
        outputs=outputs or ["output.json"],
    )


def _set_node_limit(
    job_db: JobQueries,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    concurrency_limit: int,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit)
            values (%s, %s, %s, %s)
            on conflict(workspace_id, workflow_key, node_key) do update set concurrency_limit=excluded.concurrency_limit
            """,
            (workspace_id, workflow_key, node_key, concurrency_limit),
        )


def _make_worker(
    tmp_path: Path,
    db_path: Path,
    executor: FakeExecutor,
    definitions: list[WorkflowDefinition],
    *,
    code_capacity: int = 16,
    seed_code: bool = True,
) -> WorkflowWorkerThread:
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    leases = ExecutorLeaseRepository(db_path, data_dir=tmp_path)
    runtime = ExecutionRuntime(
        leases=leases,
        executor=executor,
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=5,
    )
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={"workflows": {"enabled": True}},
        database_url=str(db_path),
        executor_runtime=ExecutorRuntimeConfig.model_validate(
            {
                "workflows": {"enabled": True},
                "openclaw": {"command_template": ["openclaw"]},
                "code_capacity": code_capacity,
            }
        ),
    )
    worker = WorkflowWorkerThread(
        job_db=job_db,
        leases=leases,
        runtime=runtime,
        settings=settings,
    )
    # Post-#96 every code node needs published code to dispatch; the
    # FakeExecutor never reads the text, so a global no-op seed is enough.
    if seed_code:
        from server.app.services.node_codes import NodeCodeService

        codes = NodeCodeService(str(db_path))
        for definition in definitions:
            for node in definition.nodes.values():
                codes.seed_global(
                    definition.key,
                    node.key,
                    "def run(job, job_dir, runtime):\n    pass\n",
                    "test seed",
                )
    worker._scan_entries = (definitions, [])
    return worker


def test_same_node_submitted_once(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")

    block_event = threading.Event()
    executor = FakeExecutor("code", block_event=block_event)
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )

    worker = _make_worker(tmp_path, db_path, executor, [definition], code_capacity=2)
    worker._poll()

    assert worker.leases.active_counts("code").get("global", 0) == 1
    assert len(worker._futures) == 1

    worker._poll()
    assert worker.leases.active_counts("code").get("global", 0) == 1
    assert len(worker._futures) == 1

    block_event.set()
    worker.stop()


def test_global_capacity_not_exceeded_across_workers(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")

    block_event = threading.Event()
    executor = FakeExecutor("code", block_event=block_event)
    definition = _make_definition([_local_node("fetch")])

    for i in range(2):
        job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id=f"Q{i}",
            batch_id="",
            title=f"Q{i}",
            node_keys=["fetch"],
            workspace_id=ws["id"],
        )

    worker1 = _make_worker(tmp_path, db_path, executor, [definition], code_capacity=1)
    worker2 = _make_worker(tmp_path, db_path, executor, [definition], code_capacity=1)

    worker1._poll()
    worker2._poll()

    total = worker1.leases.active_counts("code").get("global", 0)
    assert total == 1

    block_event.set()
    worker1.stop()
    worker2.stop()


def test_local_node_limits_are_workspace_specific(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws_a = job_db.create_workspace("Workspace A", default_workflow_key="demo_workflow")
    ws_b = job_db.create_workspace("Workspace B", default_workflow_key="demo_workflow")

    block_event = threading.Event()
    executor = FakeExecutor("code", block_event=block_event)
    definition = _make_definition([_local_node("fetch")])

    for workspace in [ws_a, ws_b]:
        for i in range(2):
            job_db.create_job(
                workflow_key="test",
                source_type="question",
                source_id=f"{workspace['id']}_{i}",
                batch_id="",
                title=f"{workspace['id']}_{i}",
                node_keys=["fetch"],
                workspace_id=workspace["id"],
            )
    _set_node_limit(job_db, ws_a["id"], "test", "fetch", 1)
    _set_node_limit(job_db, ws_b["id"], "test", "fetch", 2)

    worker = _make_worker(tmp_path, db_path, executor, [definition], code_capacity=4)
    worker._poll()

    counts = worker.leases.active_counts("code")
    assert counts.get("global", 0) == 3
    assert counts.get(ws_a["id"], 0) == 1
    assert counts.get(ws_b["id"], 0) == 2

    block_event.set()
    worker.stop()


def test_round_robin_allows_small_workspace_to_claim(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws_a = job_db.create_workspace("Workspace A", default_workflow_key="demo_workflow")
    ws_b = job_db.create_workspace("Workspace B", default_workflow_key="demo_workflow")

    block_event = threading.Event()
    executor = FakeExecutor("code", block_event=block_event)
    definition = _make_definition([_local_node("fetch")])

    for i in range(2):
        job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id=f"QA{i}",
            batch_id="",
            title=f"QA{i}",
            node_keys=["fetch"],
            workspace_id=ws_a["id"],
        )
    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="QB0",
        batch_id="",
        title="QB0",
        node_keys=["fetch"],
        workspace_id=ws_b["id"],
    )

    worker = _make_worker(tmp_path, db_path, executor, [definition], code_capacity=2)
    worker._poll()

    counts = worker.leases.active_counts("code")
    assert counts.get("global", 0) == 2
    assert counts.get(ws_a["id"], 0) == 1
    assert counts.get(ws_b["id"], 0) == 1

    block_event.set()
    worker.stop()


def test_missing_node_code_creates_failed_node_run(tmp_path: Path) -> None:
    """P-0.5: a code-pool node with no published code fails as a config error
    (the executor binding check it replaces died with the bindings table)."""
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")

    executor = FakeExecutor("code")
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )

    worker = _make_worker(tmp_path, db_path, executor, [definition], seed_code=False)
    worker._poll()

    node = job_db.get_job_node(job["id"], "fetch")
    assert node["status"] == "failed"
    assert "no published node code" in node["error_message"]

    worker.stop()


def test_target_completion_pauses_job_and_stops_further_claims(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")

    block_event = threading.Event()
    executor = FakeExecutor("code", block_event=block_event)
    definition = _make_definition(
        [
            _local_node("root"),
            WorkflowNode(key="left", label="left", capability="left", after=["root"]),
            WorkflowNode(key="target", label="target", capability="target", after=["left"]),
            WorkflowNode(key="right", label="right", capability="right", after=["root"]),
        ]
    )

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["root", "left", "target", "right"],
        workspace_id=ws["id"],
    )
    job_db.set_job_execution_target(job["id"], "target")
    for node_key in ["root", "left", "target"]:
        _set_node_limit(job_db, ws["id"], "test", node_key, 1)

    worker = _make_worker(tmp_path, db_path, executor, [definition], code_capacity=4)
    block_event.set()

    for _ in range(20):
        worker._poll()
        job_after = job_db.get_job(job["id"])
        if job_after and job_after["status"] in ("paused", "completed"):
            break

    worker.stop()

    statuses = {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job["id"])}
    assert statuses["root"] == "completed"
    assert statuses["left"] == "completed"
    assert statuses["target"] == "completed"
    assert statuses["right"] == "pending"

    job_after = job_db.get_job(job["id"])
    assert job_after is not None
    assert job_after["status"] == "paused"
    assert job_after["execution_paused"] == 1
    assert job_after["pause_reason"] == "target_reached"


def test_stale_target_snapshot_rejected_by_claim_transaction(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["root", "left"],
        workspace_id=ws["id"],
    )
    job_db.set_job_execution_target(job["id"], "root")

    # Simulate a worker with a stale snapshot by calling try_claim directly.
    leases = ExecutorLeaseRepository(db_path, data_dir=tmp_path)

    job_db.set_job_execution_target(job["id"], "left")
    claim = leases.try_claim(
        LeaseClaimRequest(
            executor_id="code",
            global_capacity=2,
            workspace_id=ws["id"],
            job_id=job["id"],
            workflow_key="test",
            node_key="root",
            capability="root",
            local_node_limit=1,
            lease_ttl_seconds=60,
            log_path="logs/run.log",
            execution_mode="until_node",
            target_node_key="root",
            allowed_node_keys=("root",),
        )
    )
    assert claim is None

    with job_db.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=%s", (job["id"],)).fetchall()
        leases_rows = conn.execute(
            "select * from executor_leases where job_id=%s", (job["id"],)
        ).fetchall()
    assert len(runs) == 0
    assert len(leases_rows) == 0


def test_global_capacity_enforced_by_lease_transaction(tmp_path: Path) -> None:
    """The lease repository itself rejects claims that would exceed global capacity."""
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")

    executor = FakeExecutor("code")
    definition = _make_definition([_local_node("fetch")])

    job1 = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    job2 = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q2",
        batch_id="",
        title="Q2",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    base_request = LeaseClaimRequest(
        executor_id="code",
        global_capacity=1,
        workspace_id=ws["id"],
        job_id=job1["id"],
        workflow_key="test",
        node_key="fetch",
        capability="fetch",
        local_node_limit=None,
        lease_ttl_seconds=60,
        log_path="logs/run.log",
    )

    claim1 = worker.leases.try_claim(base_request)
    assert claim1 is not None

    claim2 = worker.leases.try_claim(
        LeaseClaimRequest(
            executor_id="code",
            global_capacity=1,
            workspace_id=ws["id"],
            job_id=job2["id"],
            workflow_key="test",
            node_key="fetch",
            capability="fetch",
            local_node_limit=None,
            lease_ttl_seconds=60,
            log_path="logs/run2.log",
        )
    )
    assert claim2 is None

    counts = worker.leases.active_counts("code")
    assert counts.get("global", 0) == 1

    worker.stop()
