import json
from dataclasses import asdict

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.workflows.definition import workflow_definition_from_dict


def build_workspace_dag(
    job_db: JobQueries,
    workspace_id: str,
) -> dict:
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        raise NotFoundError("Workspace not found")
    workflow_key = str(workspace.get("default_workflow_key") or "")
    if not workflow_key:
        raise NotFoundError("Workspace workflow is not set")
    active = job_db.get_active_workflow_revision(workspace_id, workflow_key)
    if active is None:
        raise NotFoundError("Workspace has no active workflow revision")
    definition = workflow_definition_from_dict(json.loads(str(active["definition_json"])))
    counts = job_db.count_workspace_job_nodes_by_status(workspace_id, workflow_key)
    statuses = ["pending", "running", "completed", "failed", "stale"]
    return {
        "workflow": {
            "key": definition.key,
            "label": definition.label,
        },
        "nodes": [
            {
                "key": node.key,
                "label": node.label,
                "capability": node.capability,
                "max_concurrency": node.max_concurrency,
                "after": node.after,
                "inputs": node.inputs,
                "outputs": node.outputs,
                "execution": asdict(node.execution),
                "status_counts": {
                    status: counts.get(node.key, {}).get(status, 0) for status in statuses
                },
            }
            for node in definition.nodes.values()
        ],
    }
