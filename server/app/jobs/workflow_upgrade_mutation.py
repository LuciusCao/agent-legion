from __future__ import annotations

from server.app.db.connection import DatabaseConnection
from server.app.jobs.workflow_upgrade_mutation_inherit import (
    upgrade_job_workflow_inherit,
)

__all__ = ["upgrade_job_workflow", "upgrade_job_workflow_inherit"]


def upgrade_job_workflow(
    conn: DatabaseConnection,
    job_id: str,
    *,
    workflow_revision_id: str,
    workflow_version: int,
    workflow_definition_hash: str,
    workflow_definition_snapshot_json: str,
    node_keys: list[str],
    frozen_config_json: str | None = None,
) -> None:
    """Clean-mode legacy signature（issue #645 前的调用面）：全量 pending 重置。"""
    upgrade_job_workflow_inherit(
        conn,
        job_id,
        workflow_revision_id=workflow_revision_id,
        workflow_version=workflow_version,
        workflow_definition_hash=workflow_definition_hash,
        workflow_definition_snapshot_json=workflow_definition_snapshot_json,
        node_keys=node_keys,
        frozen_config_json=frozen_config_json,
        inherit_nodes=frozenset(),
    )
