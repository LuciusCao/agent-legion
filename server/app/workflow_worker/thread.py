from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

from server.app.agent_broker import AgentDispatchService
from server.app.agent_broker.code_dispatch import CodeDispatchService
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.scheduling.capacity import load_capacity_snapshot
from server.app.executors.scheduling.fair import WorkspaceRoundRobin
from server.app.jobs import JobQueries
from server.app.services.agent_service import has_published_agent_definitions
from server.app.settings import Settings
from server.app.workflow_worker.agent_gate import AgentPassState, prepare_agent_pass
from server.app.workflow_worker.catalog_scan import (
    ScanEntry,
    collect_runnable_workspace_jobs,
    load_workflow_scan_entries,
)
from server.app.workflow_worker.claim_flush import PreparedClaim, flush_prepared_claims
from server.app.workflow_worker.code_stock import CodeStockGate
from server.app.workflow_worker.execution import reap_futures
from server.app.workflow_worker.maintenance import WorkflowMaintenance
from server.app.workflow_worker.mark_scan import MarkStore
from server.app.workflow_worker.pass_log import log_pass_end, log_pass_start, pass_logger
from server.app.workflow_worker.pools import ensure_pools
from server.app.workflow_worker.ready import build_ready_queues
from server.app.workflow_worker.routing import NodeRoute
from server.app.workflow_worker.schedule import claim_ready_queues
from server.app.workflows.definition import WorkflowDefinition

logger = logging.getLogger(__name__)


