from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

from server.app.agent_dispatch import AgentDispatchService
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.scheduling.capacity import load_capacity_snapshot
from server.app.executors.scheduling.fair import WorkspaceRoundRobin
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.workflow_worker_execution import reap_futures
from server.app.workflow_worker_maintenance import WorkflowMaintenance
from server.app.workflow_worker_ready import build_ready_queues
from server.app.workflow_worker_routing import NodeRoute
from server.app.workflow_worker_schedule import claim_next_candidate
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.registry import list_registered_workflows

logger = logging.getLogger(__name__)


class WorkflowWorkerThread:
    def __init__(
        self,
        job_db: JobQueries,
        leases: ExecutorLeaseRepository,
        registry: ExecutorRegistry,
        runtime: ExecutionRuntime,
        settings: Settings,
        workspace_worker_control: Any | None = None,
        agent_manager: Any | None = None,
        agent_dispatch: AgentDispatchService | None = None,
    ):
        self.job_db = job_db
        self.leases = leases
        self.registry = registry
        self.executor_registry = registry  # compatibility alias for tests/lifespan
        self.runtime = runtime
        self.settings = settings
        self.workspace_worker_control = workspace_worker_control
        self.agent_manager = agent_manager
        self.agent_dispatch = agent_dispatch
        self.stop_event = threading.Event()
        # Set whenever a claimed execution finishes; the poll loop waits on
        # this so freed capacity is refilled immediately instead of after the
        # idle backoff.
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._definitions: list[WorkflowDefinition] = []
        self._pools: dict[str, ThreadPoolExecutor] = {}
        self._futures: dict[str, Future[ExecutionResult | None]] = {}
        self._round_robin = WorkspaceRoundRobin()
        self._maintenance = WorkflowMaintenance(job_db, settings)
        # Cross-pass caches: parsed workflow definitions keyed by
        # workflow_definition_hash, and ready-node evaluations keyed by job
        # id, reused while a job's lightweight scan mark is unchanged.
        self._definition_cache: dict[str, WorkflowDefinition | None] = {}
        self._job_evals: dict[str, tuple[tuple[Any, ...], list[Any]]] = {}
        self._last_ready_stats: dict[str, int] = {"hit": 0, "miss": 0}
        # Short-TTL node routing resolutions for the claim path; see
        # server.app.workflow_worker_routing.
        self._route_cache: dict[tuple[str, str, str], tuple[float, NodeRoute]] = {}

    @staticmethod
    def is_enabled(settings: Settings) -> bool:
        return settings.executor_runtime.workflows.enabled

    def _ensure_pools(self) -> None:
        for executor_id in self.registry.definitions():
            if executor_id not in self._pools:
                capacity = self.registry.global_capacity(executor_id) or 1
                self._pools[executor_id] = ThreadPoolExecutor(max_workers=capacity)

    def _pool_for(self, executor_id: str) -> ThreadPoolExecutor:
        return self._pools[executor_id]

    def _executor_capacities(self) -> dict[str, int]:
        return {eid: self.registry.global_capacity(eid) or 0 for eid in self.registry.definitions()}

    def start(self) -> None:
        self._definitions = list_registered_workflows(self.settings.root_dir)
        self._ensure_pools()
        self._maintenance.run_backfill()

        def _loop() -> None:
            while not self.stop_event.is_set():
                try:
                    processed = self._poll()
                except Exception:
                    logger.exception("workflow worker poll failed")
                    processed = False
                # Wait on the wake event (set by finishing executions and by
                # stop()) so freed capacity is refilled without waiting out
                # the full idle backoff.
                self._wake_event.wait(0.2 if processed else 3)
                self._wake_event.clear()

        self._thread = threading.Thread(target=_loop, name="workflow-worker", daemon=True)
        self._thread.start()

    def _poll(self) -> bool:
        self._maintenance.maybe_cleanup()
        if not self._definitions:
            return False

        if not self._pools:
            self._ensure_pools()

        reap_futures(self)

        # Cheap capacity gate: when every executor is saturated, skip the
        # expensive job scan for this tick (maintenance above still runs).
        snapshot = load_capacity_snapshot(self.leases.path, self._executor_capacities())
        if not snapshot.has_any_capacity() and not self.settings.agent_definitions:
            return False

        # One job scan per pass. Jobs are evaluated exactly once into a ready
        # queue per workspace; rounds then pop one candidate per workspace,
        # preserving round-robin fairness without re-scanning jobs. The
        # capacity snapshot is refreshed on the next poll.
        claimed_any = False
        scan_started = time.monotonic()
        runnable_workspaces, jobs_by_workspace = self._runnable_workspaces()
        workspaces, queues = build_ready_queues(self, runnable_workspaces, jobs_by_workspace)
        scan_seconds = time.monotonic() - scan_started
        claims = 0
        while queues:
            round_claimed = False
            for workspace_id in self._round_robin.order(list(queues)):
                queue = queues.get(workspace_id)
                if queue is None or self._is_paused(workspace_id):
                    continue
                if claim_next_candidate(self, workspaces[workspace_id], queue, snapshot):
                    round_claimed = True
                    claimed_any = True
                    claims += 1
                    self._round_robin.complete_pass(workspace_id)
                if not queue:
                    del queues[workspace_id]
            if not round_claimed:
                break
        eval_stats = getattr(self, "_last_ready_stats", {})
        pass_stats = (
            "scan=%.2fs jobs=%d ready_cache hit=%d miss=%d running_jobs=%d claims=%d",
            scan_seconds,
            sum(len(v) for v in jobs_by_workspace.values()),
            eval_stats.get("hit", 0),
            eval_stats.get("miss", 0),
            eval_stats.get("running", 0),
            claims,
        )
        logger.info("workflow worker pass: " + pass_stats[0], *pass_stats[1:])
        if scan_seconds > 15:
            logger.warning("slow workflow worker pass: " + pass_stats[0], *pass_stats[1:])
        return claimed_any

    def _is_paused(self, workspace_id: str) -> bool:
        control = self.workspace_worker_control
        return control is not None and control.is_paused(workspace_id)

    def _runnable_workspaces(
        self,
    ) -> tuple[list[str], dict[str, list[tuple[WorkflowDefinition, dict[str, Any]]]]]:
        workspace_ids: list[str] = []
        jobs_by_workspace: dict[str, list[tuple[WorkflowDefinition, dict[str, Any]]]] = {}
        for definition in self._definitions:
            for job in self.job_db.list_active_job_marks(definition.key):
                if not (workspace_id := job.get("workspace_id")):
                    continue
                workspace_id = str(workspace_id)
                if self._is_paused(workspace_id):
                    continue
                if workspace_id not in jobs_by_workspace:
                    workspace_ids.append(workspace_id)
                    jobs_by_workspace[workspace_id] = []
                jobs_by_workspace[workspace_id].append((definition, job))
        return workspace_ids, jobs_by_workspace

    def stop(self, timeout: float = 3) -> None:
        self.stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        # Request cancellation for any still-active executions so adapters can
        # terminate their children and finish leases within a bounded grace
        # period instead of blocking on the full executor timeout.
        grace = getattr(self.runtime, "cancellation_grace_seconds", 5)
        for execution_id in list(self._futures):
            try:
                self.runtime.cancel(execution_id)
            except Exception:
                logger.exception("failed to cancel execution %s during shutdown", execution_id)
        futures = list(self._futures.values())
        done, pending = wait(futures, timeout=max(timeout, grace))
        for future in done:
            try:
                future.result()
            except Exception:
                logger.exception("workflow future failed during shutdown")
        if pending:
            logger.warning(
                "%s workflow future(s) still active after shutdown timeout", len(pending)
            )
        self._futures.clear()
        for pool in self._pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
        self._pools.clear()
