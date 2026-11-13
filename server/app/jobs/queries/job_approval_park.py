from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

APPROVAL_PARK_CANDIDATES_SQL = """
select distinct
  j.id, j.workspace_id, j.source_id, j.status,
  j.execution_paused, j.execution_mode, j.target_node_key,
  j.workflow_definition_hash, j.execution_generation,
  j.created_at, j.updated_at
from job_nodes jn
join jobs j on j.id = jn.job_id
where jn.needs_capacity = false
  and jn.status in ('pending', 'ready', 'stale')
  and j.status in ('queued', 'running', 'awaiting_approval')
  and j.execution_paused = 0
"""


class JobApprovalParkQueriesMixin(ConnectionQueriesMixin):
    def list_approval_park_candidates(self) -> list[dict[str, Any]]:
        """Lean job marks with at least one runnable capacity-free node."""
        with self._connect_read() as conn:
            rows = conn.execute(APPROVAL_PARK_CANDIDATES_SQL).fetchall()
        return [dict(row) for row in rows]
