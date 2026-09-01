"""Approval-gate persistence on the JobQueries facade (BOUNDARY-DATA-001).

The service layer (``services/approval_decisions.py``) keeps the decision
flow but reaches the ``approval_decisions`` table and the gate's job_node
transition through these facade methods; the raw SQL lives here with the
rest of the queries layer. Every decision write re-validates the gate still
being ``awaiting_approval`` inside the same transaction (EXEC-APPROVAL-001),
raising ``ApprovalGateConflict`` when a concurrent decision or reset won.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

_AWAITING = "awaiting_approval"

_INSERT_DECISION = """
insert into approval_decisions(
  id, job_id, node_key, verdict, note, rework_target, decided_by
) values (%s, %s, %s, %s, %s, %s, %s)
"""


class ApprovalGateConflict(ValueError):
    """The gate is not awaiting approval (already decided, reset, or missing)."""

    def __init__(self, message: str, *, missing: bool = False) -> None:
        super().__init__(message)
        self.missing = missing


def _guard_awaiting(conn: Any, job_id: str, node_key: str) -> None:
    row = conn.execute(
        "select status from job_nodes where job_id=%s and node_key=%s",
        (job_id, node_key),
    ).fetchone()
    if row is None:
        raise ApprovalGateConflict(f"Node {node_key} not found for job", missing=True)
    if row["status"] != _AWAITING:
        raise ApprovalGateConflict(
            f"Node {node_key} is not awaiting approval (status: {row['status']})"
        )


def _insert_decision(conn: Any, decision: dict[str, Any]) -> None:
    conn.execute(
        _INSERT_DECISION,
        (
            decision["id"],
            decision["job_id"],
            decision["node_key"],
            decision["verdict"],
            decision["note"],
            decision["rework_target"],
            decision["decided_by"],
        ),
    )


class ApprovalDecisionQueriesMixin(ConnectionQueriesMixin):
    def list_approval_decisions(self, job_id: str) -> list[dict[str, Any]]:
        """Full decision history for one job, newest first (insert-only rows)."""
        with self.read() as conn:
            rows = conn.execute(
                """
                select id, job_id, node_key, verdict, note, rework_target,
                       decided_by, created_at
                from approval_decisions
                where job_id=%s
                order by created_at desc, id desc
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_approval_decisions(self, job_id: str, node_key: str) -> int:
        with self.read() as conn:
            row = conn.execute(
                "select count(*) as cnt from approval_decisions where job_id=%s and node_key=%s",
                (job_id, node_key),
            ).fetchone()
        return int(row["cnt"]) if row is not None else 0

    def approve_gate_atomic(self, decision: dict[str, Any]) -> None:
        """approved: guard + insert decision + complete the gate + re-derive job status."""
        # Local import: executors.leases imports the jobs package, so a
        # module-level import here would close an import cycle.
        from server.app.executors._lease_control import sync_job_status

        job_id, node_key = decision["job_id"], decision["node_key"]
        with self.write() as conn:
            _guard_awaiting(conn, job_id, node_key)
            _insert_decision(conn, decision)
            conn.execute(
                """
                update job_nodes
                set status='completed', error_message='', finished_at=current_timestamp
                where job_id=%s and node_key=%s
                """,
                (job_id, node_key),
            )
            sync_job_status(conn, job_id)

    def reject_gate_atomic(self, decision: dict[str, Any], message: str) -> None:
        """rejected: guard + insert decision + fail the gate + re-derive job status."""
        from server.app.executors._lease_control import sync_job_status

        job_id, node_key = decision["job_id"], decision["node_key"]
        with self.write() as conn:
            _guard_awaiting(conn, job_id, node_key)
            _insert_decision(conn, decision)
            conn.execute(
                """
                update job_nodes
                set status='failed', error_message=%s,
                    failure_category='approval_rejected', failure_detail='approval_rejected',
                    finished_at=current_timestamp
                where job_id=%s and node_key=%s
                """,
                (message, job_id, node_key),
            )
            sync_job_status(conn, job_id)

    @staticmethod
    def record_rework_decision_in_transaction(conn: Any, decision: dict[str, Any]) -> None:
        """rework: guard + insert decision inside the caller's transaction.

        The caller (approval service) composes this with
        ``mark_nodes_for_rerun_in_transaction`` under one
        ``lease_guarded_mutation`` so the audit row and the node reset commit
        or roll back together — a rework decision must never outlive a failed
        reset (EXEC-APPROVAL-001).
        """
        _guard_awaiting(conn, decision["job_id"], decision["node_key"])
        _insert_decision(conn, decision)

    def approval_gate_status(self, job_id: str, node_key: str) -> str | None:
        """The gate's current job_node status (None when the row is missing)."""
        with self.read() as conn:
            row = conn.execute(
                "select status from job_nodes where job_id=%s and node_key=%s",
                (job_id, node_key),
            ).fetchone()
        return str(row["status"]) if row is not None else None
