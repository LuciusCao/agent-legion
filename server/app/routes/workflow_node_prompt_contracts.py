"""Contracts for the workflow node prompt preview and draft-prompt editing.

Shared by the human-facing Studio route
(``/api/workspaces/{id}/workflow/node-prompt-preview``) and the studio-agent
tool surface (``/api/studio-agent/tools/workspaces/{id}/node-prompt``): both
answer the same shape so the MCP tools and the Studio inspector stay in sync.
"""

from pydantic import BaseModel


class NodePromptPreviewRequest(BaseModel):
    node_key: str
    # Optional draft definition YAML; when absent the workspace's active
    # revision is the preview baseline.
    definition_yaml: str | None = None


class NodePromptPreviewResponse(BaseModel):
    # The full prompt exactly as render_command_spec would build it for this
    # node (path placeholders {job_dir}/{skill_dir}, job id placeholder).
    effective_prompt: str
    # The auto-assembled default instructions for this node (always computed,
    # even when a custom prompt overrides them).
    default_instructions: str
    # The node's execution.prompt verbatim; empty means the default applies.
    custom_instructions: str
    is_default: bool
    # Skill of the published Agent bound to the node's capability, if any.
    skill_key: str | None = None


class NodePromptSaveRequest(BaseModel):
    node_key: str
    # Written to the workspace draft YAML at nodes.<key>.execution.prompt;
    # an empty string clears the custom prompt (back to the auto default).
    prompt: str


class NodePromptSaveResponse(BaseModel):
    node_key: str
    is_default: bool
    # The resulting workspace draft (same shape as the draft store).
    definition_yaml: str
    updated_at: str | None = None
