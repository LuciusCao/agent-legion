"""Read model for workspace Agent routes.

Routes are materialized projections of the currently published workflow
revision; this service only reads them for settings-page display. Agent
capacity is workspace-level (``workspace_agent_capacities``, exposed via the
workspace configuration payload).

#1079（#440 P3b）：自 P3 起全自含 revision 不再 upsert 路由行，存量行冻结
只读、只服务 v93 内联前的旧快照。设置页展示的是「当前 workflow 的节点走哪个
Agent」，故只保留 active revision 中仍是 legacy（未自含）agent 节点的路由行；
已自含的节点、已删除或已非 agent 的节点不再显示冻结映射。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node

if TYPE_CHECKING:
    from server.app.jobs.queries import JobQueries


def _legacy_agent_node_labels(definition_json: str | None) -> dict[str, str] | None:
    """node_key → label of the legacy agent nodes; None when the revision won't load.

    None keeps the pre-#1079 behaviour (show every route row) rather than
    hiding routes behind a definition this reader cannot interpret.
    """
    if not definition_json:
        return {}
    try:
        definition = workflow_definition_from_dict(json.loads(definition_json))
    except (TypeError, ValueError):
        return None
    return {
        key: str(node.label or key)
        for key, node in definition.nodes.items()
        if node.node_type == "agent" and not is_self_contained_agent_node(node)
    }


def list_workspace_agent_routes(job_db: JobQueries, workspace_id: str) -> list[dict[str, Any]]:
    with job_db._connect_read() as conn:
        rows = conn.execute(
            """
            select r.node_key, r.target_id as agent_id,
                   d.definition_json::jsonb->>'capability' as capability,
                   d.definition_json
            from workspace_node_routes r
            join versioned_entities d
              on d.entity_type='agent' and d.workspace_id = r.workspace_id
             and d.entity_key = r.target_id and d.status='published'
            where r.workspace_id = %s and r.target_kind = 'agent'
            order by r.node_key
            """,
            (workspace_id,),
        ).fetchall()
    revision = job_db.get_active_workflow_revision(workspace_id, workspace_id)
    labels = _legacy_agent_node_labels(revision.get("definition_json") if revision else None)

    routes: list[dict[str, Any]] = []
    for row in rows:
        node_key = str(row["node_key"])
        if labels is not None and node_key not in labels:
            continue
        try:
            skill = str(json.loads(row["definition_json"]).get("skill") or "")
        except (TypeError, json.JSONDecodeError):
            skill = ""
        routes.append(
            {
                "node_key": node_key,
                "node_label": (labels or {}).get(node_key, node_key),
                "capability": str(row["capability"]),
                "agent_id": str(row["agent_id"]),
                "agent_skill": skill,
            }
        )
    return routes
