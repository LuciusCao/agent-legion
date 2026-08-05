"""Mutable runtime settings for an otherwise immutable workflow revision."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING

from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def _structural_payload(definition: WorkflowDefinition) -> dict:
    payload = asdict(definition)
    for node in payload["nodes"].values():
        node.pop("execution", None)
    return payload


def save_revision_runtime_or_publish(
    job_db: JobQueries,
    workspace_id: str,
    definition: WorkflowDefinition,
    publish: Callable[[str, WorkflowDefinition], dict],
) -> dict:
    active = job_db.get_active_workflow_revision(workspace_id, definition.key)
    if active is None:
        return publish(workspace_id, definition)
    current = workflow_definition_from_dict(json.loads(str(active["definition_json"])))
    if _structural_payload(current) != _structural_payload(definition):
        return publish(workspace_id, definition)
    definition_json = serialize_definition(definition)
    with job_db.connect() as conn:
        row = conn.execute(
            "update workflow_revisions set definition_json=%s, definition_hash=%s"
            " where id=%s returning *",
            (definition_json, definition_hash(definition_json), active["id"]),
        ).fetchone()
    if row is None:
        raise ValueError("workflow revision not found")
    return dict(row)
