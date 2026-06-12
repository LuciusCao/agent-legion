from typing import Any

from server.app.jobs import JobQueries


def migration_warnings(job_db: JobQueries, workspace_id: str) -> list[str]:
    return [
        f"Legacy agent assignment {row['agent_id']} has no Executor mapping"
        for row in job_db.list_workspace_agents(workspace_id)
        if row["agent_id"] != "pi"
    ]


def configuration_with_warnings(
    job_db: JobQueries, workspace_id: str, configuration: dict[str, Any]
) -> dict[str, Any]:
    return {**configuration, "migration_warnings": migration_warnings(job_db, workspace_id)}
