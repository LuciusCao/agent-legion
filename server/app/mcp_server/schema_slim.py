"""inputSchema listing de-fatting for the studio-agent MCP server (#660).

FastMCP generates per-parameter ``title`` keys and explicit ``"default":
null`` entries into every tool's JSON Schema — pure noise for the MCP host's
model (several KB of the tools/list payload). ``slim_tool_parameters``
rewrites the registered tools' ``parameters`` dicts in place at server build
time. This is display-only: tools/call argument validation runs on
``fn_metadata``'s pydantic model, never on the parameters dict, so the
cleanup cannot change call semantics. ``anyOf`` structures are left alone —
only whole ``title`` keys and null-valued ``default`` keys are dropped.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP


def _slim(node: dict[str, Any]) -> None:
    node.pop("title", None)
    if "default" in node and node["default"] is None:
        del node["default"]
    for value in node.values():
        if isinstance(value, dict):
            _slim(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _slim(item)


def slim_tool_parameters(mcp: FastMCP) -> None:
    for tool in mcp._tool_manager.list_tools():  # pinned mcp==1.29 internals
        _slim(tool.parameters)
