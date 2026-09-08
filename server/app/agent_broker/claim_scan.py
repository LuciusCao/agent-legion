"""Candidate window scan for the Agent claim transaction.

Split out of ``claim.py`` for the file-size budget: the bounded candidate
query, the fair cross-workspace ordering and the skip-reason accounting
live here; the per-candidate evaluation lives in ``claim_evaluate.py``, the
per-kind scan-round loop in ``claim_windows.py``, and ``claim.py`` keeps
the Worker-level setup. Functions take the broker instance as their first
argument and must run inside the caller's transaction.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

# Bounded claim scan rounds: (per-workspace head limit, global window). The
# first round matches the historical fixed window, so healthy queues see no
# behaviour change; deeper rounds only engage when the window came back
# saturated yet nothing in it was claimable, so a queue head poisoned by
# unclaimable requests can no longer deadlock a workspace (head-of-line
# blocking, issue #13).
SCAN_ROUNDS: tuple[tuple[int, int], ...] = ((8, 256), (64, 2048), (512, 16384))
MAX_CLAIM_ATTEMPTS = 32
# awaiting_approval is non-terminal: an approval gate blocks only its own
# downstream (EXEC-APPROVAL-001), so parallel branches must stay claimable.
RUNNABLE_JOB_STATUSES = ("queued", "running", "awaiting_approval")


class ClaimRacedError(Exception):
    """Mid-claim job exit: rolls back the single claim; #546 batch contains it in a savepoint."""


@dataclass(frozen=True)
class AgentClaim:
    execution_id: str
    workspace_id: str
    job_id: str
    node_key: str
    agent_id: str
    lease_id: str
    node_run_id: int
    manifest: dict[str, Any]
    # 'agent' (default) or 'code' (batch 2 self-contained code payload).
    kind: str = "agent"
    # Resolved runtime ('code' for code claims): the scan row carries it;
    # #490's claim.granted reads it here instead of re-parsing the manifest.
    runtime: str = ""


@dataclass(frozen=True)
class WorkerView:
    """Server-side Worker declarations relevant to candidate matching."""

    runtimes: set[str]
    models: set[tuple[str, str, str]]
    labels: dict[str, Any]
    allowed_workspaces: set[str]
    # Dual capacity pools (batch 2): agent and code claims are accounted
    # separately so a long code execution never starves agent claims.
    agent_capacity: int = 1
    agent_active: int = 0
    code_capacity: int = 0
    code_active: int = 0
    # Registered protocol version: code claims additionally require v2 (a v1
    # Worker never receives the cancel heartbeat body, so it must not hold
    # kind='code' executions even if a stale row grants it code capacity).
    protocol_version: int = 1


@dataclass
class ScanState:
    """Mutable per-claim-pass accounting shared across all scan rounds."""

    attempts: int = 0
    skip_reasons: Counter[str] = field(default_factory=Counter)
    pause_cache: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class ClaimOutcome:
    """Post-transaction event payload for one claim pass (#498).

    ``claim_in_transaction`` runs inside the write transaction; committing
    its verdicts to the event stream from in there would emit events for
    claims whose transaction then failed (deadlock retry #437, serialization
    conflict, connection loss) — ghost ``claim.granted`` lines that never
    happened. The broker therefore emits AFTER the commit, same spot as
    ``record_job_update`` (broker.py). The payload is a frozen snapshot
    taken at the transaction's decision point so the retry path can't reuse
    a stale mutable view: ``claim`` is None only for the empty/rejected
    verdicts (the claimed field carries no AgentClaim on a raced discard —
    ``ClaimRacedError`` rolls the whole attempt back and gets NO event), and
    ``scan_skipped`` preserves the capacity-synthesis branch (#494 P2-2).
    """

    claim: AgentClaim | None
    view: WorkerView
    skip_reasons: dict[str, int]
    scan_skipped: bool = False

    def event_kwargs(self) -> dict[str, Any]:
        """Keyword shape for ``note_claim_outcome`` (inversion of control)."""
        return {
            "skip_reasons": self.skip_reasons,
            "scan_skipped": self.scan_skipped,
        }


