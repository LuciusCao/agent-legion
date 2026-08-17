"""Read model for workspace Agent routes.

Routes are materialized projections of the currently published workflow
revision; this service only reads them for settings-page display. Agent
capacity is workspace-level (``workspace_agent_capacities``, exposed via the
workspace configuration payload).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.jobs.queries import JobQueries


def list_workspace_agent_routes(job_db: JobQueries, workspace_id: str) -> list[dict[str, Any]]:
    with job_db._connect_read() as conn:
        rows = conn.execute(
            """
            select r.workflow_key, r.node_key, r.target_id as agent_id,
                   d.definition_json::jsonb->>'capability' as capability,
                   d.definition_json
            from workspace_node_routes r
            join versioned_entities d
              on d.entity_type='agent' and d.workspace_id = r.workspace_id
             and d.entity_key = r.target_id and d.status='published'
            where r.workspace_id = %s and r.target_kind = 'agent'
            order by r.workflow_key, r.node_key
            """,
            (workspace_id,),
        ).fetchall()
        labels: dict[tuple[str, str], str] = {}
        revisions = conn.execute(
            """
            select workflow_key, definition_json from workflow_revisions
            where workspace_id = %s and status = 'active'
            """,
            (workspace_id,),
        ).fetchall()
    for revision in revisions:
        try:
            nodes = json.loads(revision["definition_json"]).get("nodes", {})
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(nodes, dict):
            continue
        for node_key, node in nodes.items():
            if isinstance(node, dict):
                key = (str(revision["workflow_key"]), str(node_key))
                labels[key] = str(node.get("label") or node_key)

    routes: list[dict[str, Any]] = []
    for row in rows:
        try:
            skill = str(json.loads(row["definition_json"]).get("skill") or "")
        except (TypeError, json.JSONDecodeError):
            skill = ""
        workflow_key = str(row["workflow_key"])
        node_key = str(row["node_key"])
        routes.append(
            {
                "workflow_key": workflow_key,
                "node_key": node_key,
                "node_label": labels.get((workflow_key, node_key), node_key),
                "capability": str(row["capability"]),
                "agent_id": str(row["agent_id"]),
                "agent_skill": skill,
            }
        )
    return routes
