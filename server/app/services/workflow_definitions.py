"""Workspace-scoped workflow definition resolution (schema v50, issue #112).

The global workflow catalog is retired: a workflow is the DAG inside one
workspace, so the authoritative definition source is that workspace's ACTIVE
workflow revision. Jobs without an intake-frozen snapshot fall back to their
own workspace's active revision — never to a global template (the stale
catalog-template incident behind this retirement).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import NotFoundError
from server.app.workflows.builtin import load_builtin_workflow
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def workspace_revision_definition(
    revision: dict[str, Any] | None,
) -> WorkflowDefinition | None:
    """Parse a revision row's definition; None when there is no revision."""
    if revision is None:
        return None
    return workflow_definition_from_dict(json.loads(str(revision["definition_json"])))


def require_workspace_revision_definition(
    revision: dict[str, Any] | None,
) -> WorkflowDefinition:
    definition = workspace_revision_definition(revision)
    if definition is None:
        raise NotFoundError(
            "Workspace has no active workflow revision; publish a workflow revision first"
        )
    return definition


def workspace_active_definition(
    job_db: JobQueries, workspace_id: str, workflow_key: str
) -> WorkflowDefinition | None:
    """The workspace's active revision definition; None when unpublished."""
    if not workflow_key:
        return None
    revision = job_db.get_active_workflow_revision(workspace_id, workflow_key)
    return workspace_revision_definition(revision)


def require_workspace_active_definition(
    job_db: JobQueries, workspace_id: str, workflow_key: str
) -> WorkflowDefinition:
    revision = (
        job_db.get_active_workflow_revision(workspace_id, workflow_key) if workflow_key else None
    )
    return require_workspace_revision_definition(revision)


def builtin_definition_or_none(workflow_key: str) -> WorkflowDefinition | None:
    """The repo-shipped sample template; None for any other key."""
    try:
        return load_builtin_workflow(workflow_key)
    except KeyError:
        return None
