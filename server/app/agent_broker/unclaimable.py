"""Sweep for queued Agent requests no registered Worker can ever claim.

Split out of ``sweepers.py`` for the file-size budget; mirrors
``fail_stale_definition_requests`` but judges claimability (definition runtime,
resolved model and capability against Worker declarations) instead of
definition staleness.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from server.app import agent_claim_compatibility
from server.app.agent_broker.unclaimable_reasons import WorkerDeclarations, unmatched_reasons
from server.app.db.transaction import write_transaction
from server.app.executors._failed_node_recording import record_failed_node_without_execution
from server.app.services import failure_classification

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

# Bounded window per sweep; the periodic caller picks up the rest next round.
_SWEEP_LIMIT = 256


def fail_unclaimable_model_requests(broker: AgentExecutionBroker) -> list[str]:
    """Fail queued requests no registered Worker could ever claim.

    The claim path resolves the effective (provider, model) from the job's
    pinned workflow revision (node ``execution`` overrides over manifest
    defaults) and matches it against Worker declarations. A job pinned to a
    revision missing those overrides resolves to a placeholder model no
    Worker declares, so the request would sit queued forever with no error —
    the same queue-rot failure mode as ``fail_stale_definition_requests``.
    Fail the node instead, pointing at the revision's execution overrides.

    With zero non-revoked Workers registered, do nothing: that is a
    deployment gap (e.g. a restart window), not a definition problem, and
    failing then would mass-kill a healthy queue."""
    failed: list[str] = []
    with write_transaction(broker.database_dsn) as conn:
        worker_rows = conn.execute(
            "select capabilities_json, models_json, runtimes_json"
            " from agent_workers where revoked_at is null"
        ).fetchall()
        if not worker_rows:
            return failed
        workers: list[WorkerDeclarations] = []
        for worker_row in worker_rows:
            capabilities, models = agent_claim_compatibility.worker_declarations(worker_row)
            runtimes = set(json.loads(worker_row["runtimes_json"] or "[]"))
            workers.append((runtimes, capabilities, models))
        # Requests whose pinned definition is disabled/changed are excluded
        # by the enabled-definition join: ``fail_stale_definition_requests``
        # owns them. The revision join mirrors the claim candidate query.
        rows = conn.execute(
            """
            select r.execution_id, r.job_id, r.node_key, r.manifest_json, d.capability,
                   d.runtime,
                   wr.definition_json as revision_definition_json
            from agent_execution_requests r
            join agent_definitions d
              on d.agent_id=r.agent_id and d.definition_hash=r.agent_definition_hash
             and d.enabled=1
            join jobs j on j.id=r.job_id
            left join workflow_revisions wr on wr.id=j.workflow_revision_id
            where r.state='queued'
            order by r.queued_at, r.execution_id
            limit %s
            for update of r skip locked
            """,
            (_SWEEP_LIMIT,),
        ).fetchall()
        for row in rows:
            manifest = agent_claim_compatibility.live_claim_manifest(row)
            reasons = unmatched_reasons(row, manifest, workers)
            if not reasons:
                continue
            error = (
                f"No registered Agent Worker can claim this request ({'; '.join(reasons)});"
                " check the pinned workflow revision's node execution overrides"
            )
            # Declared fields win over rule-based classification; like orphan
            # recovery, this sweeper knows the cause at its write path.
            failure_category, failure_detail = failure_classification.resolve_failure_fields(
                "failed", None, error, "technical", "unclaimable_model"
            )
            outcome = {"status": "failed", "exit_code": 1, "error_message": error}
            conn.execute(
                "update agent_execution_requests set state='done', outcome_json=%s,"
                " finished_at=current_timestamp where execution_id=%s",
                (json.dumps(outcome), row["execution_id"]),
            )
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
