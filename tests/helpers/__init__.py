from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.settings import Settings, load_settings
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.builtin import load_builtin_workflow
from server.app.workflows.definition import WorkflowDefinition


def load_builtin_definition(workflow_key: str) -> WorkflowDefinition:
    """Load a built-in workflow DAG definition (retired config/workflows yaml)."""
    return load_builtin_workflow(workflow_key)


def load_demo_legacy_intake_definition() -> WorkflowDefinition:
    """Demo DAG plus the legacy intake block retired in issue #154.

    The demo workflow no longer declares intake modes; tests that still
    exercise the job-batches intake service publish this variant so
    ``source_kind: direct_ids`` resolves against the active revision.
    """
    from dataclasses import replace

    from server.app.workflows.definition import WorkflowIntake, WorkflowIntakeMode

    definition = load_builtin_workflow("education_video_problems_generation")
    return replace(
        definition,
        intake=WorkflowIntake(
            modes={
                "direct_ids": WorkflowIntakeMode(
                    key="direct_ids",
                    label="按知识点批量",
                    input_field="knowledge_point_ids",
                )
            }
        ),
    )


def publish_legacy_intake_revision(job_db: JobQueries, workspace_id: str) -> dict[str, Any]:
    """Publish the legacy-intake demo variant as the workspace's active revision.

    API workspace creation seeds the intake-less demo revision; tests that
    post job-batches call this right after creation so the legacy intake
    service path keeps resolving ``direct_ids``.
    """
    from server.app.services.workflow_revisions import WorkflowRevisionService

    return WorkflowRevisionService(job_db).publish_workspace_revision(
        workspace_id, load_demo_legacy_intake_definition()
    )


def publish_builtin_revision(
    job_db: JobQueries,
    workspace_id: str,
    workflow_key: str = "education_video_problems_generation",
) -> dict[str, Any]:
    """Publish the built-in definition as the workspace's active revision.

    Mirrors the workspace-create demo seed (ensure_active_revision is
    seed-if-absent): tests that bypass the API and create the workspace row
    directly use this so definition resolution (workspace active revision,
    schema v50) sees the DAG.
    """
    from server.app.services.demo_node_seed import seed_demo_workspace_node_codes
    from server.app.services.workflow_revisions import WorkflowRevisionService

    seed_demo_workspace_node_codes(load_settings(), workspace_id)
    return WorkflowRevisionService(job_db).ensure_active_revision(
        workspace_id, load_builtin_workflow(workflow_key)
    )


def scan_entries(*definitions: WorkflowDefinition) -> list[tuple[str, str, WorkflowDefinition]]:
    """Hand-built worker scan entries for tests (workspace id is a placeholder:
    collect falls back to the by-key definition for unknown workspaces)."""
    return [("test-ws", d.key, d) for d in definitions]


def ensure_legacy_workspace_tables(db_or_conn: Any) -> None:
    """Compatibility no-op for tests that predate authoritative configuration."""
    del db_or_conn


def make_workflow_worker(
    tmp_path: Path,
    queries: JobQueries,
    *,
    workflow_key: str = "education_video_problems_generation",
) -> tuple[WorkflowWorkerThread, WorkflowDefinition]:
    """Build a configured WorkflowWorkerThread for *workflow_key*."""
    from server.app import main as app_main
    from server.app.executors.leases import ExecutorLeaseRepository
    from server.app.executors.runtime import ExecutionRuntime

    definition = load_builtin_definition(workflow_key)
    settings = app_main.load_settings(data_dir=tmp_path)
    settings.executor_runtime = ExecutorRuntimeConfig.model_validate(
        {
            "workflows": {"enabled": True},
        }
    )

    # P-0.5: 单一隐含 code 池，直接装配。
    from server.app.executors.code import CodeExecutor

    executor = CodeExecutor(
        repo_root=settings.root_dir,
        settings_config=settings.config,
        job_db=queries,
    )
    leases = ExecutorLeaseRepository(queries, data_dir=tmp_path)
    runtime = ExecutionRuntime(
        leases=leases,
        executor=executor,
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=5,
    )

    worker = WorkflowWorkerThread(
        job_db=queries,
        leases=leases,
        runtime=runtime,
        settings=settings,
    )
    worker.state.scan_entries = scan_entries(definition)
    return worker, definition


