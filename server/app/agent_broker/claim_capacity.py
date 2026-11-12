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

Read-phase twin (#1158 R1): ``load_node_claimed_counts`` + ``node_slot_open``
give the batch selection (``claim_batch_select``) an advisory pre-filter on
the same predicate, so a queue headed by an already-full node does not get
selected, rejected by the write phase, and re-selected every poll while
runnable nodes behind it starve. Advisory only — the write-phase gate stays
the authoritative check.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def load_node_claimed_counts(conn: Any, worker_id: str) -> dict[str, int]:
    """读相预过滤的计数快照（#1158 R1）：本机各 node_key 的 claimed 行数。

    一次 group by 查询服务整个批选择循环；与写相 ``worker_node_admits``
    同一张表同一谓词（不分 kind、不带 workspace）。只在 Worker 声明了
    节点上限时调用（空 map 不发这条查询，零回归面）。
    """
    rows = conn.execute(
        "select node_key, count(*) as cnt from agent_execution_requests"
        " where worker_id=%s and state='claimed' group by node_key",
        (worker_id,),
    ).fetchall()
    return {str(row["node_key"]): int(row["cnt"]) for row in rows}


def node_slot_open(limits: Mapping[str, int], row: Mapping[str, Any], state: Any) -> bool:
    """读相 hint（#1158 R1）：声明上限覆盖该 key 且 在跑 + 本批已选 >= 上限 时拒选。

    只是避免「选中一个写相必拒的节点导致空批、饿死其后节点」的预过滤——
    写相 ``worker_node_admits`` 的锁内重校验仍是权威判定，一行不动。
    在跑快照与本批已选记账在 ``state.node_active`` / ``state.node_chosen``
    （批选择段填；ScanState 的 per-pass accounting 家族）。skip reason 与
    写相同名 ``worker_node_limit_full``（语义同 capacity_full 的读相预检）。
    """
    node_key = str(row["node_key"])
    limit = limits.get(node_key)
    taken = state.node_active.get(node_key, 0) + state.node_chosen.get(node_key, 0)
    if limit is None or taken < limit:
        return True
    execution_id = str(row["execution_id"])
    if execution_id not in state.node_filtered:
        state.node_filtered.add(execution_id)
        state.skip_reasons["worker_node_limit_full"] += 1
    return False


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
