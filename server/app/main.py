import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.db.migrations.report import MigrationBlockedError
from server.app.db.notifications import NotificationHub
from server.app.event_bus import InProcessEventBus
from server.app.events import JobEventManager
from server.app.executors.backup import legacy_backup_path
from server.app.executors.config import RemoteExecutorConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.legacy_migration import finalize_legacy_executor_schema
from server.app.executors.registry import ExecutorRegistry, RuntimeDependencies
from server.app.executors.remote_broker import RemoteExecutionBroker
from server.app.executors.remote_completion import RemoteCompletionHandler
from server.app.executors.runtime_factory import build_execution_runtime
from server.app.executors.sweeper import SweeperThread
from server.app.http_middleware import add_http_middleware
from server.app.job_events import build_workspace_event_aggregator
from server.app.jobs import JobQueries
from server.app.local_handler_loader import build_local_handlers
from server.app.pipeline.runners import list_openclaw_agents
from server.app.routes import create_router
from server.app.services.artifact_store import ArtifactStore
from server.app.services.executor_catalog import ExecutorCatalogService
from server.app.services.job_packages import JobPackageService
from server.app.services.package_deletion import PackageDeletionService
from server.app.services.package_stats_backfill import backfill_package_stats
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.services.workspace_pi_agents import sync_workspace_pi_agents
from server.app.settings import Settings, load_settings, validate_settings
from server.app.skills.manager import SkillManager
from server.app.spa import mount_spa
from server.app.startup_tasks import BackgroundTasks
from server.app.worker_control import WorkspaceWorkerControl
from server.app.workflow_worker_thread import WorkflowWorkerThread
from server.app.workflows.registry import list_registered_workflows


def build_executor_registry(
    settings: Settings,
    job_db: Any | None = None,
    remote_broker: RemoteExecutionBroker | None = None,
    artifact_store: ArtifactStore | None = None,
) -> ExecutorRegistry:
    """Build the application-wide executor registry from settings.

    The registry is constructed once per application lifecycle and reused across
    all workspaces. Runtime dependencies (Pi binary, OpenClaw template, local
    handlers) are injected here so adapters remain environment-agnostic.
    """
    skill_manager = SkillManager(
        config_path=settings.root_dir / "config" / "skills.yaml",
        lock_path=settings.root_dir / "config" / "skills.lock",
        base_dir=Path.home() / ".agents" / "skills" / "agent-legion",
    )
    runtime = RuntimeDependencies(
        local_handlers=build_local_handlers(settings),
        pi_runtime=settings.executor_runtime.workflows.pi,
        skill_manager=skill_manager,
        openclaw_runtime=settings.executor_runtime.openclaw,
        settings_config=settings.config,
        job_db=job_db,
        cancellation_grace_seconds=settings.executor_runtime.cancellation_grace_seconds,
        remote_broker=remote_broker,
        artifact_store=artifact_store,
    )
    return ExecutorRegistry.build(settings.executor_definitions, runtime)


