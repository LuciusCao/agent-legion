"""Registration and wire contracts for tools split out of the MCP composition root."""

from __future__ import annotations

import asyncio
import inspect

import pytest

from server.app.mcp_server.agent_definition_tools import AGENT_DEFINITION_RETIRED
from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.server import create_mcp_server
from server.app.mcp_server.tool_client import ToolClient

pytestmark = pytest.mark.no_db

_CONFIG = McpServerConfig(
    api_base="http://backend.test:9000", token="scoped-token-1", session_id="sess-1"
)

_REGISTRATION_ORDER = [
    "get_authoring_guide",
    "get_studio_context",
    "save_node_code_draft",
    "get_node_code",
    "save_agent_definition_draft",
    "get_skill",
    "validate_skill",
    "save_skill_version",
    "create_skill",
    "get_shared_materials",
    "save_shared_materials",
    "sync_shared_materials",
    "get_node_prompt",
    "save_node_prompt",
    "get_active_workflow",
    "validate_workflow",
    "compare_workflow",
    "request_workflow_publish",
    "get_publish_request_status",
    "get_workflow_draft",
    "save_workflow_draft",
    "get_preview_guide",
    "get_preview_context",
    "get_preview_panel",
    "save_preview_panel_draft",
    "get_agent_definitions",
    "create_agent_definition",
    "get_runtime_models",
    "get_agent_runtimes",
    "get_job_context",
    "get_job_detail",
    "get_node_logs",
    "read_artifact",
    "list_jobs",
    "compare_jobs",
]

_TOOL_CONTRACTS = {
    "get_authoring_guide": {
        "async": False,
        "description": """The built-in workflow authoring playbook. No section → FULL text;
pass one chapter key: tool-map, flow, yaml, capabilities, agents,
skills, errors. Read BEFORE authoring from scratch. Served locally —
no backend call.""",
        "inputSchema": {
            "properties": {"section": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            "type": "object",
        },
    },
    "get_studio_context": {
        "async": True,
        "description": """Current Studio session context: bound workspace, its active
workflow structure, the human's selected node, and the canvas'
unpublished draft YAML (live every call). No workspace_id — the
session binding decides. Call first for context.""",
        "inputSchema": {"properties": {}, "type": "object"},
    },
    "save_node_code_draft": {
        "async": True,
        "description": """Save a draft of a code node's Python source (module-level run
function required; get_authoring_guide §4). Draft only — a human
publishes in Studio. expected_capability declares the capability you
believe the node binds: mismatch with an existing node is rejected; a
node absent from any published revision is accepted only WITH it
(without it → 404). Supply exactly one of code or code_path. code_path
reads UTF-8 bytes from data/studio-mcp-files/<workspace_id>/ on the
MCP host (relative to that directory, or absolute within it).
The response carries the saved draft's code_hash.""",
        "inputSchema": {
            "properties": {
                "workspace_id": {"type": "string"},
                "node_key": {"type": "string"},
                "code": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "change_note": {"default": "", "type": "string"},
                "expected_capability": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "code_path": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["workspace_id", "node_key"],
            "type": "object",
        },
    },
    "save_agent_definition_draft": {
        "async": True,
        "description": """DEPRECATED — writes nothing. Agent execution profiles now live on
the workflow agent node (execution.runtime, tools, requires_labels,
config_schema, skill); edit them with save_workflow_draft.""",
        "inputSchema": {
            "properties": {
                "workspace_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "capability": {"type": "string"},
                "runtime": {"type": "string"},
                "skill": {"type": "string"},
                "tools": {
                    "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}]
                },
                "requires_labels": {
                    "anyOf": [
                        {"additionalProperties": {"type": "string"}, "type": "object"},
                        {"type": "null"},
                    ]
                },
                "config_schema": {
                    "anyOf": [{"additionalProperties": True, "type": "object"}, {"type": "null"}]
                },
            },
            "required": ["workspace_id", "agent_id", "capability", "runtime", "skill"],
            "type": "object",
        },
    },
}


def _call_tool(server, name: str, args: dict) -> str:
    blocks = asyncio.run(server.call_tool(name, args))
    return "".join(block.text for block in blocks if block.type == "text")


def test_split_modules_preserve_full_registration_order() -> None:
    server = create_mcp_server(_CONFIG)
    assert list(server._tool_manager._tools) == _REGISTRATION_ORDER


def test_split_tools_preserve_descriptions_schemas_and_async_boundaries() -> None:
    server = create_mcp_server(_CONFIG)
    registered = server._tool_manager._tools
    listed = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    for name, expected in _TOOL_CONTRACTS.items():
        assert inspect.iscoroutinefunction(registered[name].fn) is expected["async"]
        assert listed[name].description == expected["description"]
        assert listed[name].inputSchema == expected["inputSchema"]
        assert listed[name].outputSchema is None


def test_save_node_code_draft_forwards_capability_and_conflict_text(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    async def conflict(self, method: str, path: str, body: dict | None = None, *, timeout=None):
        del self, timeout
        calls.append((method, path, body))
        return 'HTTP 409: {"detail":"capability conflict"}'

    monkeypatch.setattr(ToolClient, "call", conflict)
    server = create_mcp_server(_CONFIG)

    result = _call_tool(
        server,
        "save_node_code_draft",
        {
            "workspace_id": "ws-1",
            "node_key": "render",
            "code": "def run(ctx): pass",
            "expected_capability": "render",
        },
    )

    assert result == 'HTTP 409: {"detail":"capability conflict"}'
    assert calls == [
        (
            "PUT",
            "/workspaces/ws-1/nodes/render/code/draft",
            {
                "code": "def run(ctx): pass",
                "change_note": None,
                "expected_capability": "render",
            },
        )
    ]


def test_agent_definition_write_tools_keep_identical_retired_response(monkeypatch) -> None:
    async def unexpected_call(*args, **kwargs):
        raise AssertionError("deprecated Agent-definition writes must not call the backend")

    monkeypatch.setattr(ToolClient, "call", unexpected_call)
    server = create_mcp_server(_CONFIG)
    common = {
        "workspace_id": "ws-1",
        "capability": "occupied_capability",
        "runtime": "velites",
        "skill": "group/skill",
    }

    save = _call_tool(server, "save_agent_definition_draft", {**common, "agent_id": "agent-1"})
    create = _call_tool(server, "create_agent_definition", common)

    assert save == AGENT_DEFINITION_RETIRED
    assert create == AGENT_DEFINITION_RETIRED
