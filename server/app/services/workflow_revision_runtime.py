"""Mutable runtime settings for an otherwise immutable workflow revision.

The in-place write itself lives in ``runtime_config`` (M47: the runtime
channel — PATCH workflow-runtime / rollback — shares it with this draft
path); what remains here is the dispatch between the in-place edit and a
structural publish, plus the ``embed_node_code_pins`` re-export for the
publish pipeline's historical import path.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.services.runtime_config import (
    embed_node_code_pins as embed_node_code_pins,
)
from server.app.services.runtime_config import (
    update_active_revision_runtime_in_place,
)
from server.app.services.workflow_revision_change import revision_structurally_changed
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def save_revision_runtime_or_publish(
    job_db: JobQueries,
    workspace_id: str,
    definition: WorkflowDefinition,
    publish: Callable[[str, WorkflowDefinition, Callable[[Any], None] | None], dict],
    on_commit: Callable[[Any], None] | None = None,
    created_by: str = "",
) -> dict:
    """Runtime edit in place, or delegate to ``publish`` on structural change.

    ``on_commit`` rides whichever write wins (#1221: draft publish passes the
    draft-row removal): forwarded to ``publish`` (the revision commit hook)
    on the structural/first-publish paths, or run inside the in-place edit's
    own transaction (``runtime_config.update_active_revision_runtime_in_place``,
    which also appends the runtime config version row — M47 audit trail).
    ``created_by`` is stamped on that version row; callers without an
    operator context (seed paths) leave it empty.
    """
    active = job_db.get_active_workflow_revision(workspace_id, definition.key)
    if active is None:
        return publish(workspace_id, definition, on_commit)
    current = workflow_definition_from_dict(json.loads(str(active["definition_json"])))
    # #1114：与草稿对比的 creates_revision 同一判定函数（runtime 属结构）。
    if revision_structurally_changed(current, definition):
        return publish(workspace_id, definition, on_commit)
    revision, _version_row = update_active_revision_runtime_in_place(
        job_db,
        workspace_id,
        definition,
        active=active,
        created_by=created_by,
        on_commit=on_commit,
    )
    return revision
