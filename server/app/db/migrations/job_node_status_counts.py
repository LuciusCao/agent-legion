"""Data migration applied alongside the idempotent DDL replay (v56)."""

from __future__ import annotations

from typing import Any

# Workspace job node status counts (schema v56, issue #121):
# count_workspace_job_nodes_by_status feeds the workspace DAG endpoint; as a
# join+group-by over job_nodes ⋈ jobs it is O(workspace job_nodes) per call
# and was measured at 48s on 260k jobs / 2.9M job_nodes (hash join spilling
# ~1GB to temp). The counter table is maintained transactionally by triggers
# on job_nodes and jobs (DB-JOB-NODE-STATUS-COUNTS-001), turning the read
# into a PK-prefix lookup regardless of table size. The backfill below
# rebuilds the table from job_nodes ⋈ jobs; on-conflict replace makes the
# whole migration idempotent and self-healing on replay.
_BACKFILL_SQL = """
insert into workspace_job_node_status_counts(workspace_id, workflow_key, node_key, status, cnt)
select j.workspace_id, j.workflow_key, jn.node_key, jn.status, count(*)
from job_nodes jn
join jobs j on j.id = jn.job_id
group by 1, 2, 3, 4
on conflict (workspace_id, workflow_key, node_key, status) do update set cnt = excluded.cnt
"""


def migrate_workspace_job_node_status_counts(conn: Any) -> None:
    """Backfill workspace_job_node_status_counts from job_nodes (v56); idempotent."""
    conn.execute(_BACKFILL_SQL)
