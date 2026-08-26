"""Mutable state container for the workflow worker.

The WorkflowWorkerThread used to keep ~18 private attributes that sibling
modules (schedule, ready, execution, scan, claim_flush, ...) reached into
directly — a god-object split across files with every field effectively
public to the package. This dataclass makes that contract explicit: the
thread owns one ``state`` instance, siblings receive ``worker.state.X``
instead of ``worker._X``, and the private-attribute escapes disappear.

Field groups:
- cross-pass: caches and marks that persist across poll passes
  (definition/job-eval/route/node-code caches, mark store, round robin);
- per-pass: buffers reset at the top of every ``_poll``
  (claim buffers, secret memos, scan phase timings, agent pass);
- execution: pools, in-flight futures and their claim indices.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from server.app.executors.models import ExecutionResult
from server.app.executors.scheduling.fair import WorkspaceRoundRobin
from server.app.workflow_worker.agent_gate import AgentPassState
from server.app.workflow_worker.catalog_scan import ScanEntry
from server.app.workflow_worker.claim_flush import PreparedClaim
from server.app.workflow_worker.mark_scan import MarkStore
from server.app.workflow_worker.routing import NodeRoute
from server.app.workflows.definition import WorkflowDefinition


class WorkflowWorkerState:
    """All mutable state of one workflow worker thread in one object."""

    __slots__ = (
        "agent_pass",
        "batch_payload_cache",
        "definition_cache",
        "future_claims",
        "futures",
        "job_evals",
        "last_ready_stats",
        "mark_store",
        "node_code_cache",
        "pass_claim_counts",
        "pending_claims",
        "pools",
        "route_cache",
        "round_robin",
        "scan_entries",
        "scan_phases",
        "secret_memo",
        "wake_event",
    )

    def __init__(self) -> None:
        # Set when work finishes or arrives; the poll loop waits on this.
        self.wake_event = threading.Event()
        # Scan-list snapshot (one entry per scannable workspace), swapped
        # atomically by reload_scan_entries; never mutated in place. Readers
        # take the list reference first, so a mid-swap pass never sees a
        # half-applied state.
        self.scan_entries: list[ScanEntry] = []
        self.pools: dict[str, ThreadPoolExecutor] = {}
        self.futures: dict[str, Future[ExecutionResult | None]] = {}
        # execution_id -> (executor_id, lease_id) for in-flight claims.
        self.future_claims: dict[str, tuple[str, str]] = {}
        self.round_robin = WorkspaceRoundRobin()
        # Cross-pass caches: parsed workflow definitions by definition hash,
        # and ready-node evaluations by job id (scan-mark keyed). Job marks
        # themselves live in the MarkStore (watermark delta refresh).
        self.definition_cache: dict[str, WorkflowDefinition | None] = {}
        self.job_evals: dict[str, tuple[tuple[Any, ...], list[Any]]] = {}
        self.mark_store = MarkStore()
        self.last_ready_stats: dict[str, int] = {"hit": 0, "miss": 0}
        # Per-pass scan-phase wall times (seconds), reset in _poll and rendered
        # into the pass log: marks (mark store refresh + pause probes),
        # ws_query (per-workspace row fetch), miss_fetch (batched fat-row/node
        # reads for changed jobs), eval (per-changed-job ready evaluation).
        self.scan_phases: dict[str, float] = {}
        # Short-TTL route cache; see server.app.workflow_worker.routing.
        self.route_cache: dict[tuple[str, str, str], tuple[float, NodeRoute]] = {}
        # Per-pass state (cleared in _poll).
        self.batch_payload_cache: dict[str, dict[str, Any] | None] = {}
        self.pass_claim_counts: dict[str, int] = {}
        self.pending_claims: list[PreparedClaim] = []
        # Per-pass claim-input memos (issue #124): every claimed node used to
        # re-read its published code text and re-resolve each vault secret_ref
        # against the DB, so a multi-slot pass multiplied those round trips on
        # an already-congested DB. Memoizing per (pass, key) collapses the
        # repeats. Code entries are tagged with the publish generation: an
        # in-process publish/rollback/archive lands on the very next claim
        # (the #115 "next node execution" contract); a secret write takes
        # effect on the next pass.
        self.node_code_cache: dict[tuple[str, str, str], tuple[int, str | None]] = {}
        self.secret_memo: dict[tuple[str, str], str | None] = {}
        self.agent_pass = AgentPassState()

    def reset_pass(self) -> None:
        """Clear per-pass buffers; cross-pass caches survive."""
        self.batch_payload_cache = {}
        self.pass_claim_counts = {}
        self.pending_claims = []
        self.node_code_cache = {}
        self.secret_memo = {}
        self.scan_phases = {"marks": 0.0, "ws_query": 0.0, "miss_fetch": 0.0, "eval": 0.0}
        self.agent_pass.reset_pass()
