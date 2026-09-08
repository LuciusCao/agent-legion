from __future__ import annotations

import hashlib
import json
import shutil
import sys
import threading
from pathlib import Path

import pytest

from server.app.configuration.executor_runtime import ExecutorRuntimeConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.runtime import ExecutionRuntime
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers import scan_entries
from worker import executor as agent_worker


def _claim(execution_id: str = "exec-1") -> dict:
    return {
        "execution_id": execution_id,
        "lease_id": "lease-1",
        "node_key": "node_a",
        "bundle_url": "/api/agent-executions/exec-1/bundle",
    }


class FakeClient:
    """In-memory stand-in for agent_worker.Client.

    构造参数仍是 bundle path（main() 系列传一个不存在的路径即可——它们
    只打桩 claim/register/get_self，从不下载）。
    """

    def __init__(
        self, bundle: Path, *, heartbeat_status: int = 204, release_status: int = 204
    ) -> None:
        self._bundle = bundle
        self._heartbeat_status = heartbeat_status
        self._release_status = release_status
        self.heartbeats = 0
        self.heartbeat_lease_ids: list[str] = []
        self.reports: list[dict] = []
        self.report_lease_ids: list[str] = []
        self.release_calls = 0
        self.uploads: dict[str, bytes] = {}

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._bundle.read_bytes())

    def upload_artifact(self, path: Path) -> str:
        data = path.read_bytes()
        self.uploads[hashlib.sha256(data).hexdigest()] = data
        return f"sha256:{hashlib.sha256(data).hexdigest()}"

    def get_self(self) -> dict:
        return {
            "worker_id": "w1",
            "name": "Worker 1",
            "revoked": False,
            "online": True,
        }

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        self.heartbeats += 1
        self.heartbeat_lease_ids.append(lease_id)
        return self._heartbeat_status, []

    def release_slot(self, execution_id: str, lease_id: str) -> int:
        self.release_calls += 1
        return self._release_status

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.reports.append(metadata)
        self.report_lease_ids.append(lease_id)
        return 204, b""


def _write_main_config(tmp_path: Path) -> Path:
    token_file = tmp_path / "register_token"
    token_file.write_text("management-token", encoding="utf-8")
    config = {
        "host_url": "http://unused",
        "worker_id": "w1",
        "runtimes": ["pi"],
        "max_concurrency": 1,
        "register_token_file": str(token_file),
        "work_root": str(tmp_path / "work"),
        "poll_interval_seconds": 0.05,
        "heartbeat_interval_seconds": 0.05,
    }
    config_path = tmp_path / "worker.yaml"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake: FakeClient,
    config_updates: dict | None = None,
) -> tuple[threading.Thread, dict, list]:
    """Run main() in a thread with a stubbed signal module and Client."""
    handlers: dict = {}
    monkeypatch.setattr(
        agent_worker.signal,
        "signal",
        lambda sig, handler: handlers.setdefault(sig, handler),
    )
    monkeypatch.setattr(agent_worker, "Client", lambda host, **kwargs: fake)
    fake.register = lambda config, token: {"worker_token": "worker-token", "workspaces": []}  # type: ignore[attr-defined]
    # main() 的启动预检会探测 PATH 上的 runtime 二进制；测试与真实机器环境
    # 无关，统一打桩为全部存在（预检自身的用例单独覆盖）。
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/bin/{binary}")
    config_path = _write_main_config(tmp_path)
    if config_updates:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.update(config_updates)
        config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["agent_worker.py", "--config", str(config_path)])
    result: list[int] = []

    def target() -> None:
        result.append(agent_worker.main())

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, handlers, result


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
