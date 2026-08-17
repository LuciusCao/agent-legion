"""Settings payload enrichment: node config schemas plus secret masking."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.services.job_errors import NotFoundError
from server.app.services.node_config import workflow_node_config_schemas
from server.app.services.node_secrets import mask_node_config_secrets
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workspace_settings_payload import workspace_settings_payload


def workspace_settings_payload_with_schemas(
    workflows: WorkflowCatalogService,
    agent_definitions: Mapping[str, AgentDefinition],
    workspace: dict[str, Any],
) -> dict[str, Any]:
    """Settings payload plus the node config schemas for the current workflow."""
    payload = workspace_settings_payload(workspace)
    schemas: dict[str, dict[str, Any]] = {}
    workflow_key = str(payload.get("workflowKey") or "")
    if workflow_key:
        try:
            definition = workflows.definition(workflow_key)
        except NotFoundError:
            definition = None
        if definition is not None:
            schemas = workflow_node_config_schemas(definition, agent_definitions)
    payload["nodeConfigSchemas"] = schemas
    node_config = payload.get("nodeConfig")
    payload["nodeConfig"] = mask_node_config_secrets(
        node_config if isinstance(node_config, dict) else {}, schemas
    )
    return payload
