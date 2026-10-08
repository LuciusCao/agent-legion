"""Settings-page read model: which active-revision nodes inlined which Agent (#1079).

#440 D1: after the v93 backfill the workspace "Agent 定义" directory becomes
a read-only history. Each historical definition shows how many nodes of the
current active revision carry its profile inline — read from the revision's
``agent_profile_provenance`` sibling (``services.agent_profile_provenance``).
Provenance is carried forward only while a node's profile fields stay
unchanged, so an entry here means "this node still runs the inlined copy of
that definition"; a node edited after inlining no longer counts.

Read-only by construction: the active revision comes from the JobQueries
facade (``get_active_workflow_revision``), nothing is written.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from server.app.services.agent_profile_provenance import provenance_from_revision_json

if TYPE_CHECKING:
    from server.app.jobs.queries import JobQueries


def _node_labels(definition_json: str | None) -> dict[str, str]:
    try:
        nodes = json.loads(str(definition_json)).get("nodes", {})
    except (AttributeError, TypeError, ValueError):
        return {}
    if not isinstance(nodes, dict):
        return {}
    return {
        str(key): str(node.get("label") or key)
        for key, node in nodes.items()
        if isinstance(node, dict)
    }


def list_workspace_agent_provenance(job_db: JobQueries, workspace_id: str) -> list[dict[str, Any]]:
    """Inlined nodes of the active revision, one entry per node (node_key order).

    No active revision, no provenance, or a corrupt stored definition all read
    as an empty list — the directory then shows every definition as inlined
    nowhere, which is the honest answer for a workspace that never ran v93
    against a published revision.
    """
    revision = job_db.get_active_workflow_revision(workspace_id, workspace_id)
    if revision is None:
        return []
    definition_json = revision.get("definition_json")
    provenance = provenance_from_revision_json(definition_json)
    labels = _node_labels(definition_json)
    entries: list[dict[str, Any]] = []
    for node_key in sorted(provenance):
        entry = provenance[node_key]
        agent_id = entry.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        version = entry.get("version")
        entries.append(
            {
                "node_key": node_key,
                "node_label": labels.get(node_key, node_key),
                "agent_id": agent_id,
                "agent_version": version if isinstance(version, int) else None,
            }
        )
    return entries
