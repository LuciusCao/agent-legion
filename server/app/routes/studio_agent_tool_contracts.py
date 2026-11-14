"""Contracts for the studio-agent tool surface. Shared shapes are reused from
the sibling contract modules so the tool surface stays shape-compatible with
the Studio UI endpoints it mirrors; tool-only shapes live here.
"""

from typing import Literal

from pydantic import BaseModel, Field

import server.app.routes.workflow_contracts as workflow_contracts
from server.app.routes.agent_definition_contracts import AgentVersionResponse
from server.app.routes.workflow_node_code_contracts import WorkflowNodeCodeDraftRequest
from server.app.routes.workflow_revisions_contracts import WorkflowRevisionSummary


class StudioAgentActiveWorkflowResponse(BaseModel):
    """``state="empty"`` (not 404) when no default key or no published revision."""

    state: Literal["active", "empty"]
    workflow_key: str | None = None
    revision: WorkflowRevisionSummary | None = None
    workflow: workflow_contracts.WorkflowDefinitionResponse | None = None
    definition_yaml: str | None = None


class StudioAgentNodeCodeDraftRequest(WorkflowNodeCodeDraftRequest):
    """``expected_capability`` validates an existing node (mismatch -> 400).
    Any non-null value, including ``''`` for a capless code node, authorizes
    a skeleton draft; an omitted or null value does not."""

    expected_capability: str | None = Field(default=None)


class StudioAgentAgentVersionsResponse(BaseModel):
    """Latest version per Agent with the FULL definition payload (#633) —
    the human list route carries only a summary, but the authoring agent's
    read loop needs every field (capability, runtime, skill, tools,
    requires_labels, config_schema) plus version metadata."""

    versions: list[AgentVersionResponse]
