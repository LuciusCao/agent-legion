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
- The active count spans ``executor_leases`` without filtering executor_id:
  the local pool and remote claims write the same table, so the merged count
  covers both (shard candidates included — they run the same code branch in
  ``evaluate_candidate``).
- Over-limit is a skip (``node_limit_full``), never a cancel: the request
  stays queued for the next pass with the same semantics as
  ``capacity_full``, and the unclaimable sweeper (runtime/model probes only)
  never reaps it.

Serialization: the count-then-insert race must be mutually exclusive with
the local claim, so a candidate WITH a limit row takes the global code-pool
advisory xact lock the local path takes first in ``claim_lease``
(``_lease_claims.py``). The probe here is an unlocked existence read that
only decides whether the lock is needed: a limit row removed between probe
and check costs one redundant serialization; one added in that window skips
the lock for a single claim pass and the next pass counts it — the lock
order stays ``agent-worker → code-pool → job-mutation → request row``
(local path: ``code-pool → job-mutation`` is a subsequence; no agent-worker
edge on that side, so the graph stays acyclic).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def lock_code_pool_for_node_limit(conn: Any, selected: Mapping[str, Any]) -> None:
    """Take the shared code-pool xact lock when the node has a limit row.

    Runs inside the claim transaction's lock ladder (after agent-worker,
    before job-mutation — see the module docstring for the order proof).
    """
    probe = conn.execute(
        "select 1 from workspace_node_limits where workspace_id=%s and node_key=%s",
        (selected["workspace_id"], selected["node_key"]),
    ).fetchone()
    if probe is not None:
        conn.execute("select pg_advisory_xact_lock(hashtext(%s))", ("code-pool",))


def remote_node_limit_admits(conn: Any, selected: Mapping[str, Any], state: Any) -> bool:
    """Check the node limit against the merged active-lease count.

    ``state`` is the caller's ``ScanState`` (skip-reason accounting). Returns
    True when the claim may proceed (no limit row, or capacity remains); on
    over-limit it increments ``state.skip_reasons['node_limit_full']`` and
    returns False — the caller skips the candidate and the request stays
    queued.
    """
    limit_row = conn.execute(
        "select concurrency_limit from workspace_node_limits where workspace_id=%s and node_key=%s",
        (selected["workspace_id"], selected["node_key"]),
    ).fetchone()
    if limit_row is None:
        return True
    active = conn.execute(
        "select count(*) as cnt from executor_leases"
        " where workspace_id=%s and node_key=%s and status='active'"
        " and expires_at>current_timestamp",
        (selected["workspace_id"], selected["node_key"]),
    ).fetchone()
    if int(active["cnt"]) >= int(limit_row["concurrency_limit"]):
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
    row = conn.execute(
        "select concurrency_limit from workspace_node_limits where workspace_id=%s and node_key=%s",
        (workspace_id, node_key),
    ).fetchone()
    return int(row["concurrency_limit"]) if row is not None else 1
