from __future__ import annotations

import json
from typing import Any

from server.app.workflows.resources import resolve_cms_resource


def _decode_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _effective_cms_config(job: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    settings_config = context.get("settings_config")
    if not isinstance(settings_config, dict):
        settings_config = {}
    job_db = context.get("job_db")
    workspace = None
    batch_payload = None
    workspace_id = job.get("workspace_id")
    if job_db is not None and workspace_id:
        workspace = job_db.get_workspace(str(workspace_id))
        batch = job_db.get_batch(str(job.get("batch_id", "")))
        if batch:
            batch_payload = _decode_json_object(batch.get("source_payload_json"))
    return resolve_cms_resource(settings_config, workspace, batch_payload, "question_detail")
