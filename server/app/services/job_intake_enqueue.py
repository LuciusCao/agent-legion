from __future__ import annotations

from typing import Any


def enqueue_intake_batch(
    job_db: Any,
    workspace_id: str,
    payload: dict[str, Any],
    entity: str,
    input_values: list[str],
    mode: Any,
    revision: dict[str, Any],
    node_config: dict[str, Any] | None = None,
    node_code_versions: dict[str, Any] | None = None,
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
            "node_config": node_config or {},
            "node_code_versions": node_code_versions or {},
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
    # Re-submitting identical input collides with the deterministic batch id and
    # the upsert above is a no-op for a completed batch. When jobs from that
    # batch have since been deleted (current count below the created_count
    # recorded at completion), requeue the batch from the start so the consumer
    # rebuilds the missing jobs; job-level dedup filters the ones still present.
    if str(batch["status"]) == "completed":
        source_payload["_intake_queue"]["next_index"] = 0
        source_payload["_intake_queue"].pop("chunk_errors", None)
        requeued = job_db.requeue_completed_batch_if_depleted(
            str(batch["id"]), source_payload, int(batch["created_count"] or 0)
        )
        if requeued is not None:
            batch = requeued
    return {"batch": batch, "created_count": int(batch["created_count"]), "jobs": []}
