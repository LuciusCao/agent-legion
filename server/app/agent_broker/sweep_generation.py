"""EXEC-GENERATION-001 sweep-side lock/CAS prelude for queued-request sweeps.

Shared by ``sweeper_definitions.fail_stale_definition_requests`` and
``unclaimable.fail_unclaimable_model_requests`` — the two queued-request
sweeps that write job_nodes/jobs. Both scan lock-free, walk candidates in
the single global batch order (hashtext('agent-ws:' || workspace_id)::int,
job_id), and run this prelude per row before any write:

- the per-job mutation advisory lock comes FIRST (mirroring
  claim_evaluate's reorder): the mutation side (``lease_guarded_mutation``)
  holds job-mutation while cancelling queued rows (``_cancel_queued_sql``),
  so a sweep holding the request row lock and then writing job_nodes/jobs
  would AB-BA against job-mutation → request row;
- the request row's FOR UPDATE is taken only AFTER the advisory lock;
- generation CAS: a request stamped with a pre-reset epoch is cancelled
  with the mutation side's cancel semantics (``cancel_request``) and its
  job_nodes rows stay untouched — the reset already rebuilt them for the
  new epoch, whose dispatch re-enqueues; failing them would flip the new
  epoch's fresh pending rows.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from server.app.agent_broker.manifest_trim import cancel_request
from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import lock_job_mutation_and_read_generation

logger = logging.getLogger(__name__)


def lock_sweep_candidate(conn: DatabaseConnection, row: Mapping[str, Any]) -> bool:
    """Lock one sweep candidate into the mutation protocol; True = current
    generation, caller may apply its fail semantics.

    False covers both skip cases: the row left 'queued' (or is locked by a
    concurrent claim/sweep) since the lock-free scan, and the
    generation-stale cancel."""
    current_generation = lock_job_mutation_and_read_generation(conn, str(row["job_id"]))
    locked = conn.execute(
        "select execution_generation from agent_execution_requests"
        " where execution_id=%s and state='queued' for update skip locked",
        (row["execution_id"],),
    ).fetchone()
    if locked is None:
        return False
    if current_generation is None or int(locked["execution_generation"]) != current_generation:
        # 旧代次迟到清扫（与 sweep_expired_claims 的 stale 分支同语义）：
        # mutation 已重置现场，请求取消即可——job_nodes 是新代次的行，绝不动。
        logger.info(
            "sweep cancelled stale-generation queued request: exec=%s job=%s node=%s"
            " request_generation=%s",
            row["execution_id"],
            row["job_id"],
            row["node_key"],
            locked["execution_generation"],
        )
        cancel_request(conn, str(row["execution_id"]))
        return False
    return True
