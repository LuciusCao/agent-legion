"""Agent Worker control plane (issue #191).

Worker registration and credentials lifecycle: the registry, scoped register
tokens (issue / delete / cascade), registration key guard, worker
declarations normalization, liveness throttling, execution completion
handling, and openclaw CLI discovery. Scheduling and claim policy live in
``server.app.agent_broker``; this package owns the management surface only.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.agent_control.completion import AgentCompletionHandler as AgentCompletionHandler
    from server.app.agent_control.registry import AgentWorkerRegistry as AgentWorkerRegistry

_EXPORTS = {
    "AgentCompletionHandler": "completion",
    "AgentWorkerRegistry": "registry",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    from importlib import import_module

    return getattr(import_module(f"server.app.agent_control.{module}"), name)
