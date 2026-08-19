from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError
from server.app.services.workflow_definitions import workspace_active_definition
from server.app.settings import Settings


def build_workspace_stats(
    workspace: dict[str, Any],
    workspace_id: str,
    job_db: JobQueries,
    settings: Settings,
) -> dict[str, Any]:
    workflow_key = str(workspace.get("default_workflow_key") or "")
    if not workflow_key:
        raise InvalidOperationError("Workspace workflow is not set")
    latest_run = job_db.get_latest_node_run_for_workspace(workspace_id)
    # Single implicit code pool (P-0.5): capacity comes from the instance
    # settings; availability is global (the pool is shared across workspaces).
    capacity = settings.executor_runtime.code_capacity
    counts = job_db.get_code_pool_counts(workspace_id)
    code_pool = {
        "capacity": capacity,
        "running": counts["running"],
        "available": max(0, capacity - counts["global_running"]),
    }
    # The label comes from the workspace's active revision definition (schema
    # v50); an unpublished workflow falls back to the raw key.
    definition = workspace_active_definition(job_db, workspace_id, workflow_key)
    workflow_label = definition.label if definition is not None else workflow_key
    return {
        "workspace_id": workspace_id,
        "name": workspace.get("name", ""),
        "workflow_key": workflow_key,
        "workflow_label": workflow_label,
        "job_stats": job_db.count_jobs_by_status(workspace_id),
        "code_pool": code_pool,
        "latest_run": dict(latest_run) if latest_run else None,
    }
