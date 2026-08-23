"""Studio-agent MCP server for external agents and Studio chat sessions.

A thin MCP wrapper over the platform's studio-agent tool surface
(``/api/studio-agent/tools/*``): MCP-capable agents (Kimi Code, Claude
Code, …) author workflow/agent drafts with a scoped token. Two transports
share one tool registration: stdio (``uv run python -m
server.app.mcp_server``, self-service external agents) and the in-app
streamable-HTTP endpoint (``http_app.py``, mounted at
``/api/studio-agent/mcp``) that Studio chat sessions use — kimi ≥ 0.38 no
longer accepts stdio MCP servers in ACP ``session/new``. Draft semantics are
end-to-end: the tool surface only validates, diffs, and saves drafts —
publishing stays a human action in Studio (STUDIO-AGENT-001).
"""
