from __future__ import annotations

import logging
import threading
import time
from typing import Any

from server.app.agent_broker import AgentDispatchService
from server.app.agent_broker.code_dispatch import CodeDispatchService, has_online_code_workers
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.scheduling.capacity import load_capacity_snapshot
from server.app.jobs import JobQueries
from server.app.services.agent_service import has_published_agent_definitions
from server.app.services.runtime_profile import profile
from server.app.settings import Settings
from server.app.workflow_worker.agent_gate import prepare_agent_pass
from server.app.workflow_worker.catalog_scan import (
    collect_runnable_workspace_jobs,
    load_workflow_scan_entries,
)
from server.app.workflow_worker.claim_flush import flush_prepared_claims
from server.app.workflow_worker.code_stock import CodeStockGate
from server.app.workflow_worker.execution import reap_futures
from server.app.workflow_worker.maintenance import WorkflowMaintenance
from server.app.workflow_worker.pass_log import log_pass_end, log_pass_start, pass_logger
from server.app.workflow_worker.pools import ensure_pools
from server.app.workflow_worker.ready import build_ready_queues
from server.app.workflow_worker.schedule import claim_ready_queues
from server.app.workflow_worker.shutdown import stop_worker
from server.app.workflow_worker.state import WorkflowWorkerState
from server.app.workflows.definition import WorkflowDefinition

logger = logging.getLogger(__name__)


