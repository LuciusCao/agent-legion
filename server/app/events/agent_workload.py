"""Agent workload transitions: busy/idle counting and the incremental event buffer.

Split from ``events.agents`` (issue #2 phase 2): the broker reports claim /
release here; transitions mutate registry rows in place under the shared lock
and record the latest envelope per (agent_id, workspace_id) so the next
broadcast flush sends one event per changed agent. The flush itself (and the
WS serving) stays with the facade in ``events.agents``; this module only
marks work pending.
"""

from __future__ import annotations

import threading
from typing import Any

from server.app.events.agent_registry import AgentRegistry, _agent_dict


class AgentWorkload:
    """Busy/idle transitions over registry rows, buffered for broadcast flush."""

    def __init__(self, lock: threading.Lock, registry: AgentRegistry) -> None:
        self._lock = lock
        self._registry = registry
        # Incremental WS events buffered between broadcast flushes, keyed by
        # (agent_id, workspace_id) so the latest state per agent wins.
        self._pending_events: dict[tuple[str, str], dict[str, Any]] = {}
        # A structural row-set change (upsert/discover) supersedes the
        # incremental buffer with one full snapshot envelope on next flush.
        self.snapshot_pending = False

    def set_busy(self, agent_id: str, *, workspace_id: str = "") -> None:
        with self._lock:
            for agent in self._registry.agents:
                if agent.id == agent_id and agent.workspace_id == workspace_id:
                    agent.task_count += 1
                    agent.busy = True
                    self._pending_events[(agent.id, agent.workspace_id)] = {
                        "type": "agent_busy",
                        "agent": _agent_dict(agent),
                    }
                    break

    def set_idle(self, agent_id: str, *, workspace_id: str = "") -> None:
        with self._lock:
            for agent in self._registry.agents:
                if agent.id == agent_id and agent.workspace_id == workspace_id:
                    agent.task_count = max(0, agent.task_count - 1)
                    agent.busy = agent.task_count > 0
                    self._pending_events[(agent.id, agent.workspace_id)] = {
                        "type": "agent_idle",
                        "agent": _agent_dict(agent),
                    }
                    break

    def mark_snapshot_pending(self) -> None:
        with self._lock:
            self.snapshot_pending = True

    def take_flush_batch(self) -> list[dict[str, Any]]:
        """Drain the buffer under the lock; empty list means 'send a snapshot'."""
        with self._lock:
            snapshot = self.snapshot_pending
            self.snapshot_pending = False
            events = [] if snapshot else list(self._pending_events.values())
            self._pending_events.clear()
        return events