def create_app(
    data_dir: Path | None = None,
    start_worker: bool = False,
) -> FastAPI:
    settings = load_settings(data_dir=data_dir)
    event_bus = InProcessEventBus()
    agent_manager = AgentStatusManager(
        event_bus=event_bus,
        discover_agents=lambda: list_openclaw_agents(timeout=10),
    )
    job_event_manager = JobEventManager(event_bus)
    hub = NotificationHub()
    db = Database(settings.data_dir / "video_hive.sqlite", hub=hub, videos_dir=settings.videos_dir)
    job_db = JobQueries(settings.data_dir / "video_hive.sqlite", jobs_dir=settings.jobs_dir)
    workspace_worker_control = WorkspaceWorkerControl(db_path=job_db.path)
    # capability -> requires_labels from every remote executor definition,
    # for label-affinity filtering at dequeue time.
    label_requirements = {
        capability: capability_config.requires_labels
        for definition in settings.executor_definitions.values()
        if isinstance(definition, RemoteExecutorConfig)
        for capability, capability_config in definition.capabilities.items()
        if capability_config.requires_labels
    }
    remote_broker = RemoteExecutionBroker(
        job_db.path,
        settings.data_dir / "remote_bundles",
        claim_timeout_seconds=settings.executor_runtime.remote.claim_timeout_seconds,
        requeue_limit=settings.executor_runtime.remote.requeue_limit,
        capability_label_requirements=label_requirements or None,
    )
    artifact_store = ArtifactStore(settings.data_dir / "artifacts", job_db.path)
    executor_registry = build_executor_registry(
        settings, job_db, remote_broker=remote_broker, artifact_store=artifact_store
    )

    job_event_buffer, workspace_event_aggregator = build_workspace_event_aggregator(
        job_db, settings, job_event_manager.bus
    )
    definitions = list_registered_workflows(settings.root_dir)
    with job_db.connect() as conn:
        try:
            finalize_legacy_executor_schema(
                conn,
                definitions,
                settings.executor_definitions,
                backup_path=legacy_backup_path(job_db.path),
            )
        except MigrationBlockedError as exc:
            check_cmd = (
                "UV_CACHE_DIR=.uv-cache uv run python "
                "scripts/finalize-workspace-executor-migration.py --check"
            )
            logging.getLogger(__name__).error(
                "Workspace executor finalization blocked:\n%s\n\nRun: %s",
                exc.report.to_json(),
                check_cmd,
            )
            raise RuntimeError(
                f"Workspace executor finalization blocked: {exc.report.to_json()}. "
                f"Run `{check_cmd}` for details."
            ) from exc
    backfill_package_stats(db, settings)
    workflow_worker_thread: WorkflowWorkerThread | None = None
    sweeper_thread: SweeperThread | None = None
    background_tasks = BackgroundTasks(
        workspace_event_aggregator=workspace_event_aggregator,
        agent_broadcast_controller=agent_manager.broadcast_controller,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal workflow_worker_thread, sweeper_thread
        job_event_manager.bus.attach_loop(asyncio.get_running_loop())
        if start_worker:
            validate_settings(settings)
            agent_manager.discover()
            sync_workspace_pi_agents(job_db, settings, agent_manager)
            if WorkflowWorkerThread.is_enabled(settings):
                executor_leases = ExecutorLeaseRepository(
                    job_db.path,
                    job_db=job_db,
                    job_event_manager=job_event_manager,
                    job_event_buffer=job_event_buffer,
                )
                execution_runtime = build_execution_runtime(
                    executor_leases, executor_registry, settings.executor_runtime
                )
                # Submit-only remote executors finish leases via broker callbacks.
                remote_broker.register_completion_callback(
                    RemoteCompletionHandler(
                        remote_broker,
                        executor_leases,
                        settings.jobs_dir,
                        artifact_store=artifact_store,
                    ).handle_completion
                )
                # The sweeper owns all lease hygiene (startup + interval sweeps,
                # remote lease renewal). With sweeper_enabled=False an external
                # sweeper process must run instead (multi-replica deployments).
                if settings.executor_runtime.sweeper_enabled:
                    sweeper_thread = SweeperThread(
                        executor_leases,
                        remote_broker,
                        interval_seconds=settings.executor_runtime.sweeper_interval_seconds,
                        lease_ttl_seconds=settings.executor_runtime.lease_ttl_seconds,
                    )
                    try:
                        sweeper_thread.start()
                    except Exception:
                        logging.getLogger(__name__).exception("sweeper failed to start")
                        sweeper_thread = None
                workflow_worker_thread = WorkflowWorkerThread(
                    job_db=job_db,
                    leases=executor_leases,
                    registry=executor_registry,
                    runtime=execution_runtime,
                    settings=settings,
                    workspace_worker_control=workspace_worker_control,
                    agent_manager=agent_manager,
                )
                try:
                    workflow_worker_thread.start()
                except Exception:
                    logging.getLogger(__name__).exception("workflow worker failed to start")
        background_tasks.start(app)
        yield
        background_tasks.stop(app)
        if sweeper_thread is not None:
            sweeper_thread.stop()
        if workflow_worker_thread is not None:
            workflow_worker_thread.stop()

    app = FastAPI(title="Agent Legion", lifespan=lifespan)
    add_http_middleware(app, settings)
    app.state.settings = settings
    app.state.db = db
    app.state.job_db = job_db
    app.state.executor_registry = executor_registry
    app.state.remote_broker = remote_broker
    app.state.artifact_store = artifact_store
    app.state.agent_manager = agent_manager
    app.state.workspace_worker_control = workspace_worker_control
    app.state.job_event_manager = job_event_manager
    app.state.event_bus = job_event_manager.bus
    app.state.job_event_buffer = job_event_buffer
    app.state.workspace_event_aggregator = workspace_event_aggregator
    workflow_catalog = WorkflowCatalogService(settings)
    executor_catalog = ExecutorCatalogService(settings)
    workspace_executor_configuration = WorkspaceExecutorConfigurationService(job_db)
    workspace_configuration = WorkspaceConfigurationService(
        job_db, settings, agent_manager, workflow_catalog
    )
    package_deletion = PackageDeletionService(db, settings.packages_dir)
    job_packages = JobPackageService(job_db, settings)
    app.include_router(
        create_router(
            db,
            job_db,
            settings,
            agent_manager,
            workspace_worker_control,
            workflow_catalog=workflow_catalog,
            executor_catalog=executor_catalog,
            workspace_executor_configuration=workspace_executor_configuration,
            workspace_configuration=workspace_configuration,
            package_deletion=package_deletion,
            job_packages=job_packages,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
            remote_broker=remote_broker,
            artifact_store=artifact_store,
        )
    )
    mount_spa(app, settings.root_dir / "frontend" / "dist")
    return app


app = create_app(start_worker=True)
