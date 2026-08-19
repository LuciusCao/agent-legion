"""Contracts for the studio-agent tool surface. Shared shapes are reused from
the sibling contract modules so the tool surface stays shape-compatible with
the Studio UI endpoints it mirrors; tool-only shapes live here.
"""

from typing import Literal

from pydantic import BaseModel

import server.app.routes.workflow_contracts as workflow_contracts
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
    """``expected_capability``: validated for existing nodes (mismatch -> 400);
    its presence authorizes a skeleton draft for a not-yet-published node."""

    expected_capability: str | None = None
