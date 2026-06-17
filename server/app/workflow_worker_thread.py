from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import (
    ClaimedExecution,
    ConfigurationFailureRequest,
    ExecutionContext,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.scheduling.fair import WorkspaceRoundRobin
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflow_worker_agent_status import agent_status_scope
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode
from server.app.workflows.execution_control import allowed_nodes
from server.app.workflows.registry import list_registered_workflows
from server.app.workflows.scheduler import _node_statuses, find_ready_nodes

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
        self._futures: dict[str, Future[ExecutionResult]] = {}
        self._round_robin = WorkspaceRoundRobin()

    @staticmethod
    def is_enabled(settings: Settings) -> bool:
        return settings.executor_runtime.workflows.enabled

    def _ensure_pools(self) -> None:
        for executor_id in self.registry.definitions():
            if executor_id not in self._pools:
                capacity = self.registry.global_capacity(executor_id) or 1
                self._pools[executor_id] = ThreadPoolExecutor(max_workers=capacity)

    def start(self) -> None:
        self._definitions = list_registered_workflows(self.settings.root_dir)
        self._ensure_pools()
        expired = self.leases.expire_stale(datetime.now(UTC))
        if expired:
            logger.warning("expired stale workflow executions on startup: %s", ", ".join(expired))
        recovered = self.leases.recover_orphaned_running_jobs(datetime.now(UTC))
        if recovered:
            logger.warning("recovered orphaned running jobs on startup: %s", ", ".join(recovered))

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
        if not self._definitions:
            return False

        if not self._pools:
            self._ensure_pools()

        self._reap_futures()
        expired = self.leases.expire_stale(datetime.now(UTC))
        if expired:
            logger.warning("expired stale workflow executions: %s", ", ".join(expired))
        recovered = self.leases.recover_orphaned_running_jobs(datetime.now(UTC))
        if recovered:
            logger.warning("recovered orphaned running jobs: %s", ", ".join(recovered))

        claimed_any = False
        while True:
            runnable_workspaces, jobs_by_workspace = self._runnable_workspaces()
            ordered_workspace_ids = self._round_robin.order(runnable_workspaces)
            round_claimed = False
            for workspace_id in ordered_workspace_ids:
                if (
                    self.workspace_worker_control is not None
                    and self.workspace_worker_control.is_paused(workspace_id)
                ):
                    continue
                claimed = self._schedule_workspace(workspace_id, jobs_by_workspace[workspace_id])
                if claimed:
                    round_claimed = True
                    claimed_any = True
                    self._round_robin.complete_pass(workspace_id)
            if not round_claimed:
                break
        return claimed_any

    def _runnable_workspaces(
        self,
    ) -> tuple[list[str], dict[str, list[tuple[WorkflowDefinition, dict[str, Any]]]]]:
        workspace_ids: list[str] = []
        jobs_by_workspace: dict[str, list[tuple[WorkflowDefinition, dict[str, Any]]]] = {}
        for definition in self._definitions:
            for job in self.job_db.list_jobs(workspace_id=None, workflow_key=definition.key):
                if job.get("status") in ("completed", "failed"):
                    continue
                workspace_id = str(job.get("workspace_id") or "default")
                if workspace_id not in jobs_by_workspace:
                    workspace_ids.append(workspace_id)
                    jobs_by_workspace[workspace_id] = []
                jobs_by_workspace[workspace_id].append((definition, job))
        return workspace_ids, jobs_by_workspace

    def _schedule_workspace(
        self,
        workspace_id: str,
        jobs: list[tuple[WorkflowDefinition, dict[str, Any]]],
    ) -> bool:
        workspace = self.job_db.get_workspace(workspace_id)
        if workspace is None:
            return False

        for definition, job in jobs:
            if job.get("status") in ("completed", "failed", "paused"):
                continue
            if job.get("execution_paused"):
                continue
            job_dir = resolve_job_dir(job, self.settings.jobs_dir)
            statuses = _node_statuses(self.job_db, job["id"])
            control_snapshot = {
                "execution_mode": job.get("execution_mode", "full"),
                "target_node_key": job.get("target_node_key"),
                "execution_paused": bool(job.get("execution_paused")),
                "pause_reason": job.get("pause_reason", ""),
            }
            try:
                allowed = allowed_nodes(definition, control_snapshot)
            except Exception:
                logger.exception("failed to compute allowed nodes for job %s", job["id"])
                continue
            ready_nodes = find_ready_nodes(definition, statuses, job_dir)
            for node in ready_nodes:
                if node.key not in allowed:
                    continue
                if self._try_claim_and_submit(
                    workspace, definition, job, node, job_dir, control_snapshot, allowed
                ):
                    return True
        return False

    def _try_claim_and_submit(
        self,
        workspace: dict[str, Any],
        definition: WorkflowDefinition,
        job: dict[str, Any],
        node: WorkflowNode,
        job_dir: Path,
        control_snapshot: dict[str, Any] | None = None,
        allowed_node_keys: frozenset[str] | None = None,
    ) -> bool:
        workspace_id = workspace["id"]
        workflow_key = definition.key
        node_key = node.key
        log_path = self._log_path(job_dir, f"{job['id']}-{node_key}")

        binding = self._get_binding(workspace_id, workflow_key, node_key)
        if binding is None:
            self.leases.fail_without_lease(
                ConfigurationFailureRequest(
                    workspace_id=workspace_id,
                    job_id=job["id"],
                    workflow_key=workflow_key,
                    node_key=node_key,
                    capability=node.capability,
                    log_path=str(log_path),
                ),
                "No Executor binding",
            )
            return True

        executor_id = binding["executor_id"]
        try:
            executor = self.registry.require(executor_id, node.capability)
        except Exception as exc:
            self.leases.fail_without_lease(
                ConfigurationFailureRequest(
                    workspace_id=workspace_id,
                    job_id=job["id"],
                    workflow_key=workflow_key,
                    node_key=node_key,
                    capability=node.capability,
                    log_path=str(log_path),
                ),
                str(exc),
            )
            return True

        local_node_limit: int | None = None
        if executor.kind == "local":
            local_node_limit = self._get_local_node_limit(workspace_id, workflow_key, node_key)
        elif self._has_local_node_limit(workspace_id, workflow_key, node_key):
            self.leases.fail_without_lease(
                ConfigurationFailureRequest(
                    workspace_id=workspace_id,
                    job_id=job["id"],
                    workflow_key=workflow_key,
                    node_key=node_key,
                    capability=node.capability,
                    log_path=str(log_path),
                ),
                "Node limits are not supported for agent executors",
            )
            return True

        global_capacity = self.registry.global_capacity(executor_id)
        if global_capacity is None:
            return False

        claim = self.leases.try_claim(
            LeaseClaimRequest(
                executor_id=executor_id,
                global_capacity=global_capacity,
                workspace_id=workspace_id,
                job_id=job["id"],
                workflow_key=workflow_key,
                node_key=node_key,
                capability=node.capability,
                local_node_limit=local_node_limit,
                lease_ttl_seconds=self.runtime.lease_ttl_seconds,
                log_path=str(log_path),
                execution_mode=control_snapshot.get("execution_mode", "full")
                if control_snapshot
                else "full",
                target_node_key=control_snapshot.get("target_node_key")
                if control_snapshot
                else None,
                allowed_node_keys=tuple(sorted(allowed_node_keys)) if allowed_node_keys else (),
            )
        )
        if claim is None:
            return False

        context = ExecutionContext(
            execution_id=claim.execution_id,
            lease_id=claim.lease_id,
            node_run_id=claim.node_run_id,
            executor_id=claim.executor_id,
            workspace_id=claim.workspace_id,
            job_id=claim.job_id,
            workflow_key=claim.workflow_key,
            node_key=claim.node_key,
            capability=claim.capability,
            workspace=dict(workspace),
            job=dict(job),
            job_dir=job_dir,
            log_path=log_path,
            inputs=tuple(node.inputs),
            expected_outputs=tuple(node.outputs),
        )

        pool = self._pools[executor_id]
        future = pool.submit(self._run_claim, claim, context)
        self._futures[claim.execution_id] = future
        return True

    def _run_claim(self, claim: ClaimedExecution, context: ExecutionContext) -> ExecutionResult:
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

    def _get_binding(
        self,
        workspace_id: str,
        workflow_key: str,
        node_key: str,
    ) -> dict[str, Any] | None:
        with self.job_db._connect_read() as conn:
            row = conn.execute(
                """
                select executor_id from workspace_node_bindings
                where workspace_id=? and workflow_key=? and node_key=?
                """,
                (workspace_id, workflow_key, node_key),
            ).fetchone()
        return dict(row) if row else None

    def _get_local_node_limit(
        self,
        workspace_id: str,
        workflow_key: str,
        node_key: str,
    ) -> int | None:
        with self.job_db._connect_read() as conn:
            row = conn.execute(
                """
                select concurrency_limit from workspace_node_limits
                where workspace_id=? and workflow_key=? and node_key=?
                """,
                (workspace_id, workflow_key, node_key),
            ).fetchone()
        return int(row["concurrency_limit"]) if row else None

    def _has_local_node_limit(
        self,
        workspace_id: str,
        workflow_key: str,
        node_key: str,
    ) -> bool:
        with self.job_db._connect_read() as conn:
            row = conn.execute(
                """
                select 1 from workspace_node_limits
                where workspace_id=? and workflow_key=? and node_key=?
                """,
                (workspace_id, workflow_key, node_key),
            ).fetchone()
        return row is not None

    def _log_path(self, job_dir: Path, name: str) -> Path:
        log_path = self.settings.logs_dir / "jobs" / f"{name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path

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
        deadline = time.monotonic() + min(timeout, grace)
        for future in list(self._futures.values()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                future.result(timeout=remaining)
            except Exception:
                logger.exception("workflow future failed during shutdown")
        self._futures.clear()
        for pool in self._pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
        self._pools.clear()
