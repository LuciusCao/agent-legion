from __future__ import annotations

import json
import logging
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_intake_chunks import resolve_fresh_candidates
from server.app.services.job_intake_resolution import RESOLVER_MAP
from server.app.services.job_intake_video import write_video_input
from server.app.services.job_intake_workspace import get_workspace
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import workflow_definition_from_dict

logger = logging.getLogger(__name__)
INTAKE_QUEUE_CHUNK_SIZE = 25


class JobIntakeQueue:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        job_event_buffer: Any | None = None,
    ) -> None:
        self.job_db = job_db
        self.settings = settings
        self.job_event_buffer = job_event_buffer

    def consume_once(self) -> bool:
        batch = self.job_db.claim_intake_batch()
        if batch is None:
            return False
        try:
            self._consume_chunk(batch)
        except Exception as exc:
            logger.exception("async intake batch %s failed", batch["id"])
            self.job_db.update_intake_batch(
                str(batch["id"]), status="failed", error_message=str(exc)
            )
        return True

    def _consume_chunk(self, batch: dict[str, Any]) -> None:
        payload = json.loads(str(batch["source_payload_json"]))
        queue_state = payload["_intake_queue"]
        input_values = [str(value) for value in queue_state["input_values"]]
        start = int(queue_state.get("next_index", 0))
        end = min(start + INTAKE_QUEUE_CHUNK_SIZE, len(input_values))
        revision = self.job_db.get_workflow_revision(
            str(batch["workspace_id"]),
            str(batch["workflow_key"]),
            str(queue_state["workflow_revision_id"]),
        )
        if revision is None:
            raise ValueError("Queued workflow revision no longer exists")
        definition = workflow_definition_from_dict(json.loads(revision["definition_json"]))
        mode = definition.intake.modes[str(batch["source_kind"])]
        entity = str(payload["entity"])
        resolver = RESOLVER_MAP[(entity, mode.key)]
        workspace = get_workspace(self.job_db, str(batch["workspace_id"]))
        existing_keys = self.job_db.list_job_dedup_keys(str(batch["workspace_id"]))
        candidates, _ = resolve_fresh_candidates(
            resolver,
            entity,
            input_values[start:end],
            str(batch["source_kind"]),
            dict(payload.get("cms_config") or {}),
            mode,
            self.settings,
            workspace,
            str(batch["workspace_id"]),
            existing_keys,
        )
        jobs = self.job_db.create_jobs_bulk(
            candidates=candidates,
            workflow_key=str(batch["workflow_key"]),
            batch_id=str(batch["id"]),
            node_keys=list(definition.nodes),
            workspace_id=str(batch["workspace_id"]),
            revision=revision,
        )
        if entity == "video" and str(batch["workflow_key"]) == "video_knowledge":
            for candidate, job in zip(candidates, jobs, strict=True):
                write_video_input(resolve_job_dir(job, self.settings.jobs_dir), candidate)
        if self.job_event_buffer is not None and jobs:
            self.job_event_buffer.record_jobs_created(
                str(batch["workspace_id"]), [str(job["id"]) for job in jobs]
            )
        queue_state["next_index"] = end
        status = "completed" if end >= len(input_values) else "queued"
        self.job_db.update_intake_batch(
            str(batch["id"]),
            source_payload=payload,
            created_count=self.job_db.count_jobs_in_batch(str(batch["id"])),
            status=status,
        )
