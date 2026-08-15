"""Agent availability probe: PATH-level check, TTL cache, service wiring."""

from __future__ import annotations

import sys

import pytest

from server.app.services.job_errors import InvalidOperationError
from server.app.studio_chat.availability import AgentAvailabilityProbe
from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.service import StudioChatService
from tests.postgres_support import TEST_DATABASE_URL


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_probe_uses_which_and_caches_within_ttl() -> None:
    clock = _FakeClock()
    calls: list[str] = []

    def fake_which(command: str) -> str | None:
        calls.append(command)
        return "/usr/bin/present" if command == "present" else None

    probe = AgentAvailabilityProbe(60.0, which=fake_which, clock=clock)
    assert probe.available("present") is True
    assert probe.available("missing") is False
    # Cached: no further which calls inside the TTL window.
    assert probe.available("present") is True
    assert probe.available("missing") is False
    assert calls == ["present", "missing"]
    # Past the TTL the probe re-checks.
    clock.now = 61.0
    assert probe.available("missing") is False
    assert calls == ["present", "missing", "missing"]


def test_probe_all_warms_the_cache() -> None:
    clock = _FakeClock()
    calls: list[str] = []
    probe = AgentAvailabilityProbe(
        60.0, which=lambda cmd: calls.append(cmd) or "/bin/x", clock=clock
    )
    assert probe.probe_all(["a", "b"]) == {"a": True, "b": True}
    probe.available("a")
    assert calls == ["a", "b"]


@pytest.fixture
def chat(job_db, settings):
    service = StudioChatService(job_db, settings, None)
    workspace_id = job_db.create_workspace(
        default_workflow_key="question_comprehension_info", name="Probe WS"
    )["id"]
    user_id = str(job_db.create_user("probe-user", password_hash=None)["id"])
    yield service, workspace_id, user_id
    service.shutdown()


def _register(command: str, agent_id: str = "probe-agent") -> None:
    StudioAgentRegistryStore(TEST_DATABASE_URL).put(
        {
            "api_base": "http://127.0.0.1:8000",
            "agents": [{"id": agent_id, "label": "Probe", "command": command, "args": []}],
        }
    )


def test_picker_hides_agents_missing_from_path(chat) -> None:
    service, _workspace_id, _user_id = chat
    _register("/nonexistent/acp-agent-binary")
    assert service.list_available_agents() == []
    _register(sys.executable)
    assert service.list_available_agents() == [{"id": "probe-agent", "label": "Probe"}]


def test_create_session_with_unavailable_agent_fails_before_spawn(chat) -> None:
    service, workspace_id, user_id = chat
    _register("/nonexistent/acp-agent-binary")
    with pytest.raises(InvalidOperationError, match="not available on this host"):
        service.create_session(workspace_id, user_id, "probe-agent")
    # The rejection happens before any session row or token is created.
    assert service.list_sessions(workspace_id) == []
