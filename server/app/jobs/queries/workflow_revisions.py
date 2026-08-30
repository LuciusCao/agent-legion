"""Workflow revision writes on the JobQueries facade.

Why this module stays lean (#287): the ``workspace_node_routes`` /
``workspace_node_capacities`` projection writes moved to
``workflow_revision_projection.py``, and the revision snapshot *reads* to
``workflow_revision_reads.py`` — publication is the only writer, so the
write path here is the transaction around archive-then-insert plus the
projection rewrite (startup reconcile reuses the same projection helper).
``WorkflowRevisionQueriesMixin`` inherits the read mixin so the composed
JobQueries surface is unchanged.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.workflow_revision_projection import (
    create_workflow_revision_with_projection,
    write_agent_route_projection,
)
from server.app.jobs.queries.workflow_revision_reads import (
    WorkflowRevisionReadQueriesMixin,
)


class WorkflowRevisionQueriesMixin(WorkflowRevisionReadQueriesMixin):
    def materialize_agent_routes(
        self,
        *,
        workspace_id: str,
        workflow_key: str,
        agent_routes: dict[str, str],
    ) -> None:
        with self.connect() as conn:
            write_agent_route_projection(
                conn,
                workspace_id=workspace_id,
                workflow_key=workflow_key,
                agent_routes=agent_routes,
            )

    def create_workflow_revision(
        self,
        *,
        revision_id: str,
        workspace_id: str,
        workflow_key: str,
        version: int,
        status: str,
        definition_json: str,
        definition_hash: str,
        agent_routes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        # One transaction: archive the previous active revision, insert the
        # new one, and rewrite the agent-route projection — the projection
        # must be atomic with the insert that publishes it (#287; see
        # workflow_revision_projection for the helper contracts).
        with self.connect() as conn:
            row = create_workflow_revision_with_projection(
                conn,
                revision_id=revision_id,
                workspace_id=workspace_id,
                workflow_key=workflow_key,
                version=version,
                status=status,
                definition_json=definition_json,
                definition_hash=definition_hash,
                agent_routes=agent_routes,
            )
        if row is None:
            raise RuntimeError("workflow revision insert did not return a row")
        return dict(row)
