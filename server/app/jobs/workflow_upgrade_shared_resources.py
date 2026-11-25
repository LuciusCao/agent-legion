"""Fail-closed shared-resource declaration guard for workflow upgrades."""

from __future__ import annotations

from server.app.db.connection import DatabaseConnection
from server.app.jobs.job_state_mutations import JobMutationConflict
from server.app.workflows.shared_resource_upgrade import declarations_from_snapshot


def assert_shared_resource_declarations_unchanged(
    conn: DatabaseConnection, job_id: str, workflow_definition_snapshot_json: str
) -> None:
    current = conn.execute(
        "select workflow_definition_snapshot_json from jobs where id=%s", (job_id,)
    ).fetchone()
    if current is None:
        raise ValueError(f"Job not found: {job_id}")
    previous = declarations_from_snapshot(str(current["workflow_definition_snapshot_json"] or ""))
    upgraded = declarations_from_snapshot(workflow_definition_snapshot_json)
    pinned = conn.execute(
        "select 1 from shared_resource_job_pins where job_id=%s limit 1", (job_id,)
    ).fetchone()
    if previous != upgraded or (not previous and not upgraded and pinned is not None):
        raise JobMutationConflict(
            "shared_resource_declaration_changed",
            "Shared resource declarations must remain identical to the job's frozen pins",
        )