def fetch_candidates(conn: Any, per_workspace: int, window: int, kind: str) -> list[Any]:
    # Candidates are read WITHOUT row locks (a bounded per-workspace window
    # keeps small workspaces visible behind a deep queue); only the single
    # row actually being claimed is locked, by PK, in claim_evaluate.
    # The scan is per kind (issue #125): each kind walks its own window off
    # the (workspace_id, kind, queued_at) queued-head index (schema v51), so
    # a queued code flood can no longer crowd agent candidates out of the
    # shared FIFO window. Workspace capacity is agent-only (no
    # workspace_agent_capacities row = unlimited); kind='code' requests have
    # no workspace-level cap in this phase.
    # Eligibility is an EXISTS probe per workspaces row — a `distinct
    # workspace_id` scan would walk the entire queued index on every claim.
    # kind='code' rows skip the versioned_entities hard join (batch 2): their
    # payload is self-contained, runtime is the literal 'code', and the
    # capability comes from the frozen manifest.
    rows: list[Any] = conn.execute(
        """
        with eligible_workspaces as (
          select ws.id as workspace_id
          from workspaces ws
          left join workspace_agent_capacities w on w.workspace_id=ws.id
          where exists (select 1 from agent_execution_requests q
                        where q.workspace_id=ws.id and q.state='queued' and q.kind=%s)
            and (%s = 'code'
                 or (select count(*) from agent_execution_requests active
                     where active.workspace_id=ws.id and active.state='claimed'
                       and active.kind='agent'
                    ) < coalesce(w.max_concurrency, 2147483647))
        )
        select r.*, wr.definition_json as revision_definition_json
        from eligible_workspaces ws
        cross join lateral (
          select r2.*,
                 case when r2.kind='code' then 'code'
                      else d.definition_json::jsonb->>'runtime' end as runtime,
                 case when r2.kind='code' then r2.manifest_json::jsonb->>'capability'
                      else d.definition_json::jsonb->>'capability' end as capability,
                 coalesce(d.definition_json, '{}') as definition_json
          from agent_execution_requests r2
          left join versioned_entities d
            on r2.kind='agent' and d.entity_type='agent' and d.workspace_id=r2.workspace_id
           and d.entity_key=r2.agent_id and d.definition_hash=r2.agent_definition_hash
           -- Quality replay pins match their immutable version row (any
           -- status); unpinned requests match the currently published row.
           and ((r2.pinned_agent_version is not null
                 and d.version=r2.pinned_agent_version)
                or (r2.pinned_agent_version is null and d.status='published'))
          where r2.workspace_id=ws.workspace_id and r2.state='queued' and r2.kind=%s
            and (r2.kind='code' or d.definition_json is not null)
          order by r2.queued_at, r2.execution_id limit %s
        ) r
        join jobs j on j.id=r.job_id
        left join workflow_revisions wr on wr.id=j.workflow_revision_id
        order by r.queued_at, r.execution_id limit %s
        """,
        (kind, kind, kind, per_workspace, window),
    ).fetchall()
    return rows


def window_saturated(candidates: list[Any], per_workspace: int, window: int) -> bool:
    """True when a deeper window could still surface fresh candidates.

    Saturated means the global window filled up or some workspace returned a
    full per-workspace page — either way unclaimable entries may be hiding
    claimable ones behind them, so the next scan round is worth running."""
    if len(candidates) >= window:
        return True
    counts = Counter(str(row["workspace_id"]) for row in candidates)
    return any(count >= per_workspace for count in counts.values())


def fair_candidate_order(rows: list[dict[str, Any]], cursor: int) -> Iterator[dict[str, Any]]:
    """Interleave candidates across workspaces, starting rotation at ``cursor``.

    Per-workspace order stays queued_at-FIFO; only the cross-workspace order
    rotates so a deep queue in one workspace cannot starve the others."""
    by_workspace: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_workspace.setdefault(str(row["workspace_id"]), []).append(row)
    keys = list(by_workspace)
    if not keys:
        return
    start = cursor % len(keys)
    rotated = keys[start:] + keys[:start]
    depth = 0
    while True:
        yielded = False
        for key in rotated:
            group = by_workspace[key]
            if depth < len(group):
                yield group[depth]
                yielded = True
        if not yielded:
            return
        depth += 1


def labels_satisfy(actual: Mapping[str, Any], required: Mapping[str, Any]) -> bool:
    return all(str(actual.get(key)) == str(value) for key, value in required.items())
