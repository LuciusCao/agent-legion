"""Job/workspace event pipeline: bus protocol, SSE, buffer, and aggregator."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.events.agent_broadcast import (
        AgentBroadcastController as AgentBroadcastController,
    )
    from server.app.events.agents import AgentStatus as AgentStatus
    from server.app.events.agents import AgentStatusManager as AgentStatusManager
    from server.app.events.aggregator import (
        WorkspaceJobEventAggregator as WorkspaceJobEventAggregator,
    )
    from server.app.events.aggregator import broadcast_job_update as broadcast_job_update
    from server.app.events.aggregator import (
        build_workspace_event_aggregator as build_workspace_event_aggregator,
    )
    from server.app.events.aggregator import record_job_update as record_job_update
    from server.app.events.buffer import JobEventBuffer as JobEventBuffer
    from server.app.events.bus import EventBus as EventBus
    from server.app.events.bus import InProcessEventBus as InProcessEventBus
    from server.app.events.bus import workspace_channel as workspace_channel
    from server.app.events.sse import JobEventManager as JobEventManager

_EXPORTS = {
    "AgentBroadcastController": "agent_broadcast",
    "AgentStatus": "agents",
    "AgentStatusManager": "agents",
    "EventBus": "bus",
    "InProcessEventBus": "bus",
    "JobEventBuffer": "buffer",
    "JobEventManager": "sse",
    "WorkspaceJobEventAggregator": "aggregator",
    "broadcast_job_update": "aggregator",
    "build_workspace_event_aggregator": "aggregator",
    "record_job_update": "aggregator",
    "workspace_channel": "bus",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    from importlib import import_module

    return getattr(import_module(f"server.app.events.{module}"), name)
