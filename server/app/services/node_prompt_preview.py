"""Node prompt preview and draft-prompt editing services.

``execution.prompt`` semantics: empty means the platform auto-assembles the
default node instructions; a non-empty value replaces the default wholesale.
The preview mirrors ``render_command_spec`` exactly — same ``build_prompt``
call, same ``{job_dir}``/``{skill_dir}`` placeholders — so what Studio (or an
authoring agent) sees is what the runtime ships.

All DB access goes through the JobQueries facade (BOUNDARY-DATA-001).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.agent_service import published_agent_definitions
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.workflow_draft_store import save_workflow_draft
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string
from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.node_prompt import build_default_node_instructions
from server.app.workflows.pi_protocol import (
    JOB_DIR_PLACEHOLDER,
    SKILL_DIR_PLACEHOLDER,
    build_prompt,
)
from server.app.workflows.revision_format import definition_to_yaml
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    WorkflowNode,
)

_PREVIEW_JOB_ID = "<job_id>"


def _definition_for_preview(
    job_db: JobQueries, workspace_id: str, definition_yaml: str | None
) -> WorkflowDefinition:
    if definition_yaml and definition_yaml.strip():
        try:
            return workflow_definition_from_yaml_string(definition_yaml)
        except WorkflowDefinitionError as exc:
            raise InvalidOperationError(str(exc)) from exc
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        raise NotFoundError("Workspace not found")
    workflow_key = str(workspace.get("default_workflow_key") or "")
    revision = (
        job_db.get_active_workflow_revision(workspace_id, workflow_key) if workflow_key else None
    )
    if revision is None:
        raise NotFoundError("No active workflow revision")
    return workflow_definition_from_dict(json.loads(str(revision["definition_json"])))


def _locate_executable_node(definition: WorkflowDefinition, node_key: str) -> WorkflowNode:
    node = definition.nodes.get(node_key)
    if node is None:
        raise NotFoundError(f"Unknown workflow node: {node_key}")
    if node.node_type == "start":
        raise InvalidOperationError(
            f"Start node {node_key!r} never executes and has no agent prompt"
        )
    return node


def _skill_key_for_node(job_db: JobQueries, workspace_id: str, node: WorkflowNode) -> str | None:
    """Skill of the workspace's published Agent bound to the node's capability."""
    for definition in published_agent_definitions(job_db, workspace_id).values():
        if definition.capability == node.capability:
            return definition.skill
    return None


def _preview_payload(job_db: JobQueries, workspace_id: str, node: WorkflowNode) -> dict[str, Any]:
    skill_key = _skill_key_for_node(job_db, workspace_id, node)
    custom = node.execution.prompt
    default_instructions = build_default_node_instructions(
        node_key=node.key,
        label=node.label or node.key,
        capability=node.capability,
        skill=skill_key or "",
        inputs=node.inputs,
        expected_outputs=node.outputs,
    )
    manifest = {
        "job_id": _PREVIEW_JOB_ID,
        "node_key": node.key,
        "node_label": node.label or node.key,
        "capability": node.capability,
        "skill": skill_key or "",
        "inputs": list(node.inputs),
        "expected_outputs": list(node.outputs),
        "additional_prompt": custom,
    }
    effective_prompt = build_prompt(
        manifest,
        job_dir=Path(JOB_DIR_PLACEHOLDER),
        skill_dir=Path(SKILL_DIR_PLACEHOLDER),
    )
    return {
        "effective_prompt": effective_prompt,
        "default_instructions": default_instructions,
        "custom_instructions": custom,
        "is_default": not custom.strip(),
        "skill_key": skill_key,
    }


def preview_node_prompt(
    job_db: JobQueries,
    workspace_id: str,
    node_key: str,
    definition_yaml: str | None = None,
) -> dict[str, Any]:
    definition = _definition_for_preview(job_db, workspace_id, definition_yaml)
    node = _locate_executable_node(definition, node_key)
    return _preview_payload(job_db, workspace_id, node)


def save_node_prompt(
    job_db: JobQueries, workspace_id: str, node_key: str, prompt: str
) -> dict[str, Any]:
    """Write ``execution.prompt`` for one node into the workspace draft YAML.

    The edit bases on the current canvas draft when one exists, otherwise on
    the active revision's canonical YAML; an empty prompt clears the key so
    the node falls back to the auto-assembled default instructions. The new
    draft is fully built and validated before the single upsert applies it.
    """
    draft = job_db.get_workspace_workflow_draft(workspace_id)
    base_yaml = str(draft["definition_yaml"]) if draft is not None else None
    if base_yaml is None:
        workspace = job_db.get_workspace(workspace_id)
        if workspace is None:
            raise NotFoundError("Workspace not found")
        workflow_key = str(workspace.get("default_workflow_key") or "")
        revision = (
            job_db.get_active_workflow_revision(workspace_id, workflow_key)
            if workflow_key
            else None
        )
        if revision is None:
            raise NotFoundError("No workflow draft or active revision to edit")
        base_definition = workflow_definition_from_dict(
            json.loads(str(revision["definition_json"]))
        )
    else:
        try:
            base_definition = workflow_definition_from_yaml_string(base_yaml)
        except WorkflowDefinitionError as exc:
            raise InvalidOperationError(f"Current workflow draft is invalid: {exc}") from exc
    node = _locate_executable_node(base_definition, node_key)
    updated_node = replace(node, execution=replace(node.execution, prompt=prompt))
    updated_definition = replace(
        base_definition, nodes={**base_definition.nodes, node_key: updated_node}
    )
    saved = save_workflow_draft(job_db, workspace_id, definition_to_yaml(updated_definition))
    return {
        "node_key": node_key,
        "is_default": not prompt.strip(),
        "definition_yaml": saved["definition_yaml"],
        "updated_at": saved.get("updated_at"),
    }
