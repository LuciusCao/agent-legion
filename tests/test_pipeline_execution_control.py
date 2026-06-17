from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from server.app.db.schema import init_db
from server.app.executors.config import LocalCapabilityConfig, LocalExecutorConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import (
    ClaimedExecution,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.jobs import JobQueries
from server.app.pipeline_worker_thread import PipelineWorkerThread
from server.app.pipelines.definition import (
    PipelineDefinition,
    PipelineIntake,
    PipelineNode,
)
from server.app.pipelines.execution_control import (
    ExecutionControlError,
    allowed_nodes,
    ancestor_closure,
)
from server.app.settings import Settings


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    path = tmp_path / "control.sqlite"
    init_db(path)
    return path


@pytest.fixture
def queries(tmp_db: Path) -> JobQueries:
    jobs_dir = tmp_db.parent / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return JobQueries(tmp_db, jobs_dir)


@pytest.fixture
def repo(tmp_db: Path) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(tmp_db)


def _branched_definition() -> PipelineDefinition:
    """root -> left -> target, root -> right -> after_right."""
    return PipelineDefinition(
        key="branched",
        label="Branched",
        intake=PipelineIntake(),
        nodes={
            "root": PipelineNode(key="root", label="Root", capability="root"),
            "left": PipelineNode(key="left", label="Left", capability="left", after=["root"]),
            "target": PipelineNode(
                key="target", label="Target", capability="target", after=["left"]
            ),
            "right": PipelineNode(key="right", label="Right", capability="right", after=["root"]),
            "after_right": PipelineNode(
                key="after_right",
                label="After Right",
                capability="after_right",
                after=["right"],
            ),
        },
    )


def test_ancestor_closure_for_branched_target() -> None:
    definition = _branched_definition()
    closure = ancestor_closure(definition, "target")
    assert closure == frozenset({"root", "left", "target"})


def test_ancestor_closure_unknown_target_raises() -> None:
    definition = _branched_definition()
    with pytest.raises(ExecutionControlError):
        ancestor_closure(definition, "missing")


def test_allowed_nodes_full_mode() -> None:
    definition = _branched_definition()
    assert allowed_nodes(definition, {"execution_mode": "full"}) == frozenset(definition.nodes)


def test_allowed_nodes_until_target_excludes_unrelated_branch() -> None:
    definition = _branched_definition()
    allowed = allowed_nodes(
        definition,
        {"execution_mode": "until_node", "target_node_key": "target"},
    )
    assert allowed == frozenset({"root", "left", "target"})
    assert "right" not in allowed
    assert "after_right" not in allowed


def test_allowed_nodes_unknown_target_raises() -> None:
    definition = _branched_definition()
    with pytest.raises(ExecutionControlError):
        allowed_nodes(
            definition,
            {"execution_mode": "until_node", "target_node_key": "missing"},
        )


def _claim_request(
    workspace_id: str,
    job_id: str,
    node_key: str,
    *,
    execution_mode: str = "full",
    target_node_key: str | None = None,
    allowed_node_keys: tuple[str, ...] = (),
    executor_id: str = "local-default",
    global_capacity: int = 2,
    local_node_limit: int | None = 1,
    ttl: int = 60,
    log_path: str = "/tmp/run.log",
    pipeline_key: str = "branched",
    capability: str = "root",
) -> LeaseClaimRequest:
    return LeaseClaimRequest(
        executor_id=executor_id,
        global_capacity=global_capacity,
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key=pipeline_key,
        node_key=node_key,
        capability=capability,
        local_node_limit=local_node_limit,
        lease_ttl_seconds=ttl,
        log_path=log_path,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        target_node_key=target_node_key,
        allowed_node_keys=allowed_node_keys,
    )


def _setup_workspace(
    queries: JobQueries,
    definition: PipelineDefinition,
    target_node_key: str | None = None,
    executor_id: str = "local-default",
) -> tuple[str, str]:
    workspace = queries.create_workspace(name="control-ws", default_workflow_key="branched")
    workspace_id = workspace["id"]
    job = queries.create_job(
        workflow_key="branched",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=list(definition.nodes),
        workspace_id=workspace_id,
    )
    job_id = str(job["id"])
    if target_node_key:
        queries.set_job_execution_target(job_id, target_node_key)

    with queries.connect() as conn:
        for node in definition.nodes.values():
            conn.execute(
                """
                insert into workspace_node_bindings(workspace_id, pipeline_key, node_key, executor_id)
                values (?, ?, ?, ?)
                on conflict(workspace_id, pipeline_key, node_key) do update set
                  executor_id=excluded.executor_id
                """,
                (workspace_id, "branched", node.key, executor_id),
            )
        conn.execute(
            """
            insert into workspace_executor_allocations(workspace_id, executor_id, concurrency_limit)
            values (?, ?, ?)
            on conflict(workspace_id, executor_id) do update set
              concurrency_limit=excluded.concurrency_limit
            """,
            (workspace_id, executor_id, 10),
        )
        for node in definition.nodes.values():
            conn.execute(
                """
                insert into workspace_node_limits(workspace_id, pipeline_key, node_key, concurrency_limit)
                values (?, ?, ?, ?)
                on conflict(workspace_id, pipeline_key, node_key) do update set
                  concurrency_limit=excluded.concurrency_limit
                """,
                (workspace_id, "branched", node.key, 1),
            )
    return workspace_id, job_id


def test_unrelated_node_not_claimable_in_until_node_mode(
    queries: JobQueries,
    repo: ExecutorLeaseRepository,
) -> None:
    definition = _branched_definition()
    workspace_id, job_id = _setup_workspace(queries, definition, target_node_key="target")
    allowed = allowed_nodes(
        definition,
        {"execution_mode": "until_node", "target_node_key": "target"},
    )

    # root is claimable because it is in the target closure.
    root_claim = repo.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            "root",
            execution_mode="until_node",
            target_node_key="target",
            allowed_node_keys=tuple(sorted(allowed)),
            capability="root",
        )
    )
    assert isinstance(root_claim, ClaimedExecution)

    # right is outside the closure and must be rejected transactionally.
    right_claim = repo.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            "right",
            execution_mode="until_node",
            target_node_key="target",
            allowed_node_keys=tuple(sorted(allowed)),
            capability="right",
            local_node_limit=1,
        )
    )
    assert right_claim is None


