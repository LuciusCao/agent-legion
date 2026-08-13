"""Candidate window scan for the Agent claim transaction.

Split out of ``claim.py`` for the file-size budget: the bounded candidate
query, the fair cross-workspace ordering and the skip-reason accounting
live here; the per-candidate evaluation lives in ``claim_evaluate.py`` and
``claim.py`` keeps the Worker-level setup and the scan-round loop.
Functions take the broker instance as their first argument and must run
inside the caller's transaction.
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
RUNNABLE_JOB_STATUSES = ("queued", "running")


class ClaimRacedError(Exception):
    """The job left the runnable set mid-claim; roll the whole claim back."""


@dataclass(frozen=True)
class AgentClaim:
    execution_id: str
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    agent_id: str
    lease_id: str
    node_run_id: int
    manifest: dict[str, Any]
    # 'agent' (default) or 'code' (batch 2 self-contained code payload).
    kind: str = "agent"


@dataclass(frozen=True)
class WorkerView:
    """Server-side Worker declarations relevant to candidate matching."""

    runtimes: set[str]
    capabilities: set[str]
    models: set[tuple[str, str]]
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


def fetch_candidates(conn: Any, per_workspace: int, window: int) -> list[Any]:
    # Candidates are read WITHOUT row locks (a bounded per-workspace window
    # keeps small workspaces visible behind a deep queue); only the single
    # row actually being claimed is locked, by PK, in claim_evaluate.
    # Workspace capacity is agent-only (no workspace_agent_capacities row =
    # unlimited); kind='code' requests have no workspace-level cap in this
    # phase, so queued code alone keeps a workspace eligible.
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
                        where q.workspace_id=ws.id and q.state='queued'
                          and q.kind='code')
             or (
               exists (select 1 from agent_execution_requests q
                       where q.workspace_id=ws.id and q.state='queued')
               and (select count(*) from agent_execution_requests active
                    where active.workspace_id=ws.id and active.state='claimed'
                      and active.kind='agent'
                   ) < coalesce(w.max_concurrency, 2147483647)
             )
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
            on r2.kind='agent' and d.entity_type='agent' and d.workspace_id is null
           and d.entity_key=r2.agent_id and d.definition_hash=r2.agent_definition_hash
           -- Quality replay pins match their immutable version row (any
           -- status); unpinned requests match the currently published row.
           and ((r2.pinned_agent_version is not null
                 and d.version=r2.pinned_agent_version)
                or (r2.pinned_agent_version is null and d.status='published'))
          where r2.workspace_id=ws.workspace_id and r2.state='queued'
            and (r2.kind='code' or d.definition_json is not null)
          order by r2.queued_at, r2.execution_id limit %s
        ) r
        join jobs j on j.id=r.job_id
        left join workflow_revisions wr on wr.id=j.workflow_revision_id
        order by r.queued_at, r.execution_id limit %s
        """,
        (per_workspace, window),
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
