"""Agent status rows: the registry owns the row set, nothing else.

Split from ``events.agents`` (issue #2 phase 2): discovery (openclaw-backed
``discover_agents`` callable) and broker upserts write rows here; workload
transitions and WS broadcasting live in the sibling modules. The registry
holds no broadcasting knowledge — consumers read state, they don't get
pushed. All access shares one ``threading.Lock`` with the workload module
(busy/idle transitions mutate rows in place), so both modules receive the
same lock instance at construction.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentStatus:
    id: str
    name: str
    busy: bool
    task_count: int = 0
    max_tasks: int = 1
    workspace_id: str = ""


def _agent_dict(agent: AgentStatus) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "busy": agent.busy,
        "task_count": agent.task_count,
        "max_tasks": agent.max_tasks,
        "workspace_id": agent.workspace_id,
    }


class AgentRegistry:
    """Row set for the agents panel, keyed by (agent_id, workspace_id)."""

    def __init__(
        self,
        lock: threading.Lock,
        discover_agents: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._lock = lock
        self._discover_agents = discover_agents
        self.agents: list[AgentStatus] = []

    def discover(self) -> list[AgentStatus]:
        try:
            records = self._discover_agents() if self._discover_agents is not None else []
            agents = [
                AgentStatus(
                    id=r["id"],
                    name=r.get("identityName") or r["id"],
                    busy=False,
                )
                for r in records
            ]
        except Exception:
            # #204 broad-except audit: deliberate discovery containment. The
            # callable is the openclaw discovery adapter — an external
            # process/HTTP surface whose failure space (subprocess errors,
            # malformed agent records, timeouts) is not a business family
            # this panel state layer could enumerate. The panel is a
            # status view: an empty row set on failure is strictly better
            # than killing the caller (the broker's registry upkeep and the
            # agents WS snapshot). The existing test pins the
            # clears-rows-and-returns-empty contract; the traceback goes to
            # the warning log.
            logger.warning("agent discovery failed, returning empty list", exc_info=True)
            agents = []
        with self._lock:
            self.agents = agents
        return list(agents)

    def get_all(self) -> list[AgentStatus]:
        with self._lock:
            return list(self.agents)

    def find(self, agent_id: str, workspace_id: str = "") -> AgentStatus | None:
        with self._lock:
            for agent in self.agents:
                if agent.id == agent_id and agent.workspace_id == workspace_id:
                    return agent
        return None

    def ensure_workspace_agent(
        self, agent_id: str, workspace_id: str, *, max_tasks: int = 1, name: str = ""
    ) -> bool:
        """Idempotently upsert a workspace-scoped status row for the panel.

        Distributed executions (broker claims by remote Agent Workers) have no
        in-process runner to discover, so the broker registers one row per
        (Worker, workspace) here; ``max_tasks`` mirrors the Worker's machine
        capacity so the panel shows "name busy/capacity" per Worker.
        Returns True when the row set changed (new row or capacity update).
        """
        with self._lock:
            for agent in self.agents:
                if agent.id == agent_id and agent.workspace_id == workspace_id:
                    if agent.max_tasks != max_tasks:
                        agent.max_tasks = max_tasks
                        return True
                    return False
            self.agents.append(
                AgentStatus(
                    id=agent_id,
                    name=name or agent_id,
                    busy=False,
                    task_count=0,
                    max_tasks=max_tasks,
                    workspace_id=workspace_id,
                )
            )
            return True

    def to_dicts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [_agent_dict(a) for a in self.agents]
