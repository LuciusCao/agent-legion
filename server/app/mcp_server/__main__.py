"""Entry point: ``uv run python -m server.app.mcp_server`` (stdio MCP server)."""

from server.app.mcp_server.server import main

if __name__ == "__main__":
    main()
