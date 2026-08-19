from __future__ import annotations

from server.app.jobs import JobQueries
from server.app.services.workflow_definitions import workspace_active_definition


def workspace_artifact_names(
    job_db: JobQueries, workspace_id: str, workflow_keys: set[str], base_names: set[str]
) -> list[str]:
    """Return the curated artifact list for packaging.

    Every workflow goes through the generic path: ``base_names`` merged with
    the declared node outputs from the workspace's active workflow revision
    (schema v50).
    """
    names = set(base_names)
    for workflow_key in workflow_keys:
        if not workflow_key:
            continue
        definition = workspace_active_definition(job_db, workspace_id, workflow_key)
        if definition is None:
            continue
        for node in definition.nodes.values():
            names.update(node.outputs)
    return sorted(names)
