"""Workflow worker poll loop: routing, readiness, claiming, and execution."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.workflow_worker.agent_gate import AgentPassState as AgentPassState
    from server.app.workflow_worker.execution import reap_futures as reap_futures
    from server.app.workflow_worker.maintenance import WorkflowMaintenance as WorkflowMaintenance
    from server.app.workflow_worker.ready_cache import ReadyCandidate as ReadyCandidate
    from server.app.workflow_worker.routing import NodeRoute as NodeRoute
    from server.app.workflow_worker.schedule import try_claim_and_submit as try_claim_and_submit
    from server.app.workflow_worker.thread import WorkflowWorkerThread as WorkflowWorkerThread

_EXPORTS = {
    "AgentPassState": "agent_gate",
    "NodeRoute": "routing",
    "ReadyCandidate": "ready_cache",
    "WorkflowMaintenance": "maintenance",
    "WorkflowWorkerThread": "thread",
    "reap_futures": "execution",
    "request_restock": "agent_gate",
    "try_claim_and_submit": "schedule",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    from importlib import import_module

    return getattr(import_module(f"server.app.workflow_worker.{module}"), name)
