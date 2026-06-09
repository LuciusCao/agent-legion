from pathlib import Path

import pytest

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.db.notifications import NotificationHub
from server.app.pipeline.openclaw import OpenClawRunner
from server.app.pipeline.runners import RunnerPool


class FakeAgentManager:
    def __init__(self, assignments):
        self._assignments = assignments

    def get_allowed_agents(self, workspace_id):
        return self._assignments.get(workspace_id, [])


def test_runner_pool_acquire_filters_by_workspace():
    runners = [
        OpenClawRunner(agent_id="agent-1", command_template=[], cwd=Path("."), timeout_seconds=60),
        OpenClawRunner(agent_id="agent-2", command_template=[], cwd=Path("."), timeout_seconds=60),
    ]
    manager = FakeAgentManager({"ws1": ["agent-1"]})
    pool = RunnerPool(runners, agent_manager=manager)
    idx, runner = pool.acquire(workspace_id="ws1")
    assert runner.agent_id == "agent-1"


def test_runner_pool_acquire_falls_back_when_no_workspace():
    runners = [
        OpenClawRunner(agent_id="agent-1", command_template=[], cwd=Path("."), timeout_seconds=60)
    ]
    pool = RunnerPool(runners)
    idx, runner = pool.acquire()
    assert runner.agent_id == "agent-1"


def test_runner_pool_acquire_skips_unassigned_agents():
    runners = [
        OpenClawRunner(agent_id="agent-1", command_template=[], cwd=Path("."), timeout_seconds=60),
        OpenClawRunner(agent_id="agent-2", command_template=[], cwd=Path("."), timeout_seconds=60),
    ]
    manager = FakeAgentManager({"ws1": ["agent-2"]})
    pool = RunnerPool(runners, agent_manager=manager)
    idx, runner = pool.acquire(workspace_id="ws1")
    assert runner.agent_id == "agent-2"
    with pytest.raises(RuntimeError):
        pool.acquire(workspace_id="ws1")


def test_agent_status_manager_workspace_assignments():
    manager = AgentStatusManager()
    manager.set_workspace_assignment("video-hive", "agent-a", 2)
    manager.set_workspace_assignment("other", "agent-b", 1)

    assert manager.get_allowed_agents("video-hive") == ["agent-a"]
    assert manager.is_agent_allowed("video-hive", "agent-a") is True
    assert manager.is_agent_allowed("video-hive", "agent-b") is False

    manager.remove_workspace_assignment("video-hive", "agent-a")
    assert manager.is_agent_allowed("video-hive", "agent-a") is False


def test_database_workspace_agent_assignment_queries(tmp_path):
    db_path = tmp_path / "test.sqlite"
    hub = NotificationHub()
    db = Database(db_path, hub=hub, videos_dir=tmp_path / "videos")

    db.set_workspace_agent_assignment("video-hive", "agent-1", 2)
    db.set_workspace_agent_assignment("video-hive", "agent-2", 1)
    db.set_workspace_agent_assignment("other", "agent-1", 3)

    agents = db.list_workspace_agents("video-hive")
    assert {a["agent_id"]: a["concurrency_limit"] for a in agents} == {
        "agent-1": 2,
        "agent-2": 1,
    }

    db.set_workspace_agent_assignment("video-hive", "agent-1", 5)
    agents = db.list_workspace_agents("video-hive")
    assert {a["agent_id"]: a["concurrency_limit"] for a in agents} == {
        "agent-1": 5,
        "agent-2": 1,
    }

    db.remove_workspace_agent_assignment("video-hive", "agent-1")
    agents = db.list_workspace_agents("video-hive")
    assert [a["agent_id"] for a in agents] == ["agent-2"]


def test_agent_status_manager_loads_from_database(tmp_path):
    db_path = tmp_path / "test.sqlite"
    hub = NotificationHub()
    db = Database(db_path, hub=hub, videos_dir=tmp_path / "videos")

    db.set_workspace_agent_assignment("video-hive", "agent-x", 2)
    db.set_workspace_agent_assignment("video-hive", "agent-y", 3)

    manager = AgentStatusManager()
    manager.load_workspace_assignments(db)

    assert set(manager.get_allowed_agents("video-hive")) == {"agent-x", "agent-y"}
    assert manager.is_agent_allowed("video-hive", "agent-x") is True
    assert manager.is_agent_allowed("video-hive", "other") is False
