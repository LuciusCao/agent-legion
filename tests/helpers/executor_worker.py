from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers import scan_entries


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


def make_worker(
    tmp_path: Path,
    db_path: Path,
    executor: Any,
    definitions: list[WorkflowDefinition],
    *,
    code_capacity: int = 2,
    heartbeat_interval_seconds: float = 1,
    lease_ttl_seconds: float = 5,
    cancellation_grace_seconds: float = 0.5,
    executor_runtime: ExecutorRuntimeConfig | None = None,
) -> WorkflowWorkerThread:
    """Single code pool worker (P-0.5): the executor instance runs every claim."""
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    leases = ExecutorLeaseRepository(db_path, data_dir=tmp_path)
    runtime = ExecutionRuntime(
        leases=leases,
        executor=executor,
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
        database_url=str(db_path),
        executor_runtime=executor_runtime
        or ExecutorRuntimeConfig.model_validate(
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
    # Every non-Agent-routed node runs as code (P-0.5) and needs published
    # code to dispatch; the fake executors in these tests never read the
    # text, so seed a global no-op version for every scanned node.
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
    worker._scan_entries = scan_entries(*definitions)
    return worker
