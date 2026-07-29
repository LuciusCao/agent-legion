from __future__ import annotations

from typing import Any


def enqueue_intake_batch(
    job_db: Any,
    workspace_id: str,
    payload: dict[str, Any],
    entity: str,
    input_values: list[str],
    resource_config: dict[str, Any],
    mode: Any,
    revision: dict[str, Any],
    node_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workflow_key = str(payload["workflow_key"])
    source_kind = str(payload["source_kind"])
    input_field = str(mode.input_field)
    source_payload = dict(payload)
    source_payload.update(
        {
            "entity": entity,
            "question_ids": input_values if input_field == "question_ids" else [],
            "knowledge_codes": input_values if input_field == "knowledge_codes" else [],
            "resource_config": resource_config,
            "node_config": node_config or {},
            "intake_mode": {
                "key": mode.key,
                "label": mode.label,
                "input_field": mode.input_field,
            },
            "task_candidates": [],
            "_intake_queue": {
                "input_values": input_values,
                "next_index": 0,
                "workflow_revision_id": revision["id"],
            },
        }
    )
    batch = job_db.create_batch(
        workflow_key,
        source_kind,
        source_payload,
        workspace_id=workspace_id,
        status="queued",
    )
    return {"batch": batch, "created_count": int(batch["created_count"]), "jobs": []}
