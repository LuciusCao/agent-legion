"""Authoritative manifest of studio-agent MCP tool names (issue #678).

The tool surface is registered in ``server.create_mcp_server`` — five
inline tools plus eight ``register_*_tools`` sibling modules — and Studio
chat consumes the names to recognize agent-legion tool calls (permission
auto-approve + the mcp_status smoke signal, ``studio_chat/prompts.py``).
A hand-copied list in the consumer drifted 12 tools behind the
registrations (#678) and silently degraded those tools to
human-confirmed permission. This module is the single authority instead:
adding, renaming, or removing a tool means editing this set in the same
change — ``tests/mcp_server/test_tool_names.py`` pins the equality
(FastMCP-registered names == this constant == the prompts-side
reference), so drift fails CI instead of shipping. Comments are free
under the effective-line budget, so the per-group annotations below stay
generous on purpose.
"""

from __future__ import annotations

# Grouped by registering site, in create_mcp_server's registration order:
# the server.py inline tools first, then each register_* sibling module.
AGENT_LEGION_MCP_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # server.py inline: the local authoring playbook, the session-bound
        # studio context, the code-node draft pair, and the agent-definition
        # draft save.
        "get_authoring_guide",
        "get_studio_context",
        "save_node_code_draft",
        "get_node_code",
        "save_agent_definition_draft",
        # skill_tools.py: skill read / contract validation / local-repo
        # save-version / workspace skill-dir creation (#217, #633).
        "get_skill",
        "validate_skill",
        "save_skill_version",
        "create_skill",
        # shared_tools.py: workspace shared materials read / save / the
        # cross-row propagate sync (#633, #674).
        "get_shared_materials",
        "save_shared_materials",
        "sync_shared_materials",
        # prompt_tools.py: node prompt read / save on the workflow draft.
        "get_node_prompt",
        "save_node_prompt",
        # workflow_tools.py: active read / validate / compare / the
        # publish-request pair (#416).
        "get_active_workflow",
        "validate_workflow",
        "compare_workflow",
        "request_workflow_publish",
        "get_publish_request_status",
        # draft_tools.py: canvas workflow draft CAS get / save (#633).
        "get_workflow_draft",
        "save_workflow_draft",
        # preview_tools.py: local preview guide, context / panel reads, and
        # the panel draft save (#328).
        "get_preview_guide",
        "get_preview_context",
        "get_preview_panel",
        "save_preview_panel_draft",
        # agent_tools.py: agent-definition catalog visibility (#633).
        "get_agent_definitions",
        "create_agent_definition",
        "get_runtime_models",
        "get_agent_runtimes",
        # job_tools.py: read-only job observation surface (#329).
        # get_job_context is conditionally registered (session-bound only,
        # #660); the contract test tolerates its absence for static configs
        # while everything else must register unconditionally.
        "get_job_context",
        "get_job_detail",
        "get_node_logs",
        "read_artifact",
        "list_jobs",
        "compare_jobs",
    }
)
