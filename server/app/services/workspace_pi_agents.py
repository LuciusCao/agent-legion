from __future__ import annotations

from typing import Any

from server.app.agents import AgentStatusManager
from server.app.executors.config import ExecutorConfig
from server.app.jobs import JobQueries
from server.app.settings import Settings


def _find_pi_allocation(
    allocations: list[dict[str, Any]],
    executor_definitions: dict[str, ExecutorConfig],
) -> dict[str, Any] | None:
    for allocation in allocations:
        executor_id = allocation.get("executor_id", "")
        definition = executor_definitions.get(executor_id)
        if definition is not None and definition.kind == "pi":
            return allocation
    return None


def sync_pi_agents_for_workspace(
    workspace_id: str,
    executor_allocations: list[dict[str, Any]],
    executor_definitions: dict[str, ExecutorConfig],
    agent_manager: AgentStatusManager,
) -> None:
    """Ensure the Pi agent for a workspace matches its executor allocation."""
    pi_allocation = _find_pi_allocation(executor_allocations, executor_definitions)
    if pi_allocation is not None:
        agent_manager.add_pi_agent_for_workspace(
            workspace_id, int(pi_allocation.get("concurrency_limit", 1))
        )
    else:
        agent_manager.remove_pi_agent_for_workspace(workspace_id)


def sync_workspace_pi_agents(
    job_db: JobQueries,
    settings: Settings,
    agent_manager: AgentStatusManager,
) -> None:
    """Register pi agents for all workspaces that currently allocate a pi executor."""
    for workspace in job_db.list_workspaces():
        workspace_id = str(workspace["id"])
        config = job_db.get_workspace_executor_configuration(workspace_id)
        sync_pi_agents_for_workspace(
            workspace_id,
            config.get("allocations", []),
            settings.executor_definitions,
            agent_manager,
        )
