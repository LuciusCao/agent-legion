"""Request contracts for the studio-agent tool surface.

Response models are reused from the sibling contract modules
(``workflow_revisions_contracts``, ``workflow_draft_compare_contracts``,
``workflow_node_code_contracts``, ``agent_definition_contracts``,
``workflow_contracts``) so the tool surface stays shape-compatible with the
Studio UI endpoints it mirrors. Request models that exist there
(``WorkflowDraftRequest``, ``WorkflowNodeCodeDraftRequest``,
``AgentDefinitionPayload``) are reused for the same reason; tool-only shapes
(the empty-tolerant active-workflow read) live here.
"""

from typing import Literal

from pydantic import BaseModel

import server.app.routes.workflow_contracts as workflow_contracts
from server.app.routes.workflow_revisions_contracts import WorkflowRevisionSummary


class StudioAgentWorkflowRegisterRequest(BaseModel):
    key: str
    label: str
    description: str = ""


class StudioAgentActiveWorkflowResponse(BaseModel):
    """Active-revision read with a structured empty state instead of a 404.

    ``state="empty"`` means the workspace exists but has no default workflow
    key or no published revision yet — the agent should switch to the
    from-scratch authoring flow rather than treat it as an error. Unknown
    workspaces still 404 (workspace existence is not leaked)."""

    state: Literal["active", "empty"]
    workflow_key: str | None = None
    revision: WorkflowRevisionSummary | None = None
    workflow: workflow_contracts.WorkflowDefinitionResponse | None = None
    definition_yaml: str | None = None
