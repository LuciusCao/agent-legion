from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.executors.config import ExecutorConfig, LocalExecutorConfig, PiExecutorConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode


def local_node(key: str, outputs: list[str] | None = None) -> WorkflowNode:
    return WorkflowNode(
        key=key,
        label=key,
        capability=key,
        outputs=outputs or ["output.json"],
    )


def make_definition(nodes: list[WorkflowNode]) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={n.key: n for n in nodes},
    )


def local_def(capacity: int, capabilities: set[str]) -> Any:
    return {
        "kind": "local",
        "global_capacity": capacity,
        "capabilities": {cap: {"handler": "dummy.handler"} for cap in capabilities},
    }


def pi_def(capacity: int, capabilities: dict[str, str]) -> Any:
    return {
        "kind": "pi",
        "global_capacity": capacity,
        "capabilities": {cap: {"skill": skill} for cap, skill in capabilities.items()},
    }


def make_pi_skill(skill_root: Path, skill: str) -> None:
    """Create a minimal Pi skill directory tree for tests."""
    skill_dir = skill_root / skill
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "references" / "output-contract.md").write_text("contract", encoding="utf-8")
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts" / "validate_output.py").write_text(
        "import sys; sys.exit(0)", encoding="utf-8"
    )


def make_registry(
    executors: dict[str, Any],
    definitions: dict[str, Any],
) -> ExecutorRegistry:
    """Build an ExecutorRegistry from local and/or pi executor definitions."""

    def _build_config(eid: str) -> ExecutorConfig:
        kind = definitions[eid]["kind"]
        if kind == "pi":
            return PiExecutorConfig(**definitions[eid])
        return LocalExecutorConfig(**definitions[eid])

    return ExecutorRegistry(
        executors=executors,
        global_capacities={eid: definitions[eid]["global_capacity"] for eid in definitions},
        definitions={eid: _build_config(eid) for eid in definitions},
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
            values (%s, %s, %s)
            on conflict(workspace_id, executor_id) do update set concurrency_limit=excluded.concurrency_limit
            """,
            (workspace_id, executor_id, concurrency_limit),
        )


def bind(
    job_db: JobQueries,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    executor_id: str,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id)
            values (%s, %s, %s, %s)
            on conflict(workspace_id, workflow_key, node_key) do update set executor_id=excluded.executor_id
            """,
            (workspace_id, workflow_key, node_key, executor_id),
        )


def make_worker(
    tmp_path: Path,
    db_path: Path,
    registry: ExecutorRegistry,
    definitions: list[WorkflowDefinition],
    *,
    heartbeat_interval_seconds: float = 1,
    lease_ttl_seconds: float = 5,
    cancellation_grace_seconds: float = 0.5,
    executor_runtime: ExecutorRuntimeConfig | None = None,
) -> WorkflowWorkerThread:
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    leases = ExecutorLeaseRepository(db_path)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        lease_ttl_seconds=lease_ttl_seconds,
        cancellation_grace_seconds=cancellation_grace_seconds,
    )
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={"workflows": {"enabled": True}},
        executor_definitions=registry.definitions(),
        executor_runtime=executor_runtime
        or ExecutorRuntimeConfig.model_validate(
            {"workflows": {"enabled": True}, "openclaw": {"command_template": ["openclaw"]}}
        ),
    )
    worker = WorkflowWorkerThread(
        job_db=job_db,
        leases=leases,
        registry=registry,
        runtime=runtime,
        settings=settings,
    )
    worker._definitions = definitions
    return worker
