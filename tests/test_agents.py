import json
import subprocess

from server.app.agents import AgentStatus, AgentStatusManager


def test_discover_parses_openclaw_agents(monkeypatch):
    def fake_run(command, capture_output, text, timeout):
        assert command == ["openclaw", "agents", "list", "--json"]
        assert capture_output is True
        assert text is True
        assert timeout == 10
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                [
                    {"id": "main", "identityName": "Main Agent"},
                    {"id": "fallback"},
                    {"identityName": "missing id"},
                    "invalid",
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    manager = AgentStatusManager()

    agents = manager.discover()

    assert agents == [
        AgentStatus(id="main", name="Main Agent", busy=False),
        AgentStatus(id="fallback", name="fallback", busy=False),
    ]
    assert manager.to_dicts() == [
        {
            "id": "main",
            "name": "Main Agent",
            "busy": False,
            "current_video_id": None,
            "current_title": "",
            "current_content_type": "",
            "current_external_id": "",
            "current_phase": "",
        },
        {
            "id": "fallback",
            "name": "fallback",
            "busy": False,
            "current_video_id": None,
            "current_title": "",
            "current_content_type": "",
            "current_external_id": "",
            "current_phase": "",
        },
    ]


def test_discover_clears_stale_agents_when_openclaw_fails(monkeypatch):
    def fake_run(command, capture_output, text, timeout):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="openclaw unavailable")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manager = AgentStatusManager()
    manager.agents = [AgentStatus(id="stale", name="Stale", busy=False)]

    assert manager.discover() == []
    assert manager.get_all() == []


def test_discover_clears_stale_agents_when_json_is_invalid(monkeypatch):
    def fake_run(command, capture_output, text, timeout):
        return subprocess.CompletedProcess(command, 0, stdout="{bad json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manager = AgentStatusManager()
    manager.agents = [AgentStatus(id="stale", name="Stale", busy=False)]

    assert manager.discover() == []
    assert manager.get_all() == []


def test_set_busy_and_idle_updates_current_video_details():
    manager = AgentStatusManager()
    manager.agents = [AgentStatus(id="main", name="Main", busy=False)]

    manager.set_busy(
        "main",
        {
            "id": "knowledge_K001",
            "title": "奇函数",
            "content_type": "knowledge",
            "external_id": "K001",
            "current_phase": "transcribe",
        },
    )

    assert manager.to_dicts()[0] == {
        "id": "main",
        "name": "Main",
        "busy": True,
        "current_video_id": "knowledge_K001",
        "current_title": "奇函数",
        "current_content_type": "knowledge",
        "current_external_id": "K001",
        "current_phase": "transcribe",
    }

    manager.set_idle("main")

    assert manager.to_dicts()[0] == {
        "id": "main",
        "name": "Main",
        "busy": False,
        "current_video_id": None,
        "current_title": "",
        "current_content_type": "",
        "current_external_id": "",
        "current_phase": "",
    }


def test_set_busy_accepts_string_video_id():
    manager = AgentStatusManager()
    manager.agents = [AgentStatus(id="main", name="Main", busy=False)]

    manager.set_busy("main", "abc")

    assert manager.to_dicts() == [
        {
            "id": "main",
            "name": "Main",
            "busy": True,
            "current_video_id": "abc",
            "current_title": "",
            "current_content_type": "",
            "current_external_id": "",
            "current_phase": "",
        }
    ]


def test_set_idle_clears_busy_video_for_synthetic_runner_id():
    manager = AgentStatusManager()

    manager.set_busy("runner-0", "knowledge_K001")
    assert manager.is_video_busy("knowledge_K001") is True

    manager.set_idle("runner-0")

    assert manager.is_video_busy("knowledge_K001") is False
