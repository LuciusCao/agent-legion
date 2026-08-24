from __future__ import annotations

import json
import logging
from typing import Any

from server.app.jobs import JobQueries
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.job_intake_chunks import resolve_fresh_candidates
from server.app.services.job_intake_registry import RESOLVERS, ResolverSpec
from server.app.services.job_intake_workspace import get_workspace
from server.app.settings import Settings
from server.app.workflows.definition import workflow_definition_from_dict

logger = logging.getLogger(__name__)
INTAKE_QUEUE_CHUNK_SIZE = 25
MAX_CHUNK_ERRORS = 20


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
        batch = self.job_db.claim_intake_run()
        if batch is None:
            return False
        try:
            self._consume_chunk(batch)
        except Exception as exc:
            logger.exception("async intake batch %s failed", batch["id"])
            self.job_db.update_intake_run(str(batch["id"]), status="failed", error_message=str(exc))
        return True

    def _consume_chunk(self, batch: dict[str, Any]) -> None:
        payload = json.loads(str(batch["queue_payload_json"]))
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
        spec = RESOLVERS[(entity, mode.key)]
        workspace = get_workspace(self.job_db, str(batch["workspace_id"]))
        try:
            self._create_chunk_jobs(
                batch,
                payload,
                definition,
                mode,
                entity,
                spec,
                workspace,
                revision,
                input_values[start:end],
            )
        except Exception as exc:
            # A bad chunk (CMS outage, malformed value, ...) must not fail the
            # whole batch: record the error, skip these values, and let the
            # remaining chunks proceed. Jobs already committed by earlier
            # chunks stay; the failed chunk's values can be re-submitted in a
            # new batch (dedup keys filter already-created jobs).
            logger.exception("async intake batch %s chunk [%s:%s] failed", batch["id"], start, end)
            chunk_errors = queue_state.setdefault("chunk_errors", [])
            if len(chunk_errors) < MAX_CHUNK_ERRORS:
                chunk_errors.append(
                    {
                        "chunk_start": start,
                        "values": input_values[start : min(end, start + 5)],
                        "error": str(exc)[:300],
                    }
                )
        queue_state["next_index"] = end
        chunk_errors = queue_state.get("chunk_errors") or []
        error_message = "; ".join(
            f"chunk {error['chunk_start']}: {error['error']}" for error in chunk_errors[-5:]
        )[:1000]
        status = "completed" if end >= len(input_values) else "queued"
        self.job_db.update_intake_run(
            str(batch["id"]),
            queue_payload=payload,
            created_count=self.job_db.count_jobs_in_run(str(batch["id"])),
            status=status,
            error_message=error_message,
        )

    def _create_chunk_jobs(
        self,
        batch: dict[str, Any],
        payload: dict[str, Any],
        definition: Any,
        mode: Any,
        entity: str,
        spec: ResolverSpec,
        workspace: dict[str, Any],
        revision: dict[str, Any],
        values: list[str],
    ) -> None:
        existing_keys = self.job_db.list_job_dedup_keys(
            str(batch["workspace_id"]), str(batch["workflow_key"])
        )
        candidates, _ = resolve_fresh_candidates(
            spec,
            entity,
            values,
            str(batch["source_kind"]),
            mode,
            self.settings,
            workspace,
            str(batch["workspace_id"]),
            existing_keys,
        )
        jobs = self.job_db.create_jobs_bulk(
            candidates=candidates,
            workflow_key=str(batch["workflow_key"]),
            run_id=str(batch["id"]),
            node_keys=list(definition.nodes),
            workspace_id=str(batch["workspace_id"]),
            revision=revision,
            frozen_config=payload.get("node_config") or {},
        )
        if jobs:
            notify_schedulable_work()
        if self.job_event_buffer is not None and jobs:
            self.job_event_buffer.record_jobs_created(
                str(batch["workspace_id"]), [str(job["id"]) for job in jobs]
            )
