"""Studio chat session context for the ``get_studio_context`` MCP tool (v45).

The bundled MCP server exposes one context tool per chat session; this module
resolves the session, enforces the run token's workspace binding, and assembles
the payload: the bound workspace id, the human's currently selected Studio node
(live value — the frontend pushes selection changes onto the session row), and
a structural summary of the workspace's active workflow revision.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import NotFoundError
from server.app.workflows.definition import workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def build_session_context(
    job_db: JobQueries, session_id: str, user: dict[str, Any]
) -> dict[str, Any]:
    """Assemble the context payload for one chat session.

    Authorization mirrors require_workspace_access: a bound run token may only
    read sessions of its own workspace; an unbound self-service token must
    belong to the session's workspace (admins pass). Mismatches are 404 (not
    403) so other workspaces' session ids cannot be probed.
    """
    session = job_db.get_studio_chat_session(session_id)
    if session is None or not _may_read_session(job_db, session, user):
        raise NotFoundError("Chat session not found")
    return {
        "workspace_id": session["workspace_id"],
        "selected_node_key": session.get("selected_node_key"),
        "workflow": _active_workflow_summary(job_db, str(session["workspace_id"])),
    }


def _may_read_session(job_db: JobQueries, session: dict[str, Any], user: dict[str, Any]) -> bool:
    bound = user.get("scoped_workspace_id")
    if bound is not None:
        return bool(session["workspace_id"] == bound)
    if user.get("role") == "admin":
        return True
    return job_db.get_workspace_role(str(session["workspace_id"]), str(user["id"])) is not None


def _active_workflow_summary(job_db: JobQueries, workspace_id: str) -> dict[str, Any] | None:
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        return None
    workflow_key = str(workspace.get("default_workflow_key") or "")
    revision = job_db.get_active_workflow_revision(workspace_id, workflow_key)
    if revision is None:
        return None
    definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
    return {
        "workflow_key": workflow_key,
        "version": int(revision["version"]),
        # The chat agent only needs executable nodes; the synthetic start
        # node carries no capability and would just be noise.
        "nodes": [
            {"key": key, "capability": node.capability}
            for key, node in definition.executable_nodes.items()
        ],
        "edges": [{"source": edge.source, "target": edge.target} for edge in definition.edges],
    }
