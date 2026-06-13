from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from server.app.executors.config import (
    LocalExecutorConfig,
    PiExecutorConfig,
)
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.jobs import JobQueries
from server.app.pipeline_worker_thread import PipelineWorkerThread
from server.app.pipelines.definition import (
    PipelineDefinition,
    PipelineIntake,
    PipelineNode,
)
from server.app.settings import Settings


class FakeExecutor:
    """Test executor that blocks on an event and writes declared outputs."""

    def __init__(
        self,
        executor_id: str,
        kind: str = "local",
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
        self.block_event.wait(timeout=10)
        for output in context.expected_outputs:
            (context.job_dir / output).write_text('{"done": true}', encoding="utf-8")
        return ExecutionResult(
            status="completed",
            exit_code=0,
            produced_artifacts=tuple(context.expected_outputs),
        )

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)


def _local_def(capacity: int, capabilities: set[str]) -> Any:
    return {
        "kind": "local",
        "global_capacity": capacity,
        "capabilities": {cap: {"handler": "dummy.handler"} for cap in capabilities},
    }


def _agent_def(capacity: int, capabilities: set[str]) -> Any:
    return {
        "kind": "pi",
        "global_capacity": capacity,
        "capabilities": {cap: {"skill": "dummy/skill"} for cap in capabilities},
    }


def _make_registry(
    executors: dict[str, FakeExecutor],
    definitions: dict[str, Any],
) -> ExecutorRegistry:
    return ExecutorRegistry(
        executors=executors,
        global_capacities={eid: definitions[eid]["global_capacity"] for eid in definitions},
        definitions={
            eid: (
                LocalExecutorConfig(**definitions[eid])
                if definitions[eid]["kind"] == "local"
                else PiExecutorConfig(**definitions[eid])
            )
            for eid in definitions
        },
    )


def _make_definition(nodes: list[PipelineNode]) -> PipelineDefinition:
    return PipelineDefinition(
        key="test",
        label="Test",
        intake=PipelineIntake(),
        nodes={n.key: n for n in nodes},
    )


def _local_node(key: str, outputs: list[str] | None = None) -> PipelineNode:
    return PipelineNode(
        key=key,
        label=key,
        capability=key,
        outputs=outputs or ["output.json"],
    )


def _agent_node(key: str, outputs: list[str] | None = None) -> PipelineNode:
    return PipelineNode(
        key=key,
        label=key,
        capability=key,
        outputs=outputs or ["output.json"],
    )


def _allocate(
    job_db: JobQueries,
    workspace_id: str,
    executor_id: str,
    concurrency_limit: int,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit)
            values (?, ?, ?)
            on conflict(workspace_id, executor_id) do update set concurrency_limit=excluded.concurrency_limit
            """,
            (workspace_id, executor_id, concurrency_limit),
        )


def _set_node_limit(
    job_db: JobQueries,
    workspace_id: str,
    pipeline_key: str,
    node_key: str,
    concurrency_limit: int,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into workspace_node_limits (workspace_id, pipeline_key, node_key, concurrency_limit)
            values (?, ?, ?, ?)
            on conflict(workspace_id, pipeline_key, node_key) do update set concurrency_limit=excluded.concurrency_limit
            """,
            (workspace_id, pipeline_key, node_key, concurrency_limit),
        )


