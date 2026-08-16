"""Built-in prompt bootstrap and MCP tool-name knowledge for Studio chat.

The bootstrap text is prepended to the first user prompt of every chat
session: it pins the agent's role (workflow authoring assistant), the tool
boundary (only the agent-legion MCP server may touch the platform), and the
draft-first workflow (validate before handing anything to the human). It is a
backend constant so the guidance evolves with the repo, not with each agent's
local config.
"""

from __future__ import annotations

STUDIO_AUTHORING_BOOTSTRAP = """\
[Agent Legion Studio authoring session]
You are an assistant embedded in Agent Legion Studio helping a human author
and refine workflows. Rules for this session:
1. Operate on the platform ONLY through the tools of the "agent-legion-studio"
   MCP server (get_studio_context, list_workflows, get_active_workflow,
   validate_workflow, compare_workflow, save_node_code_draft, get_node_code,
   save_agent_definition_draft, register_workflow). Never invent platform
   state you have not read through those tools.
2. When you need workspace or selection context (which workspace this is, its
   workflow structure, the node the human has selected), call
   get_studio_context — it reads the live session binding; never guess.
3. Produce drafts only: workflow YAML drafts, node code drafts, and agent
   definition drafts. Nothing you do takes effect in production — a human
   reviews and publishes every change in Studio.
4. Always validate_workflow a workflow draft (and compare_workflow it against
   the active revision) before presenting it as ready.
5. Keep answers concise; show the human the draft content and the validation
   result, and explain what changed and why.

User request:
"""

# Tool names exposed by server.app.mcp_server — used both to recognize
# agent-legion MCP tool calls in session/update traffic (permission
# auto-approve + the mcp_status smoke signal) and nowhere else; keep in sync
# with create_mcp_server.
AGENT_LEGION_MCP_TOOL_NAMES = frozenset(
    {
        "get_studio_context",
        "list_workflows",
        "get_active_workflow",
        "validate_workflow",
        "compare_workflow",
        "save_node_code_draft",
        "get_node_code",
        "save_agent_definition_draft",
        "register_workflow",
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
    permission, mcp_status warning).
    """
    lowered = text.lower()
    if AGENT_LEGION_MCP_SERVER_NAME in lowered:
        return True
    for name in AGENT_LEGION_MCP_TOOL_NAMES:
        if f" {name} " in f" {lowered} " or f"{name}(" in lowered or f"__{name}" in lowered:
            return True
    return False
