"""Protocol-level test for the studio-agent MCP server.

Spawns ``python -m server.app.mcp_server`` over stdio like a real MCP host
would, pointed at a local stub HTTP backend (no platform database involved):
handshake, tools/list discovers the 9 tools, and a tools/call round-trip
proves the scoped token reaches the backend and the response comes back as
text.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
_TOKEN = "proto-test-token"


class _StubHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for the /api/studio-agent/tools/* surface."""

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        if self.headers.get("authorization") != f"Bearer {_TOKEN}":
            self._reply(401, {"detail": "Not authenticated"})
            return
        if self.path == "/api/studio-agent/tools/workflows":
            self._reply(200, {"workflows": [{"key": "demo_workflow"}]})
            return
        if self.path == "/api/studio-agent/tools/chat-sessions/sess-1/context":
            self._reply(200, {"workspace_id": "ws-1", "selected_node_key": "node-a"})
            return
        if self.path.endswith("/workflow/validate"):
            self._reply(200, {"valid": True, "errors": []})
            return
        self._reply(404, {"detail": "unknown"})

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle

    def log_message(self, *args) -> None:  # keep test output clean
        del args


@pytest.fixture
def stub_backend():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)


def test_mcp_stdio_handshake_and_tool_call(stub_backend: str) -> None:
    async def run() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "server.app.mcp_server"],
            env={
                "AGENT_LEGION_MCP_API_BASE": stub_backend,
                "AGENT_LEGION_STUDIO_AGENT_TOKEN": _TOKEN,
                "AGENT_LEGION_MCP_SESSION_ID": "sess-1",
            },
            cwd=REPO_ROOT,
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(tool.name for tool in tools.tools)
            assert names == [
                "compare_workflow",
                "get_active_workflow",
                "get_node_code",
                "get_studio_context",
                "list_workflows",
                "register_workflow",
                "save_agent_definition_draft",
                "save_node_code_draft",
                "validate_workflow",
            ]

            async def call_text(name: str, args: dict) -> str:
                result = await session.call_tool(name, args)
                return "".join(c.text for c in result.content if c.type == "text")

            listed = json.loads(await call_text("list_workflows", {}))
            assert listed["workflows"][0]["key"] == "demo_workflow"

            validated = json.loads(
                await call_text(
                    "validate_workflow",
                    {"workspace_id": "ws-1", "definition_yaml": "key: x"},
                )
            )
            assert validated == {"valid": True, "errors": []}

            # Unknown stub path → non-2xx surfaces as text, session alive.
            missing = await call_text("get_active_workflow", {"workspace_id": "ws-x"})
            assert missing.startswith("HTTP 404: ")

            # The session-bound context tool resolves its session from env.
            context = json.loads(await call_text("get_studio_context", {}))
            assert context["workspace_id"] == "ws-1"
            assert context["selected_node_key"] == "node-a"

    asyncio.run(asyncio.wait_for(run(), timeout=60))
