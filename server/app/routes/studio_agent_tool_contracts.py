"""Request contracts for the studio-agent tool surface.

Response models are reused from the sibling contract modules
(``workflow_revisions_contracts``, ``workflow_draft_compare_contracts``,
``workflow_node_code_contracts``, ``agent_definition_contracts``,
``workflow_contracts``) so the tool surface stays shape-compatible with the
Studio UI endpoints it mirrors. Request models that exist there
(``WorkflowDraftRequest``, ``AgentDefinitionPayload``) are reused for the same
reason; tool-only shapes (the empty-tolerant active-workflow read, the
``expected_capability`` draft extension) live here.
"""

from typing import Literal

from pydantic import BaseModel

import server.app.routes.workflow_contracts as workflow_contracts
from server.app.routes.workflow_node_code_contracts import WorkflowNodeCodeDraftRequest
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


class StudioAgentNodeCodeDraftRequest(WorkflowNodeCodeDraftRequest):
    """Node-code draft write with an optional capability declaration.

    ``expected_capability`` lets the agent declare the capability it believes
    the node binds: for a node present in the active revision it is validated
    (mismatch is a clear 400); for a node that does not exist yet (no active
    revision, or a new node key) its presence authorizes creating a skeleton
    draft ahead of the workflow draft that will introduce the node."""

    expected_capability: str | None = None
