import json
import subprocess
import threading

from server.app.agent_control.openclaw_discovery import list_openclaw_agents
from server.app.events.agents import AgentStatus, AgentStatusManager


def _agent_dict(**kwargs):
    defaults = {
        "busy": False,
        "task_count": 0,
        "max_tasks": 1,
        "workspace_id": "",
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

    manager.set_busy("main")
    manager._broadcast()
    assert json.loads(queue.get_nowait()) == {
        "type": "agent_busy",
        "agent": _agent_dict(id="main", name="Main", busy=True, task_count=1),
    }

    manager.set_idle("main")
    manager._broadcast()
    assert json.loads(queue.get_nowait()) == {
        "type": "agent_idle",
        "agent": _agent_dict(id="main", name="Main"),
    }


def test_ensure_workspace_agent_broadcasts_snapshot_envelope():
    from server.app.events.bus import InProcessEventBus

    bus = InProcessEventBus()
    manager = AgentStatusManager(event_bus=bus)
    queue = bus.subscribe("agents")

    manager.ensure_workspace_agent("worker-1", "ws-1", max_tasks=2, name="Worker")

    assert json.loads(queue.get_nowait()) == {
        "type": "snapshot",
        "agents": [
            _agent_dict(id="worker-1", name="Worker", max_tasks=2, workspace_id="ws-1"),
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
    manager.ensure_workspace_agent("worker-1", "ws1", max_tasks=10)

    manager.set_busy("worker-1", workspace_id="ws1")
    manager.set_busy("worker-1", workspace_id="ws1")

    assert manager.has_pending_broadcast() is True


def test_concurrent_set_busy_increments_task_count():
    manager = AgentStatusManager()
    manager.agents = [AgentStatus(id="main", name="Main", busy=False, max_tasks=3)]

    manager.set_busy("main")
    manager.set_busy("main")
    manager.set_busy("main")

    agent = manager.to_dicts()[0]
    assert agent["task_count"] == 3
    assert agent["busy"] is True


def test_concurrent_set_idle_decrements_task_count():
    manager = AgentStatusManager()
    manager.agents = [AgentStatus(id="main", name="Main", busy=False, max_tasks=3)]

    manager.set_busy("main")
    manager.set_busy("main")
    manager.set_busy("main")

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


def test_workspace_isolated_worker_status():
    manager = AgentStatusManager()
    manager.ensure_workspace_agent("worker-1", "ws-1", max_tasks=2)
    manager.ensure_workspace_agent("worker-1", "ws-2", max_tasks=3)

    manager.set_busy("worker-1", workspace_id="ws-1")

    ws1 = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-1"][0]
    ws2 = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-2"][0]

    assert ws1["task_count"] == 1
    assert ws1["busy"] is True
    assert ws2["task_count"] == 0
    assert ws2["busy"] is False

    manager.set_idle("worker-1", workspace_id="ws-1")

    ws1 = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-1"][0]
    assert ws1["task_count"] == 0
    assert ws1["busy"] is False


def test_ensure_workspace_agent_broadcasts_capacity_changes(monkeypatch):
    manager = AgentStatusManager()
    broadcast_count = 0

    def fake_broadcast():
        nonlocal broadcast_count
        broadcast_count += 1

    monkeypatch.setattr(manager, "_broadcast", fake_broadcast)
    manager.ensure_workspace_agent("worker-1", "ws-1", max_tasks=2)
    manager.ensure_workspace_agent("worker-1", "ws-1", max_tasks=5)

    agent = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-1"][0]
    assert agent["max_tasks"] == 5
    assert broadcast_count == 2


def test_set_busy_and_idle_are_thread_safe():
    manager = AgentStatusManager()
    manager.ensure_workspace_agent("worker-1", "ws-1", max_tasks=5)

    def _busy_idle_loop() -> None:
        for _ in range(50):
            manager.set_busy("worker-1", workspace_id="ws-1")
            manager.set_idle("worker-1", workspace_id="ws-1")

    threads = [threading.Thread(target=_busy_idle_loop) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    agents = [a for a in manager.to_dicts() if a["workspace_id"] == "ws-1"]
    assert len(agents) == 1
    assert agents[0]["task_count"] == 0
    assert agents[0]["busy"] is False


def test_list_openclaw_agents_success(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps([{"id": "main"}, {"id": "aux"}]), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    agents = list_openclaw_agents()
    assert agents == [{"id": "main"}, {"id": "aux"}]


def test_list_openclaw_agents_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert list_openclaw_agents() == []


def test_list_openclaw_agents_invalid_json(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert list_openclaw_agents() == []


def test_list_openclaw_agents_exception(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("no openclaw")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert list_openclaw_agents() == []
