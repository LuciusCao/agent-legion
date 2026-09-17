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

from server.app.mcp_server.tool_names import AGENT_LEGION_MCP_TOOL_NAMES

STUDIO_AUTHORING_BOOTSTRAP = (
    Path(__file__).with_name("authoring_bootstrap.md").read_text(encoding="utf-8")
)

# Tool names exposed by server.app.mcp_server — used both to recognize
# agent-legion MCP tool calls in session/update traffic (permission
# auto-approve + the mcp_status smoke signal). #678: a hand-copied list
# here drifted 12 tools behind create_mcp_server's registrations; the
# manifest now lives on the MCP-server side (tool_names.py) and is
# imported — three-way equality (registered == manifest == this
# reference) is pinned by tests/mcp_server/test_tool_names.py.

# The MCP server name passed in session/new; agents prefix tool calls with
# it (e.g. "agent-legion-studio__list_jobs"). The mcp-prefixed variant
# ("mcp__agent-legion-studio__list_jobs", how e.g. Claude Code surfaces MCP
# tools) is accepted too. #678 review follow-up: the identity matcher only
# accepts these structured prefixes followed by a real manifest tool name —
# never a tool-name token embedded in a longer title.
AGENT_LEGION_MCP_SERVER_NAME = "agent-legion-studio"
_AGENT_LEGION_MCP_TITLE_PREFIXES = (
    f"{AGENT_LEGION_MCP_SERVER_NAME}__",
    f"mcp__{AGENT_LEGION_MCP_SERVER_NAME}__",
)


def agent_legion_tool_name(text: str) -> str | None:
    """The manifest tool name a tool-call identity field carries, else None.

    Same matching as :func:`looks_like_agent_legion_tool_call` (exact equality
    after stripping one documented server prefix), but returns the matched
    name so the caller can bind the decision to that specific tool — the
    permission auto-approve validates rawInput against the matched tool's
    input schema instead of trusting the title alone (#687 attack fix).
    """
    lowered = text.lower()
    for prefix in _AGENT_LEGION_MCP_TITLE_PREFIXES:
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix) :]
            break
    return lowered if lowered in AGENT_LEGION_MCP_TOOL_NAMES else None


def looks_like_agent_legion_tool_call(text: str) -> bool:
    """Whether a tool-call identity field is exactly one of our MCP tool names.

    ACP gives the client no direct view into the agent's MCP wiring, so MCP
    visibility and permission auto-approve both key off the tool-call text the
    agent streams in session/update notifications. Callers must pass only
    structured identity fields (title/kind/name) — never a serialization of
    the whole payload, whose rawInput would let an agent's local command text
    (e.g. a Bash line mentioning a tool name) impersonate an MCP call.
    #678 review follow-up: a field counts only when it *equals* a manifest
    tool name, after stripping one of the documented server prefixes. A
    token inside a longer title ("Bash: list_jobs && rm -rf /") is not
    evidence of an MCP call — matching is exact, so growing the manifest can
    never widen the auto-approve surface to command text. A false negative
    only degrades to the safe path (human-confirmed permission, one-time
    mcp_status advisory).
    """
    return agent_legion_tool_name(text) is not None