def test_stale_target_snapshot_rejected_and_no_state_persisted(
    queries: JobQueries,
    repo: ExecutorLeaseRepository,
) -> None:
    """Worker computes ready nodes, then the job target changes before try_claim."""
    definition = _branched_definition()
    workspace_id, job_id = _setup_workspace(queries, definition, target_node_key="target")

    # Simulate an old snapshot computed when the target was "target".
    stale_snapshot = {
        "execution_mode": "until_node",
        "target_node_key": "target",
        "execution_paused": False,
        "pause_reason": "",
    }
    stale_allowed = allowed_nodes(definition, stale_snapshot)

    # User changes the target to "after_right" before the worker claims.
    queries.set_job_execution_target(job_id, "after_right")

    claim = repo.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            "root",
            execution_mode="until_node",
            target_node_key="target",
            allowed_node_keys=tuple(sorted(stale_allowed)),
            capability="root",
        )
    )
    assert claim is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=?", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_paused_job_rejects_claim_and_creates_no_state(
    queries: JobQueries,
    repo: ExecutorLeaseRepository,
) -> None:
    definition = _branched_definition()
    workspace_id, job_id = _setup_workspace(queries, definition, target_node_key="target")
    queries.pause_job(job_id, "awaiting_resources")

    allowed = allowed_nodes(
        definition,
        {"execution_mode": "until_node", "target_node_key": "target"},
    )
    claim = repo.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            "root",
            execution_mode="until_node",
            target_node_key="target",
            allowed_node_keys=tuple(sorted(allowed)),
            capability="root",
        )
    )
    assert claim is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=?", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_target_completion_pauses_job_atomically(
    queries: JobQueries,
    repo: ExecutorLeaseRepository,
) -> None:
    definition = _branched_definition()
    workspace_id, job_id = _setup_workspace(queries, definition, target_node_key="target")
    allowed = allowed_nodes(
        definition,
        {"execution_mode": "until_node", "target_node_key": "target"},
    )

    claim = repo.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            "root",
            execution_mode="until_node",
            target_node_key="target",
            allowed_node_keys=tuple(sorted(allowed)),
            capability="root",
        )
    )
    assert isinstance(claim, ClaimedExecution)
    repo.finish(claim.lease_id, ExecutionResult(status="completed", exit_code=0))

    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["execution_mode"] == "until_node"

    # Complete the target node; job should pause atomically.
    target_claim = repo.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            "target",
            execution_mode="until_node",
            target_node_key="target",
            allowed_node_keys=tuple(sorted(allowed)),
            capability="target",
        )
    )
    assert isinstance(target_claim, ClaimedExecution)
    repo.finish(target_claim.lease_id, ExecutionResult(status="completed", exit_code=0))

    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "paused"
    assert job["execution_paused"] == 1
    assert job["pause_reason"] == "target_reached"


