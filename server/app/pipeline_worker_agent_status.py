from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from server.app.executors.models import ClaimedExecution, ExecutionContext
from server.app.executors.registry import ExecutorRegistry


def _status_agent_for_claim(
    registry: ExecutorRegistry, claim: ClaimedExecution
) -> tuple[str, str] | None:
    """Return (agent_id, workspace_id) to report for this claim, or None."""
    definition = registry.definitions().get(claim.executor_id)
    if definition is None:
        return None
    if definition.kind == "pi":
        return ("pi", claim.workspace_id)
    if definition.kind == "openclaw":
        return (getattr(definition, "agent_id", claim.executor_id), "")
    return None


def _agent_status_payload(context: ExecutionContext) -> dict[str, Any]:
    return {
        "id": context.job_id,
        "title": str(context.job.get("title", "")),
        "content_type": str(context.workspace.get("default_entity") or context.pipeline_key),
        "external_id": str(context.job.get("source_id", "")),
        "current_phase": context.node_key,
    }


@contextmanager
def agent_status_scope(
    agent_manager: Any,
    registry: ExecutorRegistry,
    claim: ClaimedExecution,
    context: ExecutionContext,
) -> Generator[None, None, None]:
    """Report an agent as busy while a claim executes, then idle."""
    status_agent = _status_agent_for_claim(registry, claim)
    if status_agent is not None and agent_manager is not None:
        agent_id, workspace_id = status_agent
        agent_manager.set_busy(
            agent_id,
            _agent_status_payload(context),
            workspace_id=workspace_id,
        )
    try:
        yield
    finally:
        if status_agent is not None and agent_manager is not None:
            agent_id, workspace_id = status_agent
            agent_manager.set_idle(agent_id, workspace_id=workspace_id)
