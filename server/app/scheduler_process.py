"""Scheduler-plane process entry (#521 方案 B).

``python -m server.app.scheduler_process`` — the role split's dedicated
scheduler: sweeper + workflow worker + slow sweeps + ops-metrics sampling
in one process, WITHOUT the HTTP app. The HTTP plane
(``AGENT_LEGION_HOST_ROLE=http``) keeps serving the API (result commits,
claims, heartbeats, dashboard) in its own process, so a completion wave's
GIL-bound commit work can no longer starve the claim/heartbeat loop —
they no longer share a Python process.

The plane composition reuses ``create_app``'s construction order
(settings → hydrate → migrate → agent plane → threads): the
app-construction side effects are idempotent and advisory-locked, and
the scheduler may boot before any HTTP process (compose `depends_on`
orders the reverse). HTTP-plane facilities (FastAPI app/routes, studio
chat, SPA) stay behind; this process takes the scheduler-plane lock
slot instead. The LISTEN bridge (``scheduler_notify``) turns NOTIFY
from the HTTP plane into local wakeups; the scheduler's own write paths
use the in-process registry directly.
"""

from __future__ import annotations

import logging
import signal
import threading
from functools import partial

from server.app.bootstrap import build_agent_plane, plane_bridges
from server.app.configuration.host_role import ROLE_SCHEDULER, host_role_from_env
from server.app.db.connection import close_database_pools
from server.app.events import JobEventManager
from server.app.events.agents import AgentStatusManager
from server.app.events.aggregator import build_workspace_event_aggregator
from server.app.events.bus import InProcessEventBus
from server.app.jobs import JobQueries
from server.app.scheduler_notify import SchedulerNotifyListener
from server.app.services.artifact_store import ArtifactStore
from server.app.services.demo_node_migration import migrate_demo_node_codes_to_workspaces
from server.app.services.instance_settings import apply_instance_settings
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.ops_metrics import OpsMetricsService
from server.app.settings import load_settings, validate_settings
from server.app.single_replica_probe import SingleReplicaProbe
from server.app.skills.skill_sources_retirement import retire_skill_sources_document
from server.app.storage import build_s3_storage_checked
from server.app.sweeper_owned_startup import start_sweeper_owned_threads
from server.app.worker_control import WorkspaceWorkerControl
from server.app.worker_startup import start_worker_threads
from server.app.workflow_worker.agent_gate import request_restock

logger = logging.getLogger("agent_legion.scheduler")


