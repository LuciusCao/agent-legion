from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING

from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def serialize_definition(definition: WorkflowDefinition) -> str:
    payload = asdict(definition)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def definition_hash(definition_json: str) -> str:
    return hashlib.sha256(definition_json.encode("utf-8")).hexdigest()


def definition_from_job_snapshot(job: dict) -> WorkflowDefinition | None:
    raw = job.get("workflow_definition_snapshot_json") or ""
    if not raw:
        return None
    try:
        payload = json.loads(str(raw))
        return workflow_definition_from_dict(payload)
    except Exception:
        return None


class WorkflowRevisionService:
    def __init__(self, job_db: JobQueries) -> None:
        self.job_db = job_db

    def publish_workspace_revision(self, workspace_id: str, definition: WorkflowDefinition) -> dict:
        definition_json = serialize_definition(definition)
        version = self.job_db.next_workflow_revision_version(workspace_id, definition.key)
        revision_id = f"{workspace_id}:{definition.key}:v{version}"
        return self.job_db.create_workflow_revision(
            revision_id=revision_id,
            workspace_id=workspace_id,
            workflow_key=definition.key,
            version=version,
            status="active",
            definition_json=definition_json,
            definition_hash=definition_hash(definition_json),
        )

    def get_active(self, workspace_id: str, workflow_key: str) -> dict:
        revision = self.job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if revision is None:
            raise ValueError(f"No active workflow revision for {workflow_key}")
        return revision

    def ensure_active_revision(self, workspace_id: str, definition: WorkflowDefinition) -> dict:
        existing = self.job_db.get_active_workflow_revision(workspace_id, definition.key)
        if existing is not None:
            return existing
        return self.publish_workspace_revision(workspace_id, definition)
