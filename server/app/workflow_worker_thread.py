from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import (
    ClaimedExecution,
    ExecutionContext,
    ExecutionResult,
)
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.scheduling.capacity import load_capacity_snapshot
from server.app.executors.scheduling.fair import WorkspaceRoundRobin
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.workflow_worker_agent_status import agent_status_scope
from server.app.workflow_worker_maintenance import WorkflowMaintenance
from server.app.workflow_worker_ready import build_ready_queues
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
    ):
        self.job_db = job_db
        self.leases = leases
        self.registry = registry
        self.executor_registry = registry  # compatibility alias for tests/lifespan
        self.runtime = runtime
        self.settings = settings
        self.workspace_worker_control = workspace_worker_control
        self.agent_manager = agent_manager
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._definitions: list[WorkflowDefinition] = []
        self._pools: dict[str, ThreadPoolExecutor] = {}
        # All submit-only (remote) claims share one bounded pool: submission is
        # a quick enqueue, so it must not scale with executor global capacity.
        self._submit_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="remote-submit")
        self._futures: dict[str, Future[ExecutionResult | None]] = {}
        self._round_robin = WorkspaceRoundRobin()
        self._maintenance = WorkflowMaintenance(job_db, settings)

    @staticmethod
    def is_enabled(settings: Settings) -> bool:
        return settings.executor_runtime.workflows.enabled

    def _ensure_pools(self) -> None:
        for executor_id, config in self.registry.definitions().items():
            if config.kind == "remote":
                continue  # remote claims run on the shared submit pool
            if executor_id not in self._pools:
                capacity = self.registry.global_capacity(executor_id) or 1
                self._pools[executor_id] = ThreadPoolExecutor(max_workers=capacity)

    def _pool_for(self, executor_id: str) -> ThreadPoolExecutor:
        executor = self.registry.get(executor_id)
        if executor is not None and getattr(executor, "submit_only", False):
            return self._submit_pool
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
                self.stop_event.wait(0.2 if processed else 3)

        self._thread = threading.Thread(target=_loop, name="workflow-worker", daemon=True)
        self._thread.start()

    def _poll(self) -> bool:
        self._maintenance.maybe_cleanup()
        if not self._definitions:
            return False

        if not self._pools:
            self._ensure_pools()

        self._reap_futures()

        # Cheap capacity gate: when every executor is saturated, skip the
        # expensive job scan for this tick (maintenance above still runs).
        snapshot = load_capacity_snapshot(self.leases.path, self._executor_capacities())
        if not snapshot.has_any_capacity():
            return False

        # One job scan per pass. Jobs are evaluated exactly once into a ready
        # queue per workspace; rounds then pop one candidate per workspace,
        # preserving round-robin fairness without re-scanning jobs. The
        # capacity snapshot is refreshed on the next poll.
        claimed_any = False
        runnable_workspaces, jobs_by_workspace = self._runnable_workspaces()
        workspaces, queues = build_ready_queues(self, runnable_workspaces, jobs_by_workspace)
        while snapshot.has_any_capacity() and queues:
            round_claimed = False
            for workspace_id in self._round_robin.order(list(queues)):
                queue = queues.get(workspace_id)
                if queue is None or self._is_paused(workspace_id):
                    continue
                if claim_next_candidate(self, workspaces[workspace_id], queue, snapshot):
                    round_claimed = True
                    claimed_any = True
                    self._round_robin.complete_pass(workspace_id)
                if not queue:
                    del queues[workspace_id]
            if not round_claimed:
                break
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
            for job in self.job_db.list_jobs(
                workspace_id=None,
                workflow_key=definition.key,
                status_not_in=("completed", "failed"),
            ):
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

    def _run_claim(
        self, claim: ClaimedExecution, context: ExecutionContext
    ) -> ExecutionResult | None:
        with agent_status_scope(self.agent_manager, self.registry, claim, context):
            try:
                return self.runtime.run(claim, context)
            except Exception as exc:
                logger.exception("workflow execution %s failed", claim.execution_id)
                result = ExecutionResult(
                    status="failed",
                    exit_code=1,
                    error_message=str(exc),
                    log_path=str(context.log_path),
                )
                self.leases.finish(claim.lease_id, result)
                return result

    def _reap_futures(self) -> None:
        for execution_id in list(self._futures):
            future = self._futures[execution_id]
            if future.done():
                try:
                    future.result()
                except Exception:
                    logger.exception("workflow future %s failed", execution_id)
                self._futures.pop(execution_id, None)

    def stop(self, timeout: float = 3) -> None:
        self.stop_event.set()
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
        self._submit_pool.shutdown(wait=False, cancel_futures=True)
