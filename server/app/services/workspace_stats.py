from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings


def build_workspace_stats(
    workspace: dict[str, Any],
    workspace_id: str,
    job_db: JobQueries,
    workflows: WorkflowCatalogService,
    settings: Settings,
) -> dict[str, Any]:
    workflow_key = workspace.get("default_workflow_key", "")
    if not workflow_key:
        raise InvalidOperationError("Workspace workflow is not set")
    latest_run = job_db.get_latest_node_run_for_workspace(workspace_id)
    executors = []
    for count in job_db.get_workspace_executor_runtime_counts(workspace_id):
        definition = settings.executor_definitions.get(count["executor_id"])
        global_capacity = definition.global_capacity if definition is not None else 0
        global_available = global_capacity - count["global_running"]
        available = max(0, min(count["workspace_limit"] - count["running"], global_available))
        executors.append(
            {
                "executor_id": count["executor_id"],
                "kind": definition.kind if definition is not None else "unknown",
                "global_capacity": global_capacity,
                "workspace_limit": count["workspace_limit"],
                "running": count["running"],
                "available": available,
                "binding_count": count["binding_count"],
            }
        )
    return {
        "workspace_id": workspace_id,
        "name": workspace.get("name", ""),
        "workflow_key": workflow_key,
        "workflow_label": workflows.label_of(str(workflow_key)),
        "job_stats": job_db.count_jobs_by_status(workspace_id),
        "executor_status": {"executors": executors},
        "latest_run": dict(latest_run) if latest_run else None,
    }
