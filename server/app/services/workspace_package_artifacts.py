from __future__ import annotations

from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings


def workspace_artifact_names(
    settings: Settings, workflow_keys: set[str], base_names: set[str]
) -> list[str]:
    """Return the curated artifact list for packaging.

    Every workflow goes through the generic path: ``base_names`` merged with
    the declared node outputs from the DB-backed workflow catalog.
    """
    catalog = WorkflowCatalogService(settings)
    names = set(base_names)
    for workflow_key in workflow_keys:
        if not workflow_key:
            continue
        definition = catalog.definition_or_none(workflow_key)
        if definition is None:
            continue
        for node in definition.nodes.values():
            names.update(node.outputs)
    return sorted(names)
