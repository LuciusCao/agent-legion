"""Unit tests for the agent status registry module (issue #2 phase 2 split)."""

from __future__ import annotations

import threading

from server.app.events.agent_registry import AgentRegistry, AgentStatus


def _registry(**kwargs) -> AgentRegistry:
    return AgentRegistry(threading.Lock(), **kwargs)


def test_discover_replaces_rows_with_injected_records():
    registry = _registry(
        discover_agents=lambda: [{"id": "a1", "identityName": "Agent One"}, {"id": "a2"}]
    )

    agents = registry.discover()

    assert agents == [
        AgentStatus(id="a1", name="Agent One", busy=False),
        AgentStatus(id="a2", name="a2", busy=False),
    ]
    assert registry.get_all() == agents


def test_discover_failure_clears_rows_and_returns_empty():
    def _boom():
        raise RuntimeError("openclaw down")

    registry = _registry(discover_agents=_boom)
    registry.agents = [AgentStatus(id="stale", name="Stale", busy=False)]

    assert registry.discover() == []
    assert registry.get_all() == []


def test_ensure_workspace_agent_upserts_and_reports_changes():
    registry = _registry()

    assert registry.ensure_workspace_agent("w1", "ws-1", max_tasks=2, name="Worker") is True
    assert registry.ensure_workspace_agent("w1", "ws-1", max_tasks=2, name="Worker") is False
    assert registry.ensure_workspace_agent("w1", "ws-1", max_tasks=5) is True

    assert registry.to_dicts() == [
        {
            "id": "w1",
            "name": "Worker",
            "busy": False,
            "task_count": 0,
            "max_tasks": 5,
            "workspace_id": "ws-1",
        }
    ]


def test_ensure_workspace_agent_scopes_rows_by_workspace():
    registry = _registry()

    registry.ensure_workspace_agent("w1", "ws-1", max_tasks=2, name="Worker")
    registry.ensure_workspace_agent("w1", "ws-2", max_tasks=3, name="Worker")

    assert [row["workspace_id"] for row in registry.to_dicts()] == ["ws-1", "ws-2"]


def test_find_scopes_by_workspace():
    registry = _registry()
    registry.ensure_workspace_agent("w1", "ws-1", max_tasks=2, name="Worker")

    assert registry.find("w1", "ws-1") is not None
    assert registry.find("w1", "ws-2") is None
    assert registry.find("missing", "ws-1") is None
