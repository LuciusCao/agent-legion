"""Agent execution queue: broker protocol, claim/sweep helpers, and dispatch."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker as AgentExecutionBroker
    from server.app.agent_broker.broker import AgentExecutionRequest as AgentExecutionRequest
    from server.app.agent_broker.dispatch import AgentDispatchService as AgentDispatchService
    from server.app.agent_broker.dispatch_pool import AgentEnqueuePool as AgentEnqueuePool

_EXPORTS = {
    "AgentDispatchService": "dispatch",
    "AgentEnqueuePool": "dispatch_pool",
    "AgentExecutionBroker": "broker",
    "AgentExecutionRequest": "broker",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    from importlib import import_module

    return getattr(import_module(f"server.app.agent_broker.{module}"), name)
