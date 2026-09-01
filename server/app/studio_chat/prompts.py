"""Built-in prompt bootstrap and MCP tool-name knowledge for Studio chat.

The bootstrap text is prepended to the first user prompt of every chat
session: it pins the agent's role (workflow authoring assistant), the tool
boundary (only the agent-legion MCP server may touch the platform), and the
draft-first workflow (validate before handing anything to the human). The
text lives in the sibling ``authoring_bootstrap.md`` resource (file budget:
long prompt text no longer counts against this module's line ceiling), still
versioned with the repo so the guidance evolves with the code, not with each
agent's local config.
"""

from __future__ import annotations

from pathlib import Path

STUDIO_AUTHORING_BOOTSTRAP = (
    Path(__file__).with_name("authoring_bootstrap.md").read_text(encoding="utf-8")
)

# Tool names exposed by server.app.mcp_server — used both to recognize
# agent-legion MCP tool calls in session/update traffic (permission
# auto-approve + the mcp_status smoke signal) and nowhere else; keep in sync
# with create_mcp_server.
AGENT_LEGION_MCP_TOOL_NAMES = frozenset(
    {
        "get_authoring_guide",
        "get_studio_context",
        "get_active_workflow",
        "validate_workflow",
        "compare_workflow",
        "save_node_code_draft",
        "get_node_code",
        "save_agent_definition_draft",
        "get_node_prompt",
        "save_node_prompt",
        "get_skill",
        "validate_skill",
        "save_skill_version",
    }
)

# The MCP server name passed in session/new; agents typically prefix tool
# calls with it (e.g. "agent-legion-studio__list_workflows").
AGENT_LEGION_MCP_SERVER_NAME = "agent-legion-studio"


def looks_like_agent_legion_tool_call(text: str) -> bool:
    """Heuristic: whether a tool-call identity field references our MCP tools.

    ACP gives the client no direct view into the agent's MCP wiring, so MCP
    visibility and permission auto-approve both key off the tool-call text the
    agent streams in session/update notifications. Callers must pass only
    structured identity fields (title/kind/name) — never a serialization of
    the whole payload, whose rawInput would let an agent's local command text
    (e.g. a Bash line mentioning a tool name) impersonate an MCP call.
    Matching is deliberately conservative (server name or an exact tool-name
    token) — a false negative only degrades to the safe path (human-confirmed
    permission, one-time mcp_status advisory).
    """
    lowered = text.lower()
    if AGENT_LEGION_MCP_SERVER_NAME in lowered:
        return True
    for name in AGENT_LEGION_MCP_TOOL_NAMES:
        if f" {name} " in f" {lowered} " or f"{name}(" in lowered or f"__{name}" in lowered:
            return True
    return False
