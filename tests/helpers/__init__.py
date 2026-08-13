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
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.builtin import load_builtin_workflow
from server.app.workflows.definition import WorkflowDefinition


def load_builtin_definition(workflow_key: str) -> WorkflowDefinition:
    """Load a built-in workflow DAG definition (retired config/workflows yaml)."""
    return load_builtin_workflow(workflow_key)


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

    definition = load_builtin_definition(workflow_key)
    settings = app_main.load_settings(data_dir=tmp_path)
    # Executor definitions are DB-backed: hydrate the seeded catalog (the app
    # does this in create_app; this helper builds the registry directly).
    app_main.hydrate_executor_definitions(settings)
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
    worker._scan_entries = ([definition], [])
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

    from server.app import main as app_main
    from tests.postgres_support import TEST_DATABASE_URL

    # The app boots against the isolated test schema; conftest already seeded
    # the published Agent catalog there via AgentService.
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
        )

    monkeypatch.setattr(app_main, "load_settings", fake_load_settings)
    # The fake root has no workflow_nodes/: skip the executor definition
    # hydration (SPA tests never dispatch jobs).
    monkeypatch.setattr(app_main, "hydrate_executor_definitions", lambda settings: None)
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


def replace_agent_catalog(definitions: dict[str, Any]) -> None:
    """Archive every live Agent version, then insert *definitions* as published.

    Mirrors the retired ``sync_agent_definitions`` replace semantics for
    tests: after the call exactly the given catalog is published. An empty
    mapping leaves no published Agents (the old empty-catalog guard went away
    with the YAML sync). Writes go straight to versioned_entities so tests
    can stage catalogs the service-level publish guard would reject (e.g.
    two published Agents sharing one capability for dual-runtime fleets).
    """
    import json as _json

    from server.app.agent_catalog import AgentDefinition
    from server.app.db.transaction import write_transaction
    from server.app.services.agent_service import reset_published_agent_cache
    from tests.postgres_support import TEST_DATABASE_URL

    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update versioned_entities set status='archived'"
            " where entity_type='agent' and status in ('draft', 'published')"
        )
        for agent_id, definition in definitions.items():
            assert isinstance(definition, AgentDefinition)
            latest = conn.execute(
                "select max(version) as v from versioned_entities"
                " where entity_type='agent' and workspace_id is null and entity_key=%s",
                (agent_id,),
            ).fetchone()
            version = int(latest["v"]) + 1 if latest is not None and latest["v"] is not None else 1
            canonical = _json.dumps(
                definition.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.execute(
                "insert into versioned_entities("
                "id, entity_type, workspace_id, entity_key, version, status,"
                " definition_json, definition_hash, created_by, created_at, published_at)"
                " values (%s, 'agent', null, %s, %s, 'published', %s, %s, 'test-seed',"
                " current_timestamp, current_timestamp)",
                (
                    f"agent:{agent_id}:v{version}",
                    agent_id,
                    version,
                    canonical,
                    definition.definition_hash(),
                ),
            )
    reset_published_agent_cache()
