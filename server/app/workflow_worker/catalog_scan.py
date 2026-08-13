"""Workflow scan entries and runnable-workspace collection for the worker.

The scan list comes from the DB-backed workflow catalog
(DB-WORKFLOW-CATALOG-001): entries with a seed definition carry the parsed
``WorkflowDefinition`` as the fallback for jobs without a snapshot, while
registered workflows without a catalog definition still scan (their jobs run
off the published revision snapshot frozen at intake). The catalog is loaded
once at worker start; a backend restart picks up newly registered keys.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from server.app.services.workflow_catalog_store import WorkflowCatalogStore
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

ScanEntry = tuple[str, "WorkflowDefinition | None"]
WorkspaceJobs = dict[str, list[tuple["WorkflowDefinition | None", dict[str, Any]]]]


def load_workflow_scan_entries(
    settings: Settings,
) -> tuple[list[WorkflowDefinition], list[str]]:
    """Split the catalog into (definitions, definitionless keys) for scanning."""
    definitions: list[WorkflowDefinition] = []
    definitionless_keys: list[str] = []
    for entry in WorkflowCatalogStore(settings.database_url).list_entries():
        raw = entry.get("definition_json")
        if raw:
            definitions.append(workflow_definition_from_dict(json.loads(str(raw))))
        else:
            definitionless_keys.append(str(entry["key"]))
    return definitions, definitionless_keys


def iter_scan_entries(
    definitions: list[WorkflowDefinition], definitionless_keys: list[str]
) -> list[ScanEntry]:
    return [(d.key, d) for d in definitions] + [(key, None) for key in definitionless_keys]


def collect_runnable_workspace_jobs(
    worker: WorkflowWorkerThread,
) -> tuple[list[str], WorkspaceJobs]:
    """Group non-terminal job marks by workspace, skipping paused workspaces.

    ``worker._is_paused`` opens a DB connection per call; memoize per pass
    instead of paying it once per job.
    """
    workspace_ids: list[str] = []
    jobs_by_workspace: WorkspaceJobs = {}
    paused: dict[str, bool] = {}
    for key, definition in iter_scan_entries(worker._definitions, worker._definitionless_keys):
        for job in worker._mark_store.refresh(worker.job_db, key):
            if not (workspace_id := job.get("workspace_id")):
                continue
            workspace_id = str(workspace_id)
            if workspace_id not in paused:
                paused[workspace_id] = worker._is_paused(workspace_id)
            if paused[workspace_id]:
                continue
            if workspace_id not in jobs_by_workspace:
                workspace_ids.append(workspace_id)
                jobs_by_workspace[workspace_id] = []
            jobs_by_workspace[workspace_id].append((definition, job))
    return workspace_ids, jobs_by_workspace
