"""Studio-agent MCP server (stdio) for external agents.

A thin MCP wrapper over the platform's studio-agent tool surface
(``/api/studio-agent/tools/*``): any MCP-capable agent (Kimi Code, Claude
Code, …) connects over stdio and authors workflow/agent drafts with a
user-minted scoped token. Draft semantics are end-to-end: the tool surface
only validates, diffs, and saves drafts — publishing stays a human action in
Studio (STUDIO-AGENT-001).

Run with: ``uv run python -m server.app.mcp_server``
"""
