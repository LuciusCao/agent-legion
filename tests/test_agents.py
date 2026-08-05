import json
import subprocess
import threading
from pathlib import Path

from server.app.events.agents import AgentStatus, AgentStatusManager
from server.app.executors.openclaw_runner import OpenClawRunner
from server.app.pipeline.runners import list_openclaw_agents


def _agent_dict(**kwargs):
    defaults = {
        "busy": False,
        "task_count": 0,
        "max_tasks": 1,
        "workspace_id": "",
        "current_video_id": None,
        "current_title": "",
        "current_content_type": "",
        "current_external_id": "",
        "current_phase": "",
    }
    defaults.update(kwargs)
    return defaults


def test_broadcast_publishes_to_agents_channel():
    from server.app.events.bus import InProcessEventBus

    bus = InProcessEventBus()
    manager = AgentStatusManager(event_bus=bus)
    queue = bus.subscribe("agents")
    manager._broadcast()

    assert json.loads(queue.get_nowait()) == {"type": "snapshot", "agents": []}


def test_broadcast_sends_incremental_agent_events():
    from server.app.events.bus import InProcessEventBus

    bus = InProcessEventBus()
    manager = AgentStatusManager(event_bus=bus)
    manager.agents = [AgentStatus(id="main", name="Main", busy=False)]
    queue = bus.subscribe("agents")

    manager.set_busy("main", "v1")
    manager._broadcast()
    assert json.loads(queue.get_nowait()) == {
        "type": "agent_busy",
        "agent": _agent_dict(
            id="main", name="Main", busy=True, task_count=1, current_video_id="v1"
        ),
    }

    manager.set_idle("main")
    manager._broadcast()
    assert json.loads(queue.get_nowait()) == {
        "type": "agent_idle",
        "agent": _agent_dict(id="main", name="Main"),
    }


def test_add_pi_agent_broadcasts_snapshot_envelope():
    from server.app.events.bus import InProcessEventBus

    bus = InProcessEventBus()
    manager = AgentStatusManager(event_bus=bus)
    queue = bus.subscribe("agents")

    manager.add_pi_agent_for_workspace("ws-1", max_tasks=2)

    assert json.loads(queue.get_nowait()) == {
        "type": "snapshot",
        "agents": [
            _agent_dict(id="pi", name="Pi Agent", max_tasks=2, workspace_id="ws-1"),
        ],
    }


def test_broadcast_controller_is_public():
    assert AgentStatusManager().broadcast_controller is not None


def test_discover_uses_injected_callable():
    from server.app.events.agents import AgentStatusManager

    manager = AgentStatusManager(
        discover_agents=lambda: [{"id": "agent_1", "identityName": "Worker One"}]
    )
    agents = manager.discover()
    assert [a.id for a in agents] == ["agent_1"]
    assert agents[0].name == "Worker One"
    assert agents[0].busy is False


def test_discover_without_injection_returns_empty():
    from server.app.events.agents import AgentStatusManager

    assert AgentStatusManager().discover() == []


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
    manager = AgentStatusManager(discover_agents=lambda: list_openclaw_agents(timeout=10))

    agents = manager.discover()

    assert agents == [
        AgentStatus(id="main", name="Main Agent", busy=False),
        AgentStatus(id="fallback", name="fallback", busy=False),
    ]
    assert manager.to_dicts() == [
        _agent_dict(id="main", name="Main Agent"),
        _agent_dict(id="fallback", name="fallback"),
    ]


def test_discover_clears_stale_agents_when_openclaw_fails(monkeypatch):
    def fake_run(command, capture_output, text, timeout):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="openclaw unavailable")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manager = AgentStatusManager(discover_agents=lambda: list_openclaw_agents(timeout=10))
    manager.agents = [AgentStatus(id="stale", name="Stale", busy=False)]

    assert manager.discover() == []
    assert manager.get_all() == []