class WorkflowWorkerThread:
    def __init__(
        self,
        job_db: JobQueries,
        leases: ExecutorLeaseRepository,
        settings: Settings,
        workspace_worker_control: Any | None = None,
        agent_manager: Any | None = None,
        agent_dispatch: AgentDispatchService | None = None,
        code_dispatch: CodeDispatchService | None = None,
        runtime: ExecutionRuntime | None = None,
    ):
        self.job_db = job_db
        self.leases = leases
        # None in pure-remote mode (#389): the local executor stack is not
        # assembled when code_capacity == 0, and the poll loop structurally
        # never submits local claims.
        self.runtime = runtime
        self.settings = settings
        self.workspace_worker_control = workspace_worker_control
        self.agent_manager = agent_manager
        self.agent_dispatch = agent_dispatch
        self.code_dispatch = code_dispatch
        self.stop_event = threading.Event()
        self.state = WorkflowWorkerState()
        self._thread: threading.Thread | None = None
        self._maintenance = WorkflowMaintenance(job_db, settings)
        # Code stockpile gate (issue #125): TTL-cached, shared across passes.
        self.code_stock = CodeStockGate(job_db, settings.executor_runtime.code_stock)

    # ``is_enabled`` retired (#385/#389): the workflows.enabled gray-release
    # switch is gone; the worker always runs and the deployment shape is
    # expressed by code_capacity (0 = pure-remote, no local executor stack).

    def local_executor(self) -> Any | None:
        """The local code executor, or None in pure-remote mode (#389)."""
        return getattr(self.runtime, "executor", None)

    def wake(self) -> None:
        """Wake the poll loop immediately; registered via scheduler_wakeup."""
        self.state.wake_event.set()

    def reload_scan_entries(self) -> None:
        """Rebuild the scan list from the workspaces table, then swap it in.

        Called outside the poll thread: at start, and after a workspace is
        created, re-keyed, or first-published. The list is fully built before
        the swap, so a failed reload leaves the previous snapshot untouched.
        """
        self.state.scan_entries = load_workflow_scan_entries(self.job_db)

    def _ensure_pools(self) -> None:
        ensure_pools(self)

    def start(self) -> None:
        self.reload_scan_entries()
        # Runtime profile (#359): register BOTH enqueue pools (agent bundling
        # on agent_dispatch, code bundling on code_dispatch) so the metrics
        # sampler's depth gauge sums the real backlogs. Best-effort — the
        # profile surface must never gate the worker's own startup.
        try:
            from server.app.services.runtime_profile import profile

            profile.enqueue_pools = [
                service.enqueue_pool
                for service in (self.agent_dispatch, self.code_dispatch)
                if service is not None and service.enqueue_pool is not None
            ]
        except Exception:
            # #204 broad-except audit: metrics registration only; a failure
            # here leaves the enqueue-depth gauge reading 0 while the worker
            # itself runs normally, so it must not abort scheduling startup.
            pass

        def _loop() -> None:
            while not self.stop_event.is_set():
                try:
                    processed = self._poll()
                except Exception:
                    # #204 broad-except audit: deliberate poll-loop safety
                    # net. Killing this thread would stop ALL workflow
                    # scheduling for the process until restart; the next pass
                    # (0.2s/3s later) is the built-in retry. logger.exception
                    # keeps the full traceback; processed=False merely widens
                    # the backoff. _poll itself narrows expected per-job
                    # failures into per-node outcomes (fail_node_config) and
                    # per-evaluation skips (ready_cache), so anything landing
                    # here is a pass-level programming error or
                    # infrastructure outage — visible in logs, never fatal.
                    logger.exception("workflow worker poll failed")
                    processed = False
                self.state.wake_event.wait(0.2 if processed else 3)
                self.state.wake_event.clear()

        self._thread = threading.Thread(target=_loop, name="workflow-worker", daemon=True)
        self._thread.start()

    def _poll(self) -> bool:
        self.state.reset_pass()
        self._maintenance.maybe_cleanup()
        if not self.state.scan_entries:
            return False

        self._ensure_pools()
        reap_futures(self)

        snapshot = load_capacity_snapshot(
            self.leases.path, self.settings.executor_runtime.code_capacity
        )
        # Pure-remote deployments (#389): with code_capacity=0 the local
        # snapshot has no capacity, but remote code Workers can still claim —
        # the pass must keep scanning (coupling fix ③). Only a host with no
        # local capacity, no published Agents and no online code Worker has
        # nothing at all to dispatch.
        if not (
            snapshot.has_any_capacity() or has_online_code_workers(self.job_db)
        ) and not has_published_agent_definitions(self.job_db):
            return False

        scan_started = time.monotonic()
        runnable_workspaces, jobs_by_workspace = self._runnable_workspaces()
        self.state.scan_phases["marks"] = time.monotonic() - scan_started
        workspaces, queues = build_ready_queues(self, runnable_workspaces, jobs_by_workspace)
        scan_seconds = time.monotonic() - scan_started
        total_candidates = sum(len(queue) for queue in queues.values())
        log_pass_start(pass_logger(self.settings), jobs_by_workspace, queues, scan_seconds)
        prepare_agent_pass(self, queues)
        claim_started = time.monotonic()
        claims = claim_ready_queues(self, workspaces, queues, snapshot)
        flush_prepared_claims(self)
        claim_seconds = time.monotonic() - claim_started
        jobs_count = sum(len(v) for v in jobs_by_workspace.values())
        eval_stats = self.state.last_ready_stats
        pass_stats = (
            "scan=%.2fs jobs=%d ready_cache hit=%d miss=%d running_jobs=%d claims=%d",
            scan_seconds,
            jobs_count,
            eval_stats.get("hit", 0),
            eval_stats.get("miss", 0),
            eval_stats.get("running", 0),
            claims,
        )
        logger.info("workflow worker pass: " + pass_stats[0], *pass_stats[1:])
        log_pass_end(
            pass_logger(self.settings),
            scan_seconds=scan_seconds,
            jobs=jobs_count,
            ready_stats=eval_stats,
            claims=claims,
            candidates=total_candidates,
            claim_seconds=claim_seconds,
            claim_counts=self.state.pass_claim_counts,
            stock_gated=self.state.agent_pass.stock_gated,
            scan_phases=self.state.scan_phases,
        )
        if scan_seconds > 15:
            logger.warning("slow workflow worker pass: " + pass_stats[0], *pass_stats[1:])
        profile.note_pass(
            seconds=scan_seconds + claim_seconds, scan_seconds=scan_seconds, slow=scan_seconds > 15
        )
        if worker_stats := getattr(self.state.agent_pass, "stock_gated", 0):
            profile.note_enqueue_stock_gated(worker_stats)
        return claims > 0

    def _is_paused(self, workspace_id: str) -> bool:
        control = self.workspace_worker_control
        return control is not None and control.is_paused(workspace_id)

    def _runnable_workspaces(
        self,
    ) -> tuple[list[str], dict[str, list[tuple[WorkflowDefinition | None, dict[str, Any]]]]]:
        return collect_runnable_workspace_jobs(self)

    def stop(self, timeout: float = 3) -> None:
        # Cancel in-flight executions, drain futures, shut pools (see
        # shutdown.py; split for the size budget).
        stop_worker(self, timeout=timeout)
