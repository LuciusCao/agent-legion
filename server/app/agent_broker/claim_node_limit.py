"""Node-level concurrency limit gate for remote code claims (issue #1149).

Split out of ``claim_evaluate.py`` (file budget): the ``(workspace_id,
node_key)`` check that ``workspace_node_limits`` previously enforced only on
the local code-pool path, letting remote code claims bypass it entirely.
Contract, deliberately different from the local path
(``executors/_lease_claim_limits.check_claim_capacity``):

- The limit value is read at claim time — the decision uses the CURRENT row,
  not a value carried on the request. Node limits are runtime-mutable
  settings: a queued request must not be fail-fasted because the setting
  changed after enqueue (the local path's carried-value vs current-value
  contract check is a dispatch-time violation detector, a different job).
- The active count spans ``executor_leases`` restricted to the code lease
  forms (#1167): the local pool (``executor_id='code'``) and remote code
  claims (``executor_id like 'agent:code:%'``) merge on the same table, so
  the merged count covers both (shard candidates included — they run the
  same code branch in ``evaluate_candidate``). Non-code ``agent:<id>``
  leases never count: a node_key that turned agent→code across revisions
  can have an old-revision agent job still running, and that execution is
  not code-pool concurrency (limit=1 must not let one stale agent lease
  block every new remote code claim).

  Counting-scope table — the model #1167 pinned; the root cause was this
  execution-kind dimension being absent from the original #1149 model
  (form × counted × basis; matrix nail: 用例 10 in
  ``tests/db/test_claim_node_limit_remote.py``):

  ==================  =======  ==========================================
  executor_id form    counted  basis
  ==================  =======  ==========================================
  ``code``            yes      local pool claim — the only form the
                               local claim path ever writes
                               (``_lease_claims.claim_lease``)
  ``agent:code:%``    yes      remote code claim (``claim_promote``,
                               kind=code; ``code_dispatch``'s mirror
                               carries the same prefix)
  ``agent:<id>``      no       Agent-lane execution — not code-pool
                               concurrency
  ``agent:code:<id>`` yes      boundary: an agent named ``code:<id>``
  (id contains ``:``)          (or a capability-derived id doing so)
                               rides the kind=agent claim path but
                               writes a lease the prefix test cannot
                               tell apart from a code claim — counted,
                               so the error direction is one stale
                               agent lease consuming code capacity
                               (the #1167 symptom) instead of
                               over-admitting
  ==================  =======  ==========================================

  Boundary premise (adversarial review P3-1; closed at the root by #1173
  codex round 2): row 4 is unreachable for NEW ids — the agent_id charset
  is enforced at the SERVICE write boundary, which closes every write
  entry (explicit request fields, capability-derived ids, PUT path
  params, Studio endpoints, copy new keys; single source
  ``agent_catalog.definition.AGENT_ID_RE``, gates
  ``AgentService.save_draft``/``copy``/``create_agent_draft``, the
  path×gate matrix lives in the constant's comment). Legacy
  pre-constraint rows are grandfathered in place (draft edits, publish,
  rollback and reads keep working; no new illegal key can enter through
  a supported write face) and stay conservative, never over-admitting —
  the residual is stock data only.

  The LOCAL path (``_lease_claim_limits.check_claim_capacity``) keeps a
  same-shaped residual: its node count also spans every active lease of
  the node (no form filter) — conservative on mixed agent→code node keys
  (skip-and-retry, never over-admitting), predates #1149, out of #1167's
  scope; tracked in #1171 with the same predicate spelled out for reuse.
- Over-limit is a skip (``node_limit_full``), never a cancel: the request
  stays queued for the next pass with the same semantics as
  ``capacity_full``, and the unclaimable sweeper (runtime/model probes only)
  never reaps it.

Serialization (#1149 adversarial review P2-1/P2-2):

- The count-then-insert race is serialized by the global code-pool advisory
  xact lock (the one the local ``claim_lease`` takes first). A candidate WITH
  a limit row takes it; unlimited nodes stay lock-free (no queueing overhead
  in the common fleet shape).
- ``workspace_node_limits`` configuration writes
  (``jobs/node_limits.replace_workspace_node_limits``) take the same lock
  BEFORE writing any limit row: a lock-holding claim's limit view is stable
  mid-transaction (no insert/update/delete of a limit row can commit while
  it holds the lock), and the write itself serializes with in-flight claims.
- The claim side never counts unlocked: the check enforces only under the
  held lock. A row that appeared after the probe decided not to lock (a
  first-config insert committing inside the claim's probe→check window)
  skips with ``node_limit_appeared`` — the request stays queued and the next
  pass probes the row and claims under the lock. The residual (a claim that
  evaluated before the first configuration exists admits unlimited) matches
  the local pool's dispatch-carried semantics and is bounded to the
  configuration round.
- The batch write phase takes the lock once at transaction START — before
  any candidate's job-mutation lock (P2-2: per-candidate acquisition inside
  the loop would invert the order once candidate 1's job-mutation is held;
  a concurrent local claim holding code-pool and waiting on that
  job-mutation closes the cycle). The centralized probe covers every code
  candidate's (workspace, node) pair; a row appearing after it cannot be
  acquired mid-batch (the frozen decision suppresses per-candidate
  acquisition) and skips with ``node_limit_appeared`` instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def lock_code_pool(conn: Any) -> None:
    """Acquire the global code-pool advisory xact lock (re-entrant per xact)."""
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", ("code-pool",))


def _limit_row(conn: Any, workspace_id: str, node_key: str) -> Any:
    return conn.execute(
        "select concurrency_limit from workspace_node_limits where workspace_id=%s and node_key=%s",
        (workspace_id, node_key),
    ).fetchone()


def enter_code_pool_domain(
    conn: Any, selected: Mapping[str, Any], kind: str, batch_code_pool_lock: bool | None = None
) -> bool:
    """The claim ladder's code-pool step (#1149); returns whether this
    transaction holds the code-pool lock.

    ``batch_code_pool_lock=None`` (single claim): probe the node's limit row
    and acquire the lock when one exists — called between agent-worker and
    job-mutation, the acquisition is order-correct. ``True``/``False`` (batch
    write phase): the start-of-transaction probe (P2-2) already decided;
    never acquire here — earlier candidates' job-mutation locks are held and
    a late acquisition would invert the global order.
    """
    if kind != "code" or batch_code_pool_lock is not None:
        return bool(batch_code_pool_lock)
    if _limit_row(conn, selected["workspace_id"], selected["node_key"]) is None:
        return False
    lock_code_pool(conn)
    return True


def lock_code_pool_for_batch(conn: Any, candidates: Sequence[Any]) -> bool:
    """P2-2 centralized probe: any limit row among the batch's code candidates.

    Runs at the batch write transaction's start — BEFORE the loop takes any
    job-mutation lock. Precise pair probe (one indexed query via paired
    unnest) rather than "any code candidate → lock unconditionally": the
    unlimited configuration keeps its zero-lock write phase, so the batch
    fleet does not globally serialize on code-pool unless limits are
    actually configured. Returns whether the lock was acquired (held to
    COMMIT).
    """
    code_candidates = [row for row in candidates if str(row["kind"]) == "code"]
    if not code_candidates:
        return False
    pairs = sorted({(str(r["workspace_id"]), str(r["node_key"])) for r in code_candidates})
    row = conn.execute(
        "select 1 from workspace_node_limits l"
        " join unnest(%s::text[], %s::text[]) as p(workspace_id, node_key)"
        " on l.workspace_id=p.workspace_id and l.node_key=p.node_key limit 1",
        ([workspace for workspace, _ in pairs], [node for _, node in pairs]),
    ).fetchone()
    if row is None:
        return False
    lock_code_pool(conn)
    return True


def code_claim_admits(
    conn: Any, selected: Mapping[str, Any], kind: str, state: Any, pool_held: bool
) -> bool:
    """The claim's node-limit check (claim-time current value, #1149).

    Enforces only under the held code-pool lock. A limit row visible without
    the lock appeared after the probe decided not to lock (P2-1 first-config
    race): counting unlocked could over-admit against a concurrent
    lock-holding claimant, so skip — the request stays queued and the next
    pass claims under the lock. Over-limit increments
    ``state.skip_reasons['node_limit_full']``; both skips keep the request
    queued (capacity_full semantics; the unclaimable sweeper never reaps it).
    """
    if kind != "code":
        return True
    row = _limit_row(conn, selected["workspace_id"], selected["node_key"])
    if row is None:
        return True
    if not pool_held:
        state.skip_reasons["node_limit_appeared"] += 1
        return False
    # #1167：计数只并「code 形态」租约（本地 'code' + 远程 'agent:code:%'，
    # 前缀与 claim_promote / code_dispatch 的写侧字面量同形，测试钉住），
    # 排除非 code 的 'agent:<id>'。口径对齐结论：本地路径
    # ``executors/_lease_claim_limits.check_claim_capacity`` 的节点计数同样
    # 不筛 executor_id（按 (workspace_id, node_key) 全计）——它自身的 claim
    # 永远写 'code'，但同 node_key 的跨形态 agent 租约同样进入其计数
    # （agent→code 跨 revision 的混合 node_key 是受支持形态，见 routing
    # 的 route_cache_key）。该残留与 #1167 同形、方向保守（skip 重试、
    # 永不超收），先于 #1149 存在、不属本 finding 范围，此处不动本地路径。
    active = conn.execute(
        "select count(*) as cnt from executor_leases where workspace_id=%s and node_key=%s"
        " and status='active' and expires_at>current_timestamp"
        " and (executor_id='code' or executor_id like 'agent:code:%%')",
        (selected["workspace_id"], selected["node_key"]),
    ).fetchone()
    if int(active["cnt"]) >= int(row["concurrency_limit"]):
        state.skip_reasons["node_limit_full"] += 1
        return False
    return True


def node_limit_audit_value(conn: Any, workspace_id: str, node_key: str) -> int:
    """Enqueue-time audit snapshot of the node limit (issue #1149).

    1 records "no configured limit (unlimited)". Audit-only, never enforced —
    the authoritative check is the claim transaction, which reads the current
    value per claim (runtime-mutable setting; queued requests must not be
    fail-fasted on a setting change after enqueue).
    """
    row = _limit_row(conn, workspace_id, node_key)
    return int(row["concurrency_limit"]) if row is not None else 1
