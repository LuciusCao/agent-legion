"""Claim-time capacity gates evaluated inside the write transaction (#1158).

Split out of ``claim_evaluate.py`` (file budget): the two gates that decide
ADMISSION BY CAPACITY after the lock ladder — the workspace-level agent
capacity check (moved here verbatim, batch 2 decision 2) and the Worker
per-node concurrency limit (#1158, the machine-protection layer on top of
the workspace-global ``workspace_node_limits``, #1149).

Worker per-node gate contract:

- Keying is the BARE node_key (no workspace): the machine does not care
  which workspace's same-named node is burning its CPU. Cross-workspace key
  collisions converge conservatively (a light node inherits a heavy node's
  declared limit — a safe-direction false positive; rename the node key to
  escape it).
- Counting is kind-agnostic: ``agent_execution_requests`` rows in
  ``state='claimed'`` for (worker_id, node_key), agent and code alike —
  machine protection covers anything burning this machine's CPU.
  ``agent_execution_requests`` is the counting source (not
  ``executor_leases``): leases carry no worker identity, while the request
  row's worker_id is stamped at promote time.
- Serialization: every claim for one worker serializes on the
  ``agent_workers`` row lock (``claim_setup.prepare_claim_view`` SELECT …
  FOR UPDATE, held to COMMIT in both the single and the batch write phase),
  so the count-then-claim inside one transaction cannot race a concurrent
  same-worker claim; within a batch, earlier promotes flip their rows to
  'claimed' in the SAME transaction and are visible to later candidates'
  counts. No additional advisory lock is needed.
- Over-limit is a skip (``worker_node_limit_full``), never a cancel: the
  request stays queued for the next pass with the same semantics as
  ``capacity_full`` / ``node_limit_full``, and the unclaimable sweeper
  (runtime/model probes only) never reaps it.
- The limit map is read from the claim's WorkerView, which the write phase
  builds AFTER ``sync_declared_capacity`` applied this poll's declaration
  under the row lock — so a console edit takes effect on the next claim
  without re-registration. An empty map (undeclared or explicitly cleared)
  skips the gate entirely: behavior is byte-identical to the pre-#1158
  claim path (zero regression surface).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def workspace_agent_admits(conn: Any, selected: Mapping[str, Any], kind: str, state: Any) -> bool:
    """Workspace-level agent capacity gate (batch 2 decision 2; agent-only).

    The ws advisory lock and this cap check are both agent-branch-only —
    code claims have no workspace-level agent pool. A lost race for the
    workspace's last slot skips (``capacity_raced``) and the request stays
    queued for the next pass.
    """
    if kind == "code":
        return True
    capacity = conn.execute(
        "select max_concurrency from workspace_agent_capacities where workspace_id=%s",
        (selected["workspace_id"],),
    ).fetchone()
    if capacity is None:
        return True
    ws_active = conn.execute(
        "select count(*) as cnt from agent_execution_requests"
        " where workspace_id=%s and state='claimed' and kind='agent'",
        (selected["workspace_id"],),
    ).fetchone() or {"cnt": 0}
    if int(ws_active["cnt"]) >= int(capacity["max_concurrency"]):
        # Lost the race for this workspace's last slot; try the next.
        state.skip_reasons["capacity_raced"] += 1
        return False
    return True


def worker_node_admits(
    conn: Any,
    worker_id: str,
    selected: Mapping[str, Any],
    view: Any,
    state: Any,
) -> bool:
    """Worker per-node concurrency gate (#1158); the view's enforced map.

    Both execution kinds count (machine protection). Over-limit skips with
    ``worker_node_limit_full`` and keeps the request queued. For code
    executions the effective ceiling for one node on one machine stacks with
    the workspace-global node limit (#1149) as two independent gates,
    yielding min(worker declared, workspace remaining); that workspace gate
    is code-only (``code_claim_admits`` returns early for other kinds), so
    for agent executions the declared value IS the effective ceiling.
    """
    limit = view.node_limits.get(str(selected["node_key"]))
    if limit is None:
        return True
    active = conn.execute(
        "select count(*) as cnt from agent_execution_requests"
        " where worker_id=%s and node_key=%s and state='claimed'",
        (worker_id, selected["node_key"]),
    ).fetchone()
    if int(active["cnt"] if active else 0) >= limit:
        state.skip_reasons["worker_node_limit_full"] += 1
        return False
    return True
