from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.executors.config import LocalExecutorConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.jobs import JobQueries
from server.app.pipeline_worker_thread import PipelineWorkerThread
from server.app.pipelines.definition import PipelineDefinition, PipelineIntake, PipelineNode
from server.app.settings import Settings


def local_node(key: str, outputs: list[str] | None = None) -> PipelineNode:
    return PipelineNode(
        key=key,
        label=key,
        capability=key,
        outputs=outputs or ["output.json"],
    )


def make_definition(nodes: list[PipelineNode]) -> PipelineDefinition:
    return PipelineDefinition(
        key="test",
        label="Test",
        intake=PipelineIntake(),
        nodes={n.key: n for n in nodes},
    )


def local_def(capacity: int, capabilities: set[str]) -> Any:
    return {
        "kind": "local",
        "global_capacity": capacity,
        "capabilities": {cap: {"handler": "dummy.handler"} for cap in capabilities},
    }


def make_registry(
    executors: dict[str, Any],
    definitions: dict[str, Any],
) -> ExecutorRegistry:
    """Build an ExecutorRegistry using only local executor definitions.

    These tests exercise the local executor path; using LocalExecutorConfig
    directly keeps the registry construction simple and correct.
    """
    return ExecutorRegistry(
        executors=executors,
        global_capacities={eid: definitions[eid]["global_capacity"] for eid in definitions},
        definitions={eid: LocalExecutorConfig(**definitions[eid]) for eid in definitions},
    )


def allocate(
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


def bind(
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


def make_worker(
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