def _make_worker(
    tmp_path: Path,
    queries: JobQueries,
    definitions: list[PipelineDefinition],
) -> PipelineWorkerThread:
    executor_def = LocalExecutorConfig(
        kind="local",
        global_capacity=2,
        capabilities={
            node.capability: LocalCapabilityConfig(handler="dummy.handler")
            for definition in definitions
            for node in definition.nodes.values()
        },
    )
    registry = ExecutorRegistry(
        executors={"local-default": _FakeExecutor("local-default")},
        global_capacities={"local-default": 2},
        definitions={"local-default": executor_def},
    )
    leases = ExecutorLeaseRepository(queries.path)
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
        config={},
        executor_definitions=registry.definitions(),
    )
    return PipelineWorkerThread(
        job_db=queries,
        leases=leases,
        registry=registry,
        runtime=runtime,
        settings=settings,
    )


class _FakeExecutor:
    kind = "local"

    def __init__(self, executor_id: str) -> None:
        self.id = executor_id
        self.kind = "local"

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: Any) -> Any:
        for output in context.expected_outputs:
            (context.job_dir / output).write_text('{"done": true}', encoding="utf-8")
        from server.app.executors.models import ExecutionResult

        return ExecutionResult(
            status="completed",
            exit_code=0,
            produced_artifacts=tuple(context.expected_outputs),
        )

    def cancel(self, execution_id: str) -> None:
        pass


def test_worker_runs_only_target_closure_in_until_node_mode(
    queries: JobQueries,
    tmp_path: Path,
) -> None:
    definition = _branched_definition()
    workspace = queries.create_workspace(name="worker-control-ws", default_workflow_key="branched")
    workspace_id = workspace["id"]
    job = queries.create_job(
        workflow_key="branched",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=list(definition.nodes),
        workspace_id=workspace_id,
    )
    job_id = str(job["id"])
    queries.set_job_execution_target(job_id, "target")

    with queries.connect() as conn:
        for node in definition.nodes.values():
            conn.execute(
                """
                insert into workspace_node_bindings(workspace_id, pipeline_key, node_key, executor_id)
                values (?, ?, ?, ?)
                on conflict(workspace_id, pipeline_key, node_key) do update set
                  executor_id=excluded.executor_id
                """,
                (workspace_id, "branched", node.key, "local-default"),
            )
        conn.execute(
            """
            insert into workspace_executor_allocations(workspace_id, executor_id, concurrency_limit)
            values (?, ?, ?)
            on conflict(workspace_id, executor_id) do update set
              concurrency_limit=excluded.concurrency_limit
            """,
            (workspace_id, "local-default", 10),
        )
        for node in definition.nodes.values():
            conn.execute(
                """
                insert into workspace_node_limits(workspace_id, pipeline_key, node_key, concurrency_limit)
                values (?, ?, ?, ?)
                on conflict(workspace_id, pipeline_key, node_key) do update set
                  concurrency_limit=excluded.concurrency_limit
                """,
                (workspace_id, "branched", node.key, 1),
            )

    worker = _make_worker(tmp_path, queries, [definition])
    worker._definitions = [definition]

    # Poll repeatedly until the worker pauses on target completion.
    for _ in range(20):
        worker._poll()
        job_after = queries.get_job(job_id)
        if job_after and job_after["status"] in ("paused", "completed"):
            break

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert statuses["root"] == "completed"
    assert statuses["left"] == "completed"
    assert statuses["target"] == "completed"
    assert statuses["right"] == "pending"
    assert statuses["after_right"] == "pending"

    job_after = queries.get_job(job_id)
    assert job_after is not None
    assert job_after["status"] == "paused"
    assert job_after["execution_paused"] == 1
    assert job_after["pause_reason"] == "target_reached"

    worker.stop()
