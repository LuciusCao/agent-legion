"""Sweeper / workflow worker thread startup for the app composition root."""

import logging
from functools import partial

from server.app.agent_broker import AgentDispatchService, AgentExecutionBroker
from server.app.agent_broker.code_dispatch import CodeDispatchService
from server.app.events.agents import AgentStatusManager
from server.app.executors.code import CodeExecutor
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.sweeper import SweeperThread
from server.app.jobs import JobQueries
from server.app.scheduler_wakeup import register_wakeup
from server.app.services.health_status import record_pure_remote_startup
from server.app.services.path_hygiene import report_absolute_db_paths_background
from server.app.settings import Settings
from server.app.worker_control import WorkspaceWorkerControl
from server.app.workflow_worker.agent_gate import request_restock
from server.app.workflow_worker.thread import WorkflowWorkerThread
from shared.material_cache import MATERIALS_CACHE_DIRNAME

logger = logging.getLogger(__name__)


def start_worker_threads(
    settings: Settings,
    *,
    job_db: JobQueries,
    executor_leases: ExecutorLeaseRepository,
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
    # changes, so surface them at startup instead of mid-incident. The count
    # queries seq-scan jobs/node_runs, so the report runs on a background
    # thread — readiness must not wait on it (issue #106).
    report_absolute_db_paths_background(job_db)
    worker_startup: dict[str, str] = {}
    # ``workflows.enabled`` is retired (#385/#389): the worker always starts;
    # the deployment shape is expressed by code_capacity below.
    # Pure-remote mode (#389): code_capacity == 0 assembles NO local executor
    # stack — no velites sandbox subprocesses, no thread pool, no local
    # heartbeat loop. The workflow worker still runs (it is the scheduler:
    # ready scan, routing, remote dispatch, approval gates), but every code
    # node must execute on a remote code-capable Worker. The health endpoint
    # surfaces the online code-Worker count so a stalled pure-remote fleet is
    # visible (tasks queue silently when no Worker is online).
    pure_remote = settings.executor_runtime.code_capacity <= 0
    execution_runtime: ExecutionRuntime | None = None
    if not pure_remote:
        # P-0.5: the single implicit code pool — one CodeExecutor, assembled
        # directly; the executor registry/kinds machinery is retired (v47).
        code_executor = CodeExecutor(
            repo_root=settings.root_dir,
            settings_config=settings.config,
            job_db=job_db,
            cancellation_grace_seconds=settings.executor_runtime.cancellation_grace_seconds,
            # Materialization cache lives under the instance data dir (design
            # §6.2) so it is covered by the same data-volume lifecycle.
            materials_cache_root=settings.data_dir / MATERIALS_CACHE_DIRNAME,
        )
        execution_runtime = ExecutionRuntime(
            executor_leases,
            code_executor,
            heartbeat_interval_seconds=settings.executor_runtime.heartbeat_interval_seconds,
            lease_ttl_seconds=settings.executor_runtime.lease_ttl_seconds,
            heartbeat_failure_threshold=settings.executor_runtime.heartbeat_failure_threshold,
            cancellation_grace_seconds=settings.executor_runtime.cancellation_grace_seconds,
        )
    if pure_remote:
        record_pure_remote_startup(worker_startup, job_db)
    sweeper_thread: SweeperThread | None = None
    # The sweeper owns all lease hygiene; sweeper_enabled=False means
    # an external sweeper process (multi-replica deployments).
    if settings.executor_runtime.sweeper_enabled:
        sweeper_thread = SweeperThread(
            executor_leases,
            agent_broker,
            interval_seconds=settings.executor_runtime.sweeper_interval_seconds,
            lease_ttl_seconds=settings.executor_runtime.lease_ttl_seconds,
            # Leak GC: skill execution snapshots orphaned by a hard crash
            # between copytree and the finally-cleanup (no other reaper
            # covers the runs dir).
            skill_sweeper=agent_dispatch.skill_manager.sweep_stale_executions,
        )
        try:
            sweeper_thread.start()
        except Exception:
            # #204 broad-except audit: startup must not abort on a thread
            # that failed to spawn — the docstring pins the contract
            # (failures are logged and recorded in the status map that
            # /api/health surfaces). thread.start()'s failure space is the
            # runtime one (RuntimeError: can't start new thread, OSError on
            # resource exhaustion) plus the sweeper's own synchronous
            # startup sweep; none is a business family, and converting any
            # of them to a crash would take the whole control plane down
            # with a degraded component that the operator can see in the
            # health payload. logger.exception keeps the traceback.
            logger.exception("sweeper failed to start")
            sweeper_thread = None
            worker_startup["sweeper"] = "failed"
        else:
            worker_startup["sweeper"] = "running"
    workflow_worker_thread = WorkflowWorkerThread(
        job_db=job_db,
        leases=executor_leases,
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
        # #204 broad-except audit: same startup contract as the sweeper
        # catch above — a failed spawn (runtime/OS-level thread errors, the
        # worker's reload_scan_entries on start) degrades to a "failed"
        # health entry instead of crashing app startup: the API plane stays
        # up and readable (the composition root deliberately records instead
        # of raising), and the operator sees which thread is down. The
        # traceback is preserved via logger.exception.
        logger.exception("workflow worker failed to start")
        worker_startup["workflow_worker"] = "failed"
    else:
        worker_startup["workflow_worker"] = "running"
        register_wakeup(workflow_worker_thread.wake)
        # Empty Worker claims demand immediate restocking (debounced).
        agent_broker.empty_claim.on_empty_queue = partial(request_restock, workflow_worker_thread)
    return sweeper_thread, workflow_worker_thread, worker_startup
