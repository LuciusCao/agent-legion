"""Workflow scan entries and runnable-workspace collection for the worker.

Workspace-driven (schema v50, issue #112): every workspace with a non-empty
``default_workflow_key`` is scanned, and its ACTIVE revision definition rides
along as the fallback for snapshot-less jobs.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from server.app.db.transaction import read_connection
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)

# (workspace_id, workflow_key, fallback definition | None)
ScanEntry = tuple[str, str, "WorkflowDefinition | None"]
WorkspaceJobs = dict[str, list[tuple["WorkflowDefinition | None", dict[str, Any]]]]

_SCANNABLE_WORKSPACES = (
    "select id, default_workflow_key from workspaces"
    " where default_workflow_key <> '' order by created_at, id"
)
_ACTIVE_REVISIONS = (
    "select workspace_id, workflow_key, definition_json from workflow_revisions"
    " where status='active'"
)


def load_workflow_scan_entries(settings: Settings) -> list[ScanEntry]:
    """One scan entry per workspace, carrying its active revision definition."""
    with read_connection(settings.database_url) as conn:
        workspaces = conn.execute(_SCANNABLE_WORKSPACES).fetchall()
        revisions = {
            (str(row["workspace_id"]), str(row["workflow_key"])): row["definition_json"]
            for row in conn.execute(_ACTIVE_REVISIONS).fetchall()
        }
    entries: list[ScanEntry] = []
    for workspace in workspaces:
        workspace_id = str(workspace["id"])
        workflow_key = str(workspace["default_workflow_key"])
        raw = revisions.get((workspace_id, workflow_key))
        definition: WorkflowDefinition | None = None
        if raw:
            try:
                definition = workflow_definition_from_dict(json.loads(str(raw)))
            except Exception:
                logger.warning(
                    "workflow scan: workspace %s active revision for %r failed to parse;"
                    " scanning with no fallback definition",
                    workspace_id,
                    workflow_key,
                )
        entries.append((workspace_id, workflow_key, definition))
    return entries


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
    # Take the snapshot once so the pass is consistent even when a reload
    # swaps the list mid-iteration.
    entries = worker.state.scan_entries
    definitions_by_workspace = {workspace_id: d for workspace_id, _key, d in entries}
    # Refresh marks once per distinct workflow key (legacy databases may share
    # a key across workspaces); each job pairs with its OWN workspace's
    # fallback definition. The by-key fallback only covers hand-built scan
    # lists (tests): in production every job's workspace has its own entry.
    definitions_by_key: dict[str, WorkflowDefinition | None] = {}
    for _ws, workflow_key, definition in entries:
        definitions_by_key.setdefault(workflow_key, definition)
    seen_keys: set[str] = set()
    for _workspace_id, workflow_key, _definition in entries:
        if workflow_key in seen_keys:
            continue
        seen_keys.add(workflow_key)
        for job in worker.state.mark_store.refresh(worker.job_db, workflow_key):
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
            definition = definitions_by_workspace.get(
                workspace_id, definitions_by_key.get(workflow_key)
            )
            jobs_by_workspace[workspace_id].append((definition, job))
    return workspace_ids, jobs_by_workspace