def run_scheduler_process() -> int:
    """Compose and run the scheduler plane until SIGTERM/SIGINT; returns exit code."""
    role = host_role_from_env()
    if role != ROLE_SCHEDULER:
        logger.error("scheduler_process requires AGENT_LEGION_HOST_ROLE=scheduler (got %r)", role)
        return 2

    plane_bridges.configure_scheduler_logging()

    settings = load_settings()
    job_db = JobQueries(settings.database_url, jobs_dir=settings.jobs_dir)
    apply_instance_settings(settings, job_db)
    migrate_demo_node_codes_to_workspaces(settings, job_db)
    retire_skill_sources_document(job_db)

    # Scheduler-plane lock (#521 方案 B): the http plane holds the
    # control-plane slot; two scheduler processes against one database
    # contend on this one. Same probe lifetime semantics as the app.
    replica_probe = SingleReplicaProbe(job_db, lock_name="scheduler")
    replica_probe.probe()

    event_bus = InProcessEventBus()
    agent_manager = AgentStatusManager(event_bus=event_bus)
    job_event_manager = JobEventManager(event_bus)
    # The JobEventBuffer here is a construction dependency of the agent
    # plane (lease transitions record into it), but nothing drains it:
    # the SSE delivery loop is an HTTP-plane facility. Every recorded
    # event is therefore ALSO relayed over the ``job_touched`` NOTIFY
    # bridge (wired below via cross_plane_event) so the http plane's
    # buffer — the one its SSE clients drain — records the same touch.
    job_event_buffer, _aggregator = build_workspace_event_aggregator(
        job_db, settings, job_event_manager.bus
    )
    workspace_worker_control = WorkspaceWorkerControl(db_path=job_db)
    # Resume state must not survive a restart (same invariant as the
    # combined-role app): dispatch stays off until an operator explicitly
    # resumes it. The scheduler process owns this reset in the split
    # shape — the http plane deliberately skips it so a rolling restart
    # of the API does not wipe an operator's just-restored dispatch.
    workspace_worker_control.reset_all_to_paused()
    artifact_store = ArtifactStore(settings.data_dir / "artifacts", job_db)
    object_storage = build_s3_storage_checked()
    job_artifact_objects = JobArtifactObjectStore(job_db, object_storage)
    agent_plane = build_agent_plane(
        job_db,
        settings,
        agent_manager,
        workspace_worker_control,
        artifact_store,
        job_event_manager,
        job_event_buffer,
        object_store=job_artifact_objects,
        # #521 方案 B: relay this process's recorded job events to the
        # http plane over the NOTIFY bridge (its SSE clients never see
        # this process's buffer). Best-effort by the emitter's contract.
        cross_plane_event=plane_bridges.relay_job_event_emitter(job_db),
    )

    validate_settings(settings)
    agent_manager.discover()
    sweeper_thread, workflow_worker_thread, worker_status = start_worker_threads(
        settings,
        job_db=job_db,
        executor_leases=agent_plane.executor_leases,
        agent_broker=agent_plane.broker,
        workspace_worker_control=workspace_worker_control,
        agent_manager=agent_manager,
        agent_dispatch=agent_plane.dispatch,
    )
    for name, status in sorted(worker_status.items()):
        logger.info("scheduler plane: %s=%s", name, status)

    # #521 方案 B (codex P1): a failed workflow worker is FATAL — this
    # plane would read ready while scheduling nothing (compose restart
    # only fires on exit; the launcher probes only HTTP/Worker). Exit
    # non-zero so the supervisor retries. A failed sweeper only degrades
    # (lease expiry falls back to TTL).
    if worker_status.get("workflow_worker") == "failed":
        logger.error(
            "scheduler plane: workflow worker failed to start — exiting for supervisor retry"
        )
        if sweeper_thread is not None:
            sweeper_thread.stop()
        replica_probe.close()
        close_database_pools()
        return 3
    if worker_status.get("sweeper") == "failed":
        logger.warning(
            "scheduler plane: sweeper failed to start — lease hygiene degrades to TTL expiry"
        )

    slow_sweeps: tuple = ()
    if settings.executor_runtime.sweeper_enabled:
        slow_sweeps = start_sweeper_owned_threads(
            artifact_store, job_artifact_objects, job_db, settings, object_storage
        )

    notify_listener = SchedulerNotifyListener(
        job_db,
        # scan_reload payloads reload the worker's scan list before the
        # wake: workspaces created on the http plane after this process
        # booted otherwise never enter the scan snapshot (P1 on review).
        on_scan_reload=(
            workflow_worker_thread.reload_scan_entries
            if workflow_worker_thread is not None
            else None
        ),
        # restock payloads restore the combined role's request_restock
        # semantics for the http plane's debounced empty-claim signal:
        # expire the agent-stock snapshot, then wake (N2 on review).
        on_restock=(
            partial(request_restock, workflow_worker_thread)
            if workflow_worker_thread is not None
            else None
        ),
    )
    notify_listener.start()

    # Ops-metrics sampling loop (#521 方案 B): the sampler lives HERE — the
    # minute rows are per-process upserts, and the scheduler process is the
    # single sampler of the deployment. The catch-up/retention semantics
    # are shared via OpsMetricsService; the loop scaffolding below is a
    # thread-shaped mirror of ops_metrics_background.run_ops_metrics_loop.
    stop_event = threading.Event()
    ops_metrics = OpsMetricsService(job_db, settings.config)
    sampling_thread = threading.Thread(
        target=_ops_metrics_loop, args=(ops_metrics, stop_event), daemon=True
    )
    sampling_thread.start()

    def _request_stop(signum, _frame) -> None:  # type: ignore[no-untyped-def]
        logger.info("scheduler plane: received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    logger.info("scheduler plane running (sweeper + workflow worker + sampling)")
    try:
        stop_event.wait()
    except KeyboardInterrupt:  # pragma: no cover - signal path sets the event
        pass
    finally:
        notify_listener.stop()
        # Let an in-flight sampling write settle before the pools close
        # (bounded, same discipline as the app's BackgroundTasks.stop);
        # an unjoined daemon would hit a closing pool mid-transaction.
        sampling_thread.join(timeout=5.0)
        for thread in (sweeper_thread, *(slow_sweeps or ())):
            if thread is not None:
                thread.stop()
        if workflow_worker_thread is not None:
            from server.app.scheduler_wakeup import unregister_wakeup

            unregister_wakeup(workflow_worker_thread.wake)
            workflow_worker_thread.stop()
        replica_probe.close()
        close_database_pools()
    return 0


def _ops_metrics_loop(ops_metrics: OpsMetricsService, stop_event: threading.Event) -> None:
    """Thread wrapper over the app's sampling loop semantics.

    Mirrors ``run_ops_metrics_loop``'s catch-up + cleanup + interval
    discipline with a stop event instead of asyncio cancellation; a
    sampling failure logs and retries next interval (the catch-up pass
    fills any gaps the failure left).
    """
    interval = ops_metrics.sample_interval_seconds
    while not stop_event.is_set():
        try:
            ops_metrics.sample_catch_up()
            ops_metrics.cleanup_expired()
        except Exception:
            # #204 broad-except audit: the metrics loop's life support,
            # same discipline as run_ops_metrics_loop — dying would
            # leave permanent gaps in the ops series (the catch-up pass
            # is the anti-gap mechanism), so log-and-retry is the
            # containment; the outcome space is the DB write surface of
            # sample_catch_up/cleanup_expired, not a business family.
            logger.exception("ops metrics sampling failed; retrying next interval")
        stop_event.wait(interval)


if __name__ == "__main__":
    raise SystemExit(run_scheduler_process())
