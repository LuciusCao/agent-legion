"""Capacity checks for the code-pool claim path.

Split out of ``_lease_claims.py`` (file budget): the workspace node-limit
validation plus the global/node lease counts that gate every claim.
"""

from __future__ import annotations

from server.app.db.connection import DatabaseConnection
from server.app.executors.models import LeaseClaimRequest


def check_claim_capacity(
    conn: DatabaseConnection, request: LeaseClaimRequest, now_str: str
) -> bool:
    """Validate the node limit and count active leases; False = no capacity.

    Raises ValueError for limit configuration mismatches (dispatch-time
    contract violations, surfaced as claim rejection upstream).

    Counting scope (#1167 fallout, tracked in #1171): the node-level count
    below spans every active lease of the node — it does NOT filter lease
    forms. The local claim path only ever writes ``executor_id='code'`` rows
    itself, but a node_key that turned agent→code across revisions can hold
    a live ``agent:<id>`` lease, and that lease also lands in this count —
    conservative on mixed node keys (the claim skips and retries next pass,
    never over-admitting). The remote claim gate filters to the code lease
    forms (``claim_node_limit``, #1167); aligning this local count with the
    same predicate is the #1171 follow-up.
    """
    if request.local_node_limit is not None:
        # #211 Phase 3 (read-layer binding): predicates key on
        # (workspace_id, node_key) — workflow_key equals the workspace id on
        # every row (v62 binding, aligned by v68).
        limit_row = conn.execute(
            """
            select concurrency_limit
            from workspace_node_limits
            where workspace_id=%s and node_key=%s
            """,
            (request.workspace_id, request.node_key),
        ).fetchone()
        if limit_row is None:
            raise ValueError(
                f"No local node limit for {request.node_key} in {request.workspace_id}/{request.workflow_key}"
            )
        if limit_row["concurrency_limit"] != request.local_node_limit:
            raise ValueError(
                f"Local node limit mismatch for {request.node_key}: "
                f"persisted {limit_row['concurrency_limit']} vs requested {request.local_node_limit}"
            )

    global_count_row = conn.execute(
        """
        select count(*) as cnt
        from executor_leases
        where executor_id=%s and status='active' and expires_at>%s
        """,
        (request.executor_id, now_str),
    ).fetchone()
    global_count = int(global_count_row["cnt"]) if global_count_row is not None else 0
    if global_count >= request.global_capacity:
        return False

    if request.local_node_limit is not None:
        node_count_row = conn.execute(
            """
            select count(*) as cnt
            from executor_leases
            where workspace_id=%s and node_key=%s and status='active' and expires_at>%s
            """,
            (request.workspace_id, request.node_key, now_str),
        ).fetchone()
        node_count = int(node_count_row["cnt"]) if node_count_row is not None else 0
        if node_count >= request.local_node_limit:
            return False

    return True
