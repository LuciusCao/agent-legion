"""Frozen node-config re-resolution for job workflow upgrade.

Extracted from ``job_workflow_upgrade`` to keep that service within its size
budget; the resolution itself is shared with intake semantics.
"""

from __future__ import annotations

import json

from server.app.jobs import JobQueries
from server.app.services.agent_service import published_agent_definitions
from server.app.services.node_config import resolve_workflow_node_configs
from server.app.workflows.definition import WorkflowDefinition


def intake_frozen_config_json(
    job_db: JobQueries, workspace_id: str, definition: WorkflowDefinition
) -> str | None:
    """Frozen node-config JSON exactly as an intake on *definition* would freeze it.

    Upgrade re-freezes so node-level config fixes (schema defaults → node
    config → workspace override) reach old jobs without a re-intake; ``None``
    when nothing resolves (mirrors intake's NULL). Raises ValueError on config
    drift — callers must treat it as a validation failure and apply no mutation.
    """
    resolved = resolve_workflow_node_configs(
        definition,
        published_agent_definitions(job_db, workspace_id),
        job_db.get_workspace(workspace_id),
    )
    if not resolved:
        return None
    return json.dumps(resolved, ensure_ascii=False, sort_keys=True)
