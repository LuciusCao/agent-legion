from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.pipeline.transcribe import TranscriptionProvider
from server.app.settings import Settings
from server.app.workflow_worker_thread import WorkflowWorkerThread
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.registry import load_registered_workflow
from server.app.workflows.resource_providers import ResourceProviderDeclarations


def ensure_legacy_workspace_tables(db_or_conn: Any) -> None:
    """Compatibility no-op for tests that predate authoritative configuration."""
    del db_or_conn


def make_workflow_worker(
    tmp_path: Path,
    queries: JobQueries,
    *,
    workflow_key: str = "question_comprehension_info",
    pi_binary: str | None = "echo",
    pi_timeout: int = 1,
) -> tuple[WorkflowWorkerThread, WorkflowDefinition]:
    """Build a configured WorkflowWorkerThread for *workflow_key*."""
    from server.app import main as app_main
    from server.app.executors.leases import ExecutorLeaseRepository
    from server.app.executors.runtime import ExecutionRuntime

    definition = load_registered_workflow(Path("."), workflow_key)
    settings = app_main.load_settings(data_dir=tmp_path)
    # Avoid real CMS/network calls in tests: empty declarations make every
    # resource resolve to no provider/api_url, and the declarations travel
    # with the executor context into isolated child processes (which do not
    # inherit parent monkeypatches).
    settings.resource_providers = ResourceProviderDeclarations()
    settings.executor_runtime = ExecutorRuntimeConfig.model_validate(
        {
            "workflows": {
                "enabled": True,
                "pi": {"binary": pi_binary, "timeout_seconds": pi_timeout}
                if pi_binary is not None
                else {},
            },
            "openclaw": {"command_template": ["openclaw", "agent"]},
        }
    )

    registry = app_main.build_executor_registry(settings, queries)
    leases = ExecutorLeaseRepository(queries.path, data_dir=tmp_path)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=5,
    )

    worker = WorkflowWorkerThread(
        job_db=queries,
        leases=leases,
        registry=registry,
        runtime=runtime,
        settings=settings,
    )
    worker._definitions = [definition]
    return worker, definition


class BadProvider(TranscriptionProvider):
    name = "whisper"

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        raise RuntimeError("forced failure")


class GoodProvider(TranscriptionProvider):
    name = "sensevoice"

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        output_path.write_text(
            "1\n00:00:00,000 --> 00:00:10,000\n第一段讲解。\n\n"
            "2\n00:00:10,000 --> 00:00:20,000\n第二段讲解。\n",
            encoding="utf-8",
        )


class ChapterRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self, phase: Any, video_id: str, video_dir: Path, prompt_dir: Path, log_path: Path
    ) -> Any:
        self.calls += 1
        phase_key = getattr(phase, "key", str(phase))
        (video_dir / "chapters_raw.json").write_text(
            json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "开始"}]),
            encoding="utf-8",
        )
        (video_dir / "chapters.json").write_text(
            json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "开始"}]),
            encoding="utf-8",
        )
        return type(
            "Result",
            (),
            {
                "status": "completed",
                "error_message": "",
                "command": ["openclaw", phase_key, video_id],
            },
        )()


def setup_spa_app(tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path]:
    """Create a temporary project root/data dir and patch create_app to use them."""
    root_dir = tmp_path / "root"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # Copy workflow definitions so the legacy finalizer can resolve the default
    # workspace workflow during app construction.
    import shutil

    real_root = Path(__file__).resolve().parents[2]
    workflows_src = real_root / "config" / "workflows"
    workflows_dst = root_dir / "config" / "workflows"
    workflows_dst.mkdir(parents=True, exist_ok=True)
    for src_file in workflows_src.iterdir():
        if src_file.is_file():
            shutil.copy2(src_file, workflows_dst / src_file.name)

    from server.app import main as app_main
    from server.app.agent_catalog import load_agent_definitions
    from server.app.configuration import load_application_config
    from server.app.workflows.resource_providers import load_resource_provider_declarations
    from tests.postgres_support import TEST_DATABASE_URL

    # The app boots against the isolated test schema with the real Agent
    # catalog: the Settings defaults (public dev database_url, empty catalog)
    # would otherwise make startup sync wipe the dev database's agent state.
    real_config = load_application_config(real_root).config
    agent_definitions = load_agent_definitions(real_config.get("agents", {}))
    # Settings-payload endpoints mask and describe resource bindings with the
    # real provider declarations.
    resource_providers = load_resource_provider_declarations(real_config.get("resource_providers"))

    def fake_load_settings(
        data_dir: Path | None = None, config_path: Path | None = None
    ) -> Settings:
        resolved = data_dir or tmp_path / "data"
        return Settings(
            root_dir=root_dir,
            data_dir=resolved,
            videos_dir=resolved / "videos",
            logs_dir=resolved / "logs",
            packages_dir=resolved / "packages",
            jobs_dir=resolved / "jobs",
            config={},
            database_url=TEST_DATABASE_URL,
            agent_definitions=agent_definitions,
            resource_providers=resource_providers,
        )

    monkeypatch.setattr(app_main, "load_settings", fake_load_settings)
    return root_dir, data_dir


def wait_for_predicate(
    predicate: Callable[[], bool], timeout: float = 5.0, interval: float = 0.01
) -> None:
    """Poll *predicate* until it returns True or *timeout* expires.

    Uses a short sleep between polls; this is acceptable when waiting on
    external thread state, while avoiding arbitrary long sleeps in tests.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("Predicate was not satisfied in time")
        time.sleep(interval)