def setup_spa_app(tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path]:
    """Create a temporary project root/data dir and patch create_app to use them."""
    root_dir = tmp_path / "root"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    from server.app import main as app_main
    from tests.postgres_support import TEST_DATABASE_URL

    # The app boots against the isolated test schema; Agent definitions are
    # workspace-scoped (schema v46), seeded per workspace by the tests that
    # need them (tests/helpers.seed_workspace_agent_definitions).
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
    # The fake root has no workflow_nodes/: skip the compatibility migration
    # (SPA tests never dispatch jobs).
    monkeypatch.setattr(
        app_main, "migrate_demo_node_codes_to_workspaces", lambda settings, job_db: 0
    )
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


def pid_is_running(pid: int) -> bool:
    """True when *pid* names a live (non-zombie) process.

    os.kill(pid, 0) alone also succeeds for zombies: an unreaped orphan
    keeps its PID until somebody wait()s it, so a kill-probe liveness loop
    spins to its deadline in containers without a reaping init. On Linux,
    consult /proc for the state field; elsewhere fall back to the signal
    probe (macOS launchd reaps orphans promptly).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return True
    # comm may contain spaces and parens; the state letter is the first
    # field after the final ')'.
    tail = stat.rsplit(")", 1)[1] if ")" in stat else ""
    return tail.split()[0] != "Z" if tail else True


def seed_workspace_agent_definitions(workspace_id: str) -> list[str]:
    """Seed the built-in demo Agent definitions into *workspace_id*.

    Agent definitions are workspace-scoped (schema v46): the conftest reset no
    longer seeds a global catalog, so tests that run the demo workflow end to
    end instantiate the factory templates into their own workspace here.
    """
    from server.app.agent_catalog.builtin import seed_demo_workspace_agent_definitions
    from tests.postgres_support import TEST_DATABASE_URL

    return seed_demo_workspace_agent_definitions(TEST_DATABASE_URL, workspace_id)


def replace_agent_catalog(workspace_ids: str | list[str], definitions: dict[str, Any]) -> None:
    """Archive every live Agent version in the given workspaces, then insert
    *definitions* as published into each of them.

    Mirrors the retired ``sync_agent_definitions`` replace semantics for
    tests, workspace-scoped (schema v46): after the call exactly the given
    catalog is published in each listed workspace. An empty mapping leaves no
    published Agents in those workspaces. Missing workspace rows are created
    (the versioned_entities workspace FK requires the row first). Writes go
    straight to versioned_entities so tests can stage catalogs the
    service-level publish guard would reject (e.g. two published Agents
    sharing one capability for dual-runtime fleets).
    """
    import json as _json

    from server.app.agent_catalog import AgentDefinition
    from server.app.db.transaction import write_transaction
    from server.app.services.agent_service import reset_published_agent_cache
    from tests.postgres_support import TEST_DATABASE_URL

    ids = [workspace_ids] if isinstance(workspace_ids, str) else list(workspace_ids)
    with write_transaction(TEST_DATABASE_URL) as conn:
        for workspace_id in ids:
            conn.execute(
                "insert into workspaces(id, name, default_workflow_key)"
                " values (%s, 'Test', 'demo_workflow') on conflict(id) do nothing",
                (workspace_id,),
            )
            conn.execute(
                "update versioned_entities set status='archived'"
                " where entity_type='agent' and workspace_id=%s"
                " and status in ('draft', 'published')",
                (workspace_id,),
            )
            for agent_id, definition in definitions.items():
                assert isinstance(definition, AgentDefinition)
                latest = conn.execute(
                    "select max(version) as v from versioned_entities"
                    " where entity_type='agent' and workspace_id=%s and entity_key=%s",
                    (workspace_id, agent_id),
                ).fetchone()
                version = (
                    int(latest["v"]) + 1 if latest is not None and latest["v"] is not None else 1
                )
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
                    " values (%s, 'agent', %s, %s, %s, 'published', %s, %s, 'test-seed',"
                    " current_timestamp, current_timestamp)",
                    (
                        f"agent:{workspace_id}:{agent_id}:v{version}",
                        workspace_id,
                        agent_id,
                        version,
                        canonical,
                        definition.definition_hash(),
                    ),
                )
    reset_published_agent_cache()
