"""Capability snapshot extraction for ACP session initialize responses."""

from __future__ import annotations

from typing import Any


def capability_snapshot(initialize: Any) -> dict[str, Any]:
    """Freeze the negotiated agent capabilities (decision: snapshot at
    initialize; later logic trims behavior by this table). Built field by
    field — a wholesale model_dump of AgentCapabilities trips pydantic
    serializer warnings on the auth sub-model."""
    capabilities = initialize.agent_capabilities
    snapshot: dict[str, Any] = {}
    if capabilities is not None:
        snapshot = {
            "loadSession": bool(capabilities.load_session),
            "mcpCapabilities": (
                capabilities.mcp_capabilities.model_dump(exclude_none=True)
                if capabilities.mcp_capabilities
                else {}
            ),
            "promptCapabilities": (
                capabilities.prompt_capabilities.model_dump(exclude_none=True)
                if capabilities.prompt_capabilities
                else {}
            ),
            "sessionCapabilities": (
                capabilities.session_capabilities.model_dump(exclude_none=True)
                if capabilities.session_capabilities
                else {}
            ),
        }
    agent_info = initialize.agent_info
    if agent_info is not None:
        snapshot["agentInfo"] = {
            "name": agent_info.name,
            "title": agent_info.title,
            "version": agent_info.version,
        }
    return snapshot
