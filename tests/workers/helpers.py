from __future__ import annotations

import threading
from pathlib import Path

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers import scan_entries


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


class RecordingExecutor:
    kind = "code"

    def __init__(self, executor_id: str, block_event: threading.Event | None = None):
        self.id = executor_id
        self.kind = "code"
        self.block_event = block_event or threading.Event()
        self.contexts: list[ExecutionContext] = []
        self._cancelled: set[str] = set()

    def supports(self, capability: str) -> bool:
        return True

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


def _make_worker(
    tmp_path: Path,
    db_path: Path,
    executor: RecordingExecutor,
    definitions: list[WorkflowDefinition],
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
        config={},
        database_url=str(db_path),
    )
    settings.executor_runtime = ExecutorRuntimeConfig.model_validate(
        {
            "workflows": {"enabled": True},
            "openclaw": {"command_template": ["openclaw"]},
            "code_capacity": 2,
        }
    )
    worker = WorkflowWorkerThread(
        job_db=job_db,
        leases=leases,
        runtime=runtime,
        settings=settings,
    )
    worker.state.scan_entries = scan_entries(*definitions)
    return worker


def _make_test_definition(nodes: list[WorkflowNode]) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={n.key: n for n in nodes},
    )


def _make_fake_skill(skill_dir: Path) -> None:
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    (skill_dir / "references" / "output-contract.md").write_text("# contract", encoding="utf-8")
    validator = skill_dir / "scripts" / "validate_output.py"
    validator.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "job_dir = Path(sys.argv[1])\n"
        "(job_dir / 'keywords_raw.json').write_text('{\"questions\": []}')\n"
        "(job_dir / 'keywords_report.json').write_text('{\"summary\": {}}')\n"
    )
    validator.chmod(0o755)


def _seed_trivial_node_code(
    database_url: str, workspace_id: str, workflow_key: str, node_key: str
) -> None:
    """Publish a no-op node code so a code-executor node can dispatch.

    Since #96 every code node requires published workspace code; the
    RecordingExecutor never reads the text.
    """
    from server.app.services.node_codes import NodeCodeService

    codes = NodeCodeService(database_url)
    codes.save_draft(
        workspace_id,
        workflow_key,
        node_key,
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish(workspace_id, workflow_key, node_key)
