"""Unit tests for the agent workload module (issue #2 phase 2 split)."""

from __future__ import annotations

import threading

from server.app.events.agent_registry import AgentRegistry
from server.app.events.agent_workload import AgentWorkload


def _workload() -> tuple[AgentRegistry, AgentWorkload]:
    lock = threading.Lock()
    registry = AgentRegistry(lock)
    return registry, AgentWorkload(lock, registry)


def test_set_busy_increments_and_buffers_one_event():
    registry, workload = _workload()
    registry.ensure_workspace_agent("w1", "ws-1", max_tasks=4, name="Worker")

    workload.set_busy("w1", workspace_id="ws-1")
    workload.set_busy("w1", workspace_id="ws-1")

    (row,) = registry.to_dicts()
    assert row["task_count"] == 2
    assert row["busy"] is True
    # One buffered event per agent (latest state wins), not one per call.
    events = workload.take_flush_batch()
    assert len(events) == 1
    assert events[0]["type"] == "agent_busy"
    assert events[0]["agent"]["task_count"] == 2


def test_set_idle_derives_busy_from_task_count():
    registry, workload = _workload()
    registry.ensure_workspace_agent("w1", "ws-1", max_tasks=4, name="Worker")
    workload.set_busy("w1", workspace_id="ws-1")
    workload.set_busy("w1", workspace_id="ws-1")

    workload.set_idle("w1", workspace_id="ws-1")
    (row,) = registry.to_dicts()
    assert row["task_count"] == 1
    assert row["busy"] is True

    workload.set_idle("w1", workspace_id="ws-1")
    (row,) = registry.to_dicts()
    assert row["task_count"] == 0
    assert row["busy"] is False

    events = workload.take_flush_batch()
    assert events[-1]["type"] == "agent_idle"
    assert events[-1]["agent"]["task_count"] == 0


def test_set_idle_is_clamped_at_zero():
    registry, workload = _workload()
    registry.ensure_workspace_agent("w1", "ws-1", max_tasks=1, name="Worker")

    workload.set_idle("w1", workspace_id="ws-1")

    (row,) = registry.to_dicts()
    assert row["task_count"] == 0


def test_unknown_agent_is_silent():
    registry, workload = _workload()

    workload.set_busy("ghost", workspace_id="ws-1")
    workload.set_idle("ghost", workspace_id="ws-1")

    assert registry.to_dicts() == []
    # Empty flush batch signals the facade to send a full snapshot instead.
    assert workload.take_flush_batch() == []


def test_workspace_scoping_isolates_transitions():
    registry, workload = _workload()
    registry.ensure_workspace_agent("w1", "ws-1", max_tasks=2, name="Worker")
    registry.ensure_workspace_agent("w1", "ws-2", max_tasks=2, name="Worker")

    workload.set_busy("w1", workspace_id="ws-1")

    rows = {row["workspace_id"]: row for row in registry.to_dicts()}
    assert rows["ws-1"]["task_count"] == 1
    assert rows["ws-2"]["task_count"] == 0


def test_snapshot_pending_supersedes_incrementals():
    registry, workload = _workload()
    registry.ensure_workspace_agent("w1", "ws-1", max_tasks=2, name="Worker")
    workload.set_busy("w1", workspace_id="ws-1")

    workload.mark_snapshot_pending()

    # A structural change discards the buffered incrementals; the facade
    # answers with one snapshot envelope instead.
    assert workload.take_flush_batch() == []
    assert workload.take_flush_batch() == []


def test_flush_batch_drains_the_buffer():
    registry, workload = _workload()
    registry.ensure_workspace_agent("w1", "ws-1", max_tasks=2, name="Worker")
    workload.set_busy("w1", workspace_id="ws-1")

    assert len(workload.take_flush_batch()) == 1
    assert workload.take_flush_batch() == []