def _bind(
    job_db: JobQueries,
    workspace_id: str,
    pipeline_key: str,
    node_key: str,
    executor_id: str,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into workspace_node_bindings (workspace_id, pipeline_key, node_key, executor_id)
            values (?, ?, ?, ?)
            on conflict(workspace_id, pipeline_key, node_key) do update set executor_id=excluded.executor_id
            """,
            (workspace_id, pipeline_key, node_key, executor_id),
        )


def _make_worker(
    tmp_path: Path,
    db_path: Path,
    registry: ExecutorRegistry,
    definitions: list[PipelineDefinition],
) -> PipelineWorkerThread:
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    leases = ExecutorLeaseRepository(db_path)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
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
        config={"pipelines": {"enabled": True}},
        executor_definitions=registry.definitions(),
    )
    worker = PipelineWorkerThread(
        job_db=job_db,
        leases=leases,
        registry=registry,
        runtime=runtime,
        settings=settings,
    )
    worker._definitions = definitions
    return worker


def test_same_node_submitted_once(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    block_event = threading.Event()
    executor = FakeExecutor("local-default", block_event=block_event)
    registry = _make_registry(
        {"local-default": executor},
        {"local-default": _local_def(2, {"fetch"})},
    )
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    _bind(job_db, ws["id"], "test", "fetch", "local-default")
    _allocate(job_db, ws["id"], "local-default", 2)

    worker = _make_worker(tmp_path, db_path, registry, [definition])
    worker._poll()

    assert worker.leases.active_counts("local-default").get("global", 0) == 1
    assert len(worker._futures) == 1

    worker._poll()
    assert worker.leases.active_counts("local-default").get("global", 0) == 1
    assert len(worker._futures) == 1

    block_event.set()
    worker.stop()


def test_global_capacity_not_exceeded_across_workers(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    block_event = threading.Event()
    executor = FakeExecutor("local-default", block_event=block_event)
    registry = _make_registry(
        {"local-default": executor},
        {"local-default": _local_def(1, {"fetch"})},
    )
    definition = _make_definition([_local_node("fetch")])

    for i in range(2):
        job_db.create_job(
            pipeline_key="test",
            source_type="question",
            source_id=f"Q{i}",
            batch_id="",
            title=f"Q{i}",
            node_keys=["fetch"],
            workspace_id=ws["id"],
        )
    _bind(job_db, ws["id"], "test", "fetch", "local-default")
    _allocate(job_db, ws["id"], "local-default", 2)

    worker1 = _make_worker(tmp_path, db_path, registry, [definition])
    worker2 = _make_worker(tmp_path, db_path, registry, [definition])

    worker1._poll()
    worker2._poll()

    total = worker1.leases.active_counts("local-default").get("global", 0)
    assert total == 1

    block_event.set()
    worker1.stop()
    worker2.stop()


def test_workspace_limit_does_not_reserve_unused_global_capacity(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws_a = job_db.create_workspace("Workspace A")
    ws_b = job_db.create_workspace("Workspace B")

    block_event = threading.Event()
    executor = FakeExecutor("local-default", block_event=block_event)
    registry = _make_registry(
        {"local-default": executor},
        {"local-default": _local_def(2, {"fetch"})},
    )
    definition = _make_definition([_local_node("fetch")])

    for i in range(2):
        job_db.create_job(
            pipeline_key="test",
            source_type="question",
            source_id=f"QB{i}",
            batch_id="",
            title=f"QB{i}",
            node_keys=["fetch"],
            workspace_id=ws_b["id"],
        )
    _bind(job_db, ws_b["id"], "test", "fetch", "local-default")
    _allocate(job_db, ws_a["id"], "local-default", 1)
    _allocate(job_db, ws_b["id"], "local-default", 2)

    worker = _make_worker(tmp_path, db_path, registry, [definition])
    worker._poll()

    counts = worker.leases.active_counts("local-default")
    assert counts.get("global", 0) == 2
    assert counts.get(ws_a["id"], 0) == 0
    assert counts.get(ws_b["id"], 0) == 2

    block_event.set()
    worker.stop()


def test_two_agent_nodes_share_workspace_executor_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    block_event = threading.Event()
    executor = FakeExecutor("pi-default", kind="pi", block_event=block_event)
    registry = _make_registry(
        {"pi-default": executor},
        {"pi-default": _agent_def(2, {"agent_a", "agent_b"})},
    )
    definition = _make_definition([_agent_node("agent_a"), _agent_node("agent_b")])

    job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["agent_a", "agent_b"],
        workspace_id=ws["id"],
    )
    _bind(job_db, ws["id"], "test", "agent_a", "pi-default")
    _bind(job_db, ws["id"], "test", "agent_b", "pi-default")
    _allocate(job_db, ws["id"], "pi-default", 1)

    worker = _make_worker(tmp_path, db_path, registry, [definition])
    worker._poll()

    counts = worker.leases.active_counts("pi-default")
    assert counts.get("global", 0) == 1
    assert counts.get(ws["id"], 0) == 1

    block_event.set()
    worker.stop()


def test_local_node_limits_are_workspace_specific(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws_a = job_db.create_workspace("Workspace A")
    ws_b = job_db.create_workspace("Workspace B")

    block_event = threading.Event()
    executor = FakeExecutor("local-default", block_event=block_event)
    registry = _make_registry(
        {"local-default": executor},
        {"local-default": _local_def(4, {"fetch"})},
    )
    definition = _make_definition([_local_node("fetch")])

    for workspace in [ws_a, ws_b]:
        for i in range(2):
            job_db.create_job(
                pipeline_key="test",
                source_type="question",
                source_id=f"{workspace['id']}_{i}",
                batch_id="",
                title=f"{workspace['id']}_{i}",
                node_keys=["fetch"],
                workspace_id=workspace["id"],
            )
        _bind(job_db, workspace["id"], "test", "fetch", "local-default")
        _allocate(job_db, workspace["id"], "local-default", 2)
    _set_node_limit(job_db, ws_a["id"], "test", "fetch", 1)
    _set_node_limit(job_db, ws_b["id"], "test", "fetch", 2)

    worker = _make_worker(tmp_path, db_path, registry, [definition])
    worker._poll()

    counts = worker.leases.active_counts("local-default")
    assert counts.get("global", 0) == 3
    assert counts.get(ws_a["id"], 0) == 1
    assert counts.get(ws_b["id"], 0) == 2

    block_event.set()
    worker.stop()


def test_round_robin_allows_small_workspace_to_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws_a = job_db.create_workspace("Workspace A")
    ws_b = job_db.create_workspace("Workspace B")

    block_event = threading.Event()
    executor = FakeExecutor("local-default", block_event=block_event)
    registry = _make_registry(
        {"local-default": executor},
        {"local-default": _local_def(2, {"fetch"})},
    )
    definition = _make_definition([_local_node("fetch")])

    for i in range(2):
        job_db.create_job(
            pipeline_key="test",
            source_type="question",
            source_id=f"QA{i}",
            batch_id="",
            title=f"QA{i}",
            node_keys=["fetch"],
            workspace_id=ws_a["id"],
        )
    job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="QB0",
        batch_id="",
        title="QB0",
        node_keys=["fetch"],
        workspace_id=ws_b["id"],
    )
    for ws in [ws_a, ws_b]:
        _bind(job_db, ws["id"], "test", "fetch", "local-default")
    _allocate(job_db, ws_a["id"], "local-default", 2)
    _allocate(job_db, ws_b["id"], "local-default", 1)

    worker = _make_worker(tmp_path, db_path, registry, [definition])
    worker._poll()

    counts = worker.leases.active_counts("local-default")
    assert counts.get("global", 0) == 2
    assert counts.get(ws_a["id"], 0) == 1
    assert counts.get(ws_b["id"], 0) == 1

    block_event.set()
    worker.stop()


def test_missing_binding_creates_failed_node_run(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    executor = FakeExecutor("local-default")
    registry = _make_registry(
        {"local-default": executor},
        {"local-default": _local_def(2, {"fetch"})},
    )
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    _allocate(job_db, ws["id"], "local-default", 2)

    worker = _make_worker(tmp_path, db_path, registry, [definition])
    worker._poll()

    node = job_db.get_job_node(job["id"], "fetch")
    assert node["status"] == "failed"
    assert "No Executor binding" in node["error_message"]

    worker.stop()


def test_target_completion_pauses_job_and_stops_further_claims(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    block_event = threading.Event()
    executor = FakeExecutor("local-default", block_event=block_event)
    registry = _make_registry(
        {"local-default": executor},
        {"local-default": _local_def(4, {"root", "left", "target", "right"})},
    )
    definition = _make_definition(
        [
            _local_node("root"),
            PipelineNode(key="left", label="left", capability="left", after=["root"]),
            PipelineNode(key="target", label="target", capability="target", after=["left"]),
            PipelineNode(key="right", label="right", capability="right", after=["root"]),
        ]
    )

    job = job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["root", "left", "target", "right"],
        workspace_id=ws["id"],
    )
    job_db.set_job_execution_target(job["id"], "target")
    for node in definition.nodes.values():
        _bind(job_db, ws["id"], "test", node.key, "local-default")
    _allocate(job_db, ws["id"], "local-default", 4)
    for node_key in ["root", "left", "target"]:
        _set_node_limit(job_db, ws["id"], "test", node_key, 1)

    worker = _make_worker(tmp_path, db_path, registry, [definition])
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
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    job = job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["root"],
        workspace_id=ws["id"],
    )
    job_db.set_job_execution_target(job["id"], "root")
    _bind(job_db, ws["id"], "test", "root", "local-default")
    _allocate(job_db, ws["id"], "local-default", 2)

    # Simulate a worker with a stale snapshot by calling try_claim directly.
    leases = ExecutorLeaseRepository(db_path)
    from server.app.executors.models import LeaseClaimRequest

    job_db.set_job_execution_target(job["id"], "other")
    claim = leases.try_claim(
        LeaseClaimRequest(
            executor_id="local-default",
            global_capacity=2,
            workspace_id=ws["id"],
            job_id=job["id"],
            pipeline_key="test",
            node_key="root",
            capability="root",
            local_node_limit=1,
            lease_ttl_seconds=60,
            log_path="/tmp/run.log",
            execution_mode="until_node",
            target_node_key="root",
            allowed_node_keys=("root",),
        )
    )
    assert claim is None

    with job_db.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=?", (job["id"],)).fetchall()
        leases_rows = conn.execute(
            "select * from executor_leases where job_id=?", (job["id"],)
        ).fetchall()
    assert len(runs) == 0
    assert len(leases_rows) == 0


def test_binding_to_unsupported_capability_creates_failed_node_run(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    executor = FakeExecutor("local-default", supports={"other"})
    registry = _make_registry(
        {"local-default": executor},
        {"local-default": _local_def(2, {"fetch"})},
    )
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    _bind(job_db, ws["id"], "test", "fetch", "local-default")
    _allocate(job_db, ws["id"], "local-default", 2)

    worker = _make_worker(tmp_path, db_path, registry, [definition])
    worker._poll()

    node = job_db.get_job_node(job["id"], "fetch")
    assert node["status"] == "failed"
    assert "does not support capability" in node["error_message"]

    worker.stop()
