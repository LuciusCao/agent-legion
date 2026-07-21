from __future__ import annotations

from pathlib import Path
from typing import Any


def bootstrap_default_workspace(conn: Any) -> None:
    """Create the built-in workspace and its first active workflow revision."""
    from server.app.services.workflow_revision_format import definition_hash, serialize_definition
    from server.app.workflows.definition import load_workflow_definition

    workspace_id = "question_comprehension"
    workflow_key = "question_comprehension_info"
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key, default_entity)"
        " values (?, ?, ?, 'question') on conflict (id) do nothing",
        (workspace_id, "题目审题信息", workflow_key),
    )
    existing = conn.execute(
        "select 1 from workflow_revisions"
        " where workspace_id=? and workflow_key=? and status='active'",
        (workspace_id, workflow_key),
    ).fetchone()
    if existing is not None:
        return
    root_dir = Path(__file__).resolve().parents[3]
    definition = load_workflow_definition(
        root_dir / "config" / "workflows" / f"{workflow_key}.yaml"
    )
    definition_json = serialize_definition(definition)
    conn.execute(
        "insert into workflow_revisions("
        " id, workspace_id, workflow_key, version, status, definition_json,"
        " definition_hash, published_at)"
        " values (?, ?, ?, 1, 'active', ?, ?, current_timestamp)",
        (
            f"{workspace_id}:{workflow_key}:v1",
            workspace_id,
            workflow_key,
            definition_json,
            definition_hash(definition_json),
        ),
    )
