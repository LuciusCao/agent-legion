from typing import Any

from server.app.jobs import JobQueries


def migration_warnings(workspace_id: str) -> list[str]:
    # Legacy workspace_agent_assignments were removed by V005; no runtime warnings remain.
    _ = workspace_id
    return []


def configuration_with_warnings(
    job_db: JobQueries, workspace_id: str, configuration: dict[str, Any]
) -> dict[str, Any]:
    _ = job_db
    return {**configuration, "migration_warnings": migration_warnings(workspace_id)}
