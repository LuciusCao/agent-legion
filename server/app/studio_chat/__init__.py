"""Studio chat backend (phase 3 chunk 4): ACP client conversation surface.

The backend acts as an ACP (Agent Client Protocol) client: each Studio chat
session owns one ACP agent subprocess (stdio JSON-RPC) registered by an admin
in the instance-level agent registry; the agent reaches the platform through
the bundled MCP server (``server.app.mcp_server``) authenticated with a
per-session scoped token (STUDIO-AGENT-001). Sessions are in-process only
(v1): they do not survive a backend restart.
"""