def test_discover_clears_stale_agents_when_json_is_invalid(monkeypatch):
    def fake_run(command, capture_output, text, timeout):
        return subprocess.CompletedProcess(command, 0, stdout="{bad json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manager = AgentStatusManager(discover_agents=lambda: list_openclaw_agents(timeout=10))
    manager.agents = [AgentStatus(id="stale", name="Stale", busy=False)]

    assert manager.discover() == []
    assert manager.get_all() == []


def test_agent_status_manager_marks_broadcast_pending_once():
    manager = AgentStatusManager()
    manager.add_pi_agent_for_workspace("ws1", max_tasks=10)

    manager.set_busy("pi", {"id": "job1"}, workspace_id="ws1")
    manager.set_busy("pi", {"id": "job2"}, workspace_id="ws1")

    assert manager.has_pending_broadcast() is True


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

    assert manager.to_dicts()[0] == _agent_dict(
        id="main",
        name="Main",
        busy=True,
        task_count=1,
        current_video_id="knowledge_K001",
        current_title="奇函数",
        current_content_type="knowledge",
        current_external_id="K001",
        current_phase="transcribe",
    )

    manager.set_idle("main")

    assert manager.to_dicts()[0] == _agent_dict(id="main", name="Main")


def test_set_busy_accepts_string_video_id():
    manager = AgentStatusManager()
    manager.agents = [AgentStatus(id="main", name="Main", busy=False)]

    manager.set_busy("main", "abc")

    assert manager.to_dicts() == [
        _agent_dict(id="main", name="Main", busy=True, task_count=1, current_video_id="abc")
    ]


def test_set_idle_clears_busy_video_for_synthetic_runner_id():
    manager = AgentStatusManager()

    manager.set_busy("runner-0", "knowledge_K001")
    assert manager.is_video_busy("knowledge_K001") is True

    manager.set_idle("runner-0")

    assert manager.is_video_busy("knowledge_K001") is False


def test_concurrent_set_busy_increments_task_count():
    manager = AgentStatusManager()
    manager.agents = [AgentStatus(id="main", name="Main", busy=False, max_tasks=3)]

    manager.set_busy("main", "video_1")
    manager.set_busy("main", "video_2")
    manager.set_busy("main", "video_3")

    agent = manager.to_dicts()[0]
    assert agent["task_count"] == 3
    assert agent["busy"] is True
    assert agent["current_video_id"] == "video_3"


def test_concurrent_set_idle_decrements_task_count():
    manager = AgentStatusManager()
    manager.agents = [AgentStatus(id="main", name="Main", busy=False, max_tasks=3)]

    manager.set_busy("main", "video_1")
    manager.set_busy("main", "video_2")
    manager.set_busy("main", "video_3")

    manager.set_idle("main")
    agent = manager.to_dicts()[0]
    assert agent["task_count"] == 2
    assert agent["busy"] is True

    manager.set_idle("main")
    agent = manager.to_dicts()[0]
    assert agent["task_count"] == 1
    assert agent["busy"] is True

    manager.set_idle("main")
    agent = manager.to_dicts()[0]
    assert agent["task_count"] == 0
    assert agent["busy"] is False
    assert agent["current_video_id"] is None


def test_concurrent_set_idle_clears_all_busy_video_ids():
    manager = AgentStatusManager()
    manager.agents = [AgentStatus(id="main", name="Main", busy=False, max_tasks=3)]

    manager.set_busy("main", "video_1")
    manager.set_busy("main", "video_2")
    manager.set_busy("main", "video_3")

    manager.set_idle("main")
    manager.set_idle("main")
    manager.set_idle("main")

    assert manager.is_video_busy("video_1") is False
    assert manager.is_video_busy("video_2") is False
    assert manager.is_video_busy("video_3") is False


def test_openclaw_runner_extracts_agent_id_from_command_template():
    runner = OpenClawRunner(
        command_template=[
            "openclaw",
            "agent",
            "--local",
            "--agent",
            "main",
            "--message",
            "{prompt_text}",
            "--json",
        ],
        cwd=Path("."),
        timeout_seconds=600,
    )
    assert runner.agent_id == "main"


def test_openclaw_runner_extracts_agent_id_returns_empty_when_missing():
    runner = OpenClawRunner(
        command_template=["openclaw", "agent", "--local", "--message", "{prompt_text}", "--json"],
        cwd=Path("."),
        timeout_seconds=600,
    )
    assert runner.agent_id == ""


def test_openclaw_runner_extracts_agent_id_at_end_of_list():
    runner = OpenClawRunner(
        command_template=["openclaw", "--agent", "ops"],
        cwd=Path("."),
        timeout_seconds=600,
    )
    assert runner.agent_id == "ops"


def test_workspace_isolated_pi_status():
    manager = AgentStatusManager()
    manager.add_pi_agent_for_workspace("ws-1", max_tasks=2)
    manager.add_pi_agent_for_workspace("ws-2", max_tasks=3)

    manager.set_busy("pi", "video_1", workspace_id="ws-1")

    ws1 = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-1"][0]
    ws2 = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-2"][0]

    assert ws1["task_count"] == 1
    assert ws1["busy"] is True
    assert ws1["current_video_id"] == "video_1"
    assert ws2["task_count"] == 0
    assert ws2["busy"] is False
    assert ws2["current_video_id"] is None

    manager.set_idle("pi", workspace_id="ws-1")

    ws1 = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-1"][0]
    assert ws1["task_count"] == 0
    assert ws1["busy"] is False
    assert ws1["current_video_id"] is None


def test_remove_pi_agent_for_workspace():
    manager = AgentStatusManager()
    manager.add_pi_agent_for_workspace("ws-1", max_tasks=2)
    manager.add_pi_agent_for_workspace("ws-2", max_tasks=3)

    manager.remove_pi_agent_for_workspace("ws-1")

    assert [a["workspace_id"] for a in manager.to_dicts() if a["id"] == "pi"] == ["ws-2"]


def test_remove_pi_agent_for_workspace_is_noop_when_missing():
    manager = AgentStatusManager()
    manager.remove_pi_agent_for_workspace("ws-missing")
    assert manager.to_dicts() == []


def test_add_pi_agent_for_workspace_broadcasts_capacity_changes(monkeypatch):
    manager = AgentStatusManager()
    broadcast_count = 0

    def fake_broadcast():
        nonlocal broadcast_count
        broadcast_count += 1

    monkeypatch.setattr(manager, "_broadcast", fake_broadcast)
    manager.add_pi_agent_for_workspace("ws-1", max_tasks=2)
    manager.add_pi_agent_for_workspace("ws-1", max_tasks=5)

    agent = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-1"][0]
    assert agent["max_tasks"] == 5
    assert broadcast_count == 2


def test_idle_pops_correct_video_id():
    manager = AgentStatusManager()
    manager.add_pi_agent_for_workspace("ws-1", max_tasks=2)

    manager.set_busy("pi", "video_a", workspace_id="ws-1")
    manager.set_busy("pi", "video_b", workspace_id="ws-1")

    agent = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-1"][0]
    assert agent["task_count"] == 2
    assert agent["current_video_id"] == "video_b"

    manager.set_idle("pi", workspace_id="ws-1")

    agent = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-1"][0]
    assert agent["task_count"] == 1
    assert agent["current_video_id"] == "video_a"


def test_set_busy_and_idle_are_thread_safe():
    manager = AgentStatusManager()
    manager.add_pi_agent_for_workspace("ws-1", max_tasks=5)

    def _busy_idle_loop() -> None:
        for i in range(50):
            manager.set_busy("pi", {"id": f"job_{i}"}, workspace_id="ws-1")
            manager.set_idle("pi", workspace_id="ws-1")

    threads = [threading.Thread(target=_busy_idle_loop) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    agents = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-1"]
    assert len(agents) == 1
    assert agents[0]["task_count"] == 0
    assert agents[0]["busy"] is False
