"""Contracts for the workflow node prompt preview and draft-prompt editing.

Shared by the human-facing Studio route
(``/api/workspaces/{id}/workflow/node-prompt-preview``) and the studio-agent
tool surface (``/api/studio-agent/tools/workspaces/{id}/node-prompt``): both
answer the same shape so the MCP tools and the Studio inspector stay in sync.
"""

from typing import Literal

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
    # #513：仅平台信封半区（不含节点指令段）——Studio 的「平台提示词」
    # 面板显示它，节点指令半区由面板的编辑区单独呈现。
    platform_prompt: str = ""
    # The auto-assembled default instructions for this node (always computed,
    # even when a custom prompt overrides them).
    default_instructions: str
    # The node's execution.prompt verbatim; empty means the default applies.
    custom_instructions: str
    is_default: bool
    # #513：自定义提示词拼接模式（append=默认指令+自定义，overwrite=
    # 仅自定义）；空串在响应里归一为 append。
    prompt_mode: str = "append"
    # Skill of the published Agent bound to the node's capability, if any.
    skill_key: str | None = None


class NodePromptSaveRequest(BaseModel):
    node_key: str
    # Written to the workspace draft YAML at nodes.<key>.execution.prompt;
    # an empty string clears the custom prompt (back to the auto default).
    prompt: str
    # #513：拼接模式（append/overwrite）；None = 保留现值。Literal 收紧
    # 写入口（codex P2 on #527）：任意字符串写进草稿会留下一份无法再次
    # 加载/发布的定义。
    prompt_mode: Literal["append", "overwrite"] | None = None


class NodePromptSaveResponse(BaseModel):
    node_key: str
    is_default: bool
    # #513：保存后的有效模式（空归一 append）。
    prompt_mode: str = "append"
    # The resulting workspace draft (same shape as the draft store).
    definition_yaml: str
    updated_at: str | None = None