class WorkflowWorkerThread:
    def __init__(
        self,
        job_db: JobQueries,
        leases: ExecutorLeaseRepository,
        runtime: ExecutionRuntime,
        settings: Settings,
        workspace_worker_control: Any | None = None,
        agent_manager: Any | None = None,
        agent_dispatch: AgentDispatchService | None = None,
        code_dispatch: CodeDispatchService | None = None,
    ):
        self.job_db = job_db
        self.leases = leases
        self.runtime = runtime
        self.settings = settings
        self.workspace_worker_control = workspace_worker_control
        self.agent_manager = agent_manager
        self.agent_dispatch = agent_dispatch
        self.code_dispatch = code_dispatch
        self.stop_event = threading.Event()
        # Set when work finishes or arrives; the poll loop waits on this.
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Scan-list snapshot (one entry per scannable workspace), swapped
        # atomically by reload_scan_entries; never mutated in place. Readers
        # take the list reference first, so a mid-swap pass never sees a
        # half-applied state.
        self._scan_entries: list[ScanEntry] = []
        self._pools: dict[str, ThreadPoolExecutor] = {}
        self._futures: dict[str, Future[ExecutionResult | None]] = {}
        # execution_id -> (executor_id, lease_id) for in-flight claims.
        self._future_claims: dict[str, tuple[str, str]] = {}
        self._round_robin = WorkspaceRoundRobin()
        self._maintenance = WorkflowMaintenance(job_db, settings)
        # Cross-pass caches: parsed workflow definitions by definition hash,
        # and ready-node evaluations by job id (scan-mark keyed). Job marks
        # themselves live in the MarkStore (watermark delta refresh).
        self._definition_cache: dict[str, WorkflowDefinition | None] = {}
        self._job_evals: dict[str, tuple[tuple[Any, ...], list[Any]]] = {}
        self._mark_store = MarkStore()
        self._last_ready_stats: dict[str, int] = {"hit": 0, "miss": 0}
        # Per-pass scan-phase wall times (seconds), reset in _poll and rendered
        # into the pass log: marks (mark store refresh + pause probes),
        # ws_query (per-workspace row fetch), miss_fetch (batched fat-row/node
        # reads for changed jobs), eval (per-changed-job ready evaluation).
        self._scan_phases: dict[str, float] = {}
        # Short-TTL route cache; see server.app.workflow_worker.routing.
        self._route_cache: dict[tuple[str, str, str], tuple[float, NodeRoute]] = {}
        # Per-pass state (cleared in _poll).
        self._batch_payload_cache: dict[str, dict[str, Any] | None] = {}
        self._pass_claim_counts: dict[str, int] = {}
        self._pending_claims: list[PreparedClaim] = []
        # Per-pass claim-input memos (issue #124): every claimed node used to
        # re-read its published code text and re-resolve each vault secret_ref
        # against the DB, so a multi-slot pass multiplied those round trips on
        # an already-congested DB. Memoizing per (pass, key) collapses the
        # repeats. Code entries are tagged with the publish generation: an
        # in-process publish/rollback/archive lands on the very next claim
        # (the #115 "next node execution" contract); a secret write takes
        # effect on the next pass.
        self._node_code_cache: dict[tuple[str, str, str], tuple[int, str | None]] = {}
        self._secret_memo: dict[tuple[str, str], str | None] = {}
        self._agent_pass = AgentPassState()
        # Code stockpile gate (issue #125): TTL-cached, shared across passes.
        self.code_stock = CodeStockGate(settings.database_url, settings.executor_runtime.code_stock)

    @staticmethod
    def is_enabled(settings: Settings) -> bool:
        return settings.executor_runtime.workflows.enabled

    def wake(self) -> None:
        """Wake the poll loop immediately; registered via scheduler_wakeup."""
        self._wake_event.set()

    def reload_scan_entries(self) -> None:
        """Rebuild the scan list from the workspaces table, then swap it in.

        Called outside the poll thread: at start, and after a workspace is
        created, re-keyed, or first-published. The list is fully built before
        the swap, so a failed reload leaves the previous snapshot untouched.
        """
        self._scan_entries = load_workflow_scan_entries(self.settings)

    def _ensure_pools(self) -> None:
        ensure_pools(self)

    def _pool_for(self, executor_id: str) -> ThreadPoolExecutor:
        return self._pools[executor_id]

    def start(self) -> None:
        self.reload_scan_entries()

        def _loop() -> None:
            while not self.stop_event.is_set():
                try:
                    processed = self._poll()
                except Exception:
                    logger.exception("workflow worker poll failed")
                    processed = False
                self._wake_event.wait(0.2 if processed else 3)
                self._wake_event.clear()

        self._thread = threading.Thread(target=_loop, name="workflow-worker", daemon=True)
        self._thread.start()

    def _poll(self) -> bool:
        self._batch_payload_cache, self._pass_claim_counts, self._pending_claims = {}, {}, []
        self._node_code_cache, self._secret_memo = {}, {}
        self._scan_phases = {"marks": 0.0, "ws_query": 0.0, "miss_fetch": 0.0, "eval": 0.0}
        self._agent_pass.reset_pass()
        self._maintenance.maybe_cleanup()
        if not self._scan_entries:
            return False

        self._ensure_pools()
        reap_futures(self)

        snapshot = load_capacity_snapshot(
            self.leases.path, self.settings.executor_runtime.code_capacity
        )
        if not snapshot.has_any_capacity() and not has_published_agent_definitions(
            self.settings.database_url
        ):
            return False

        scan_started = time.monotonic()
        runnable_workspaces, jobs_by_workspace = self._runnable_workspaces()
        self._scan_phases["marks"] = time.monotonic() - scan_started
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
        eval_stats = getattr(self, "_last_ready_stats", {})
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
            claim_counts=self._pass_claim_counts,
            stock_gated=self._agent_pass.stock_gated,
            scan_phases=self._scan_phases,
        )
        if scan_seconds > 15:
            logger.warning("slow workflow worker pass: " + pass_stats[0], *pass_stats[1:])
        return claims > 0

    def _is_paused(self, workspace_id: str) -> bool:
        control = self.workspace_worker_control
        return control is not None and control.is_paused(workspace_id)

    def _runnable_workspaces(
        self,
    ) -> tuple[list[str], dict[str, list[tuple[WorkflowDefinition | None, dict[str, Any]]]]]:
        return collect_runnable_workspace_jobs(self)

    def stop(self, timeout: float = 3) -> None:
        self.stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        # Cancel still-active executions so adapters terminate children and
        # finish leases within a bounded grace period.
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
        self._future_claims.clear()
        for pool in self._pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
        self._pools.clear()
