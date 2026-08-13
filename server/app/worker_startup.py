"""Sweeper / workflow worker thread startup for the app composition root."""

import logging
from functools import partial

from server.app.agent_broker import AgentDispatchService, AgentExecutionBroker
from server.app.agent_broker.code_dispatch import CodeDispatchService
from server.app.events.agents import AgentStatusManager
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.sweeper import SweeperThread
from server.app.jobs import JobQueries
from server.app.scheduler_wakeup import register_wakeup
from server.app.services.path_hygiene import report_absolute_db_paths
from server.app.settings import Settings
from server.app.worker_control import WorkspaceWorkerControl
from server.app.workflow_worker.agent_gate import request_restock
from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


def start_worker_threads(
    settings: Settings,
    *,
    job_db: JobQueries,
    executor_leases: ExecutorLeaseRepository,
    executor_registry: ExecutorRegistry,
    agent_broker: AgentExecutionBroker,
    workspace_worker_control: WorkspaceWorkerControl,
    agent_manager: AgentStatusManager,
    agent_dispatch: AgentDispatchService,
) -> tuple[SweeperThread | None, WorkflowWorkerThread | None, dict[str, str]]:
    """Start the sweeper and workflow worker threads.

    Startup failures are logged and recorded in the returned status map
    (surfaced via /api/health) instead of aborting app startup.
    """
    # DB path columns must hold data-dir-relative values only; legacy
    # absolute rows (bare-metal era, issue #37) break on deployment shape
    # changes, so surface them at startup instead of mid-incident.
    report_absolute_db_paths(job_db)
    worker_startup: dict[str, str] = {}
    if not WorkflowWorkerThread.is_enabled(settings):
        return None, None, worker_startup
    execution_runtime = ExecutionRuntime(
        executor_leases,
        executor_registry,
        heartbeat_interval_seconds=settings.executor_runtime.heartbeat_interval_seconds,
        lease_ttl_seconds=settings.executor_runtime.lease_ttl_seconds,
        heartbeat_failure_threshold=settings.executor_runtime.heartbeat_failure_threshold,
        cancellation_grace_seconds=settings.executor_runtime.cancellation_grace_seconds,
    )
    sweeper_thread: SweeperThread | None = None
    # The sweeper owns all lease hygiene; sweeper_enabled=False means
    # an external sweeper process (multi-replica deployments).
    if settings.executor_runtime.sweeper_enabled:
        sweeper_thread = SweeperThread(
            executor_leases,
            agent_broker,
            interval_seconds=settings.executor_runtime.sweeper_interval_seconds,
            lease_ttl_seconds=settings.executor_runtime.lease_ttl_seconds,
        )
        try:
            sweeper_thread.start()
        except Exception:
            logger.exception("sweeper failed to start")
            sweeper_thread = None
            worker_startup["sweeper"] = "failed"
        else:
            worker_startup["sweeper"] = "running"
    workflow_worker_thread = WorkflowWorkerThread(
        job_db=job_db,
        leases=executor_leases,
        registry=executor_registry,
        runtime=execution_runtime,
        settings=settings,
        workspace_worker_control=workspace_worker_control,
        agent_manager=agent_manager,
        agent_dispatch=agent_dispatch,
        # The code dispatch shares the agent dispatch's broker and artifact
        # store (same instances, same composition root).
        code_dispatch=CodeDispatchService(
            settings, agent_broker, agent_dispatch.artifact_store, job_db
        ),
    )
    try:
        workflow_worker_thread.start()
    except Exception:
        logger.exception("workflow worker failed to start")
        worker_startup["workflow_worker"] = "failed"
    else:
        worker_startup["workflow_worker"] = "running"
        register_wakeup(workflow_worker_thread.wake)
        # Empty Worker claims demand immediate restocking (debounced).
        agent_broker.empty_claim.on_empty_queue = partial(request_restock, workflow_worker_thread)
    return sweeper_thread, workflow_worker_thread, worker_startup
