"""Request contracts for the studio-agent tool surface.

Response models are reused from the sibling contract modules
(``workflow_revisions_contracts``, ``workflow_draft_compare_contracts``,
``workflow_node_code_contracts``, ``agent_definition_contracts``,
``workflow_contracts``) so the tool surface stays shape-compatible with the
Studio UI endpoints it mirrors. Request models that exist there
(``WorkflowDraftRequest``, ``WorkflowNodeCodeDraftRequest``,
``AgentDefinitionPayload``) are reused for the same reason.
"""

from pydantic import BaseModel


class StudioAgentWorkflowRegisterRequest(BaseModel):
    key: str
    label: str
    description: str = ""
