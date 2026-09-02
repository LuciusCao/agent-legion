"""Fail queued requests pinned to stale Agent definitions.

Split from ``sweepers.py`` when the #389 shard-aware requeue changes
outgrew the parent's size budget. This sweep owns a separate concern
from Worker-loss requeue: a queued request whose pinned definition was
disabled or edited would sit queued forever while ``has_active_request``
blocks re-enqueue.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from server.app.agent_broker.manifest_trim import MANIFEST_TRIM
from server.app.db.transaction import write_transaction
from server.app.executors._failed_node_recording import record_failed_node_without_execution
from server.app.services import failure_classification

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

# Shared terminal 'done' write (same contract as sweepers._SWEEP_DONE_SQL):
# closes the request and slims the manifest back to the audit stub.
_SWEEP_DONE_SQL = (
    "update agent_execution_requests set state='done', outcome_json=%s,"
    " finished_at=current_timestamp, manifest_json=" + MANIFEST_TRIM + " where execution_id=%s"
)


def fail_stale_definition_requests(broker: AgentExecutionBroker) -> list[str]:
    """Fail queued requests whose pinned Agent definition is gone or disabled.

    The claim query joins the CURRENT enabled definition hash, so a request
    pinned to an edited/disabled definition would otherwise sit queued
    forever while ``has_active_request`` blocks re-enqueue."""
    failed: list[str] = []
    with write_transaction(broker.database_dsn) as conn:
        rows = conn.execute(
            """
            select r.execution_id, r.job_id, r.node_key, r.agent_id
            from agent_execution_requests r
            where r.state='queued'
              -- kind='code' payloads are self-contained: no versioned Agent
              -- definition exists for them by design (batch 2).
              and r.kind='agent'
              and not exists (
                  select 1 from versioned_entities d
                  where d.entity_type='agent' and d.workspace_id=r.workspace_id
                    and d.entity_key=r.agent_id
                    and d.definition_hash=r.agent_definition_hash
                    -- Quality replay pins stay valid while their immutable
                    -- version row exists, whatever its lifecycle status.
                    and ((r.pinned_agent_version is not null
                          and d.version=r.pinned_agent_version)
                         or (r.pinned_agent_version is null and d.status='published'))
              )
            for update of r skip locked
            """
        ).fetchall()
        for row in rows:
            error = (
                f"Agent definition {row['agent_id']!r} was disabled or changed"
                " while the request was queued"
            )
            failure_category, failure_detail = failure_classification.resolve_failure_fields(
                "failed", None, error
            )
            outcome = {"status": "failed", "exit_code": 1, "error_message": error}
            conn.execute(_SWEEP_DONE_SQL, (json.dumps(outcome), row["execution_id"]))
            updated = record_failed_node_without_execution(
                conn,
                job_id=str(row["job_id"]),
                node_key=str(row["node_key"]),
                error_message=error,
                failure_category=failure_category,
                failure_detail=failure_detail,
            )
            if updated is not None:
                conn.execute(
                    "update jobs set status='failed', error_message=%s,"
                    " updated_at=current_timestamp"
                    " where id=%s and status not in ('failed', 'completed')",
                    (error, row["job_id"]),
                )
            failed.append(str(row["execution_id"]))
    return failed
