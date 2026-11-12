from __future__ import annotations

import json
import logging
from typing import Any

from server.app.db.rowmap import wire_batch_id
from server.app.events import JobEventManager
from server.app.jobs import JobQueries
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.job_creation_pipeline import JobCreationPipeline
from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake_chunks import resolve_fresh_candidates
from server.app.services.job_intake_enqueue import enqueue_intake_batch
from server.app.services.job_intake_registry import RESOLVERS
from server.app.services.job_intake_resolution import normalize_values
from server.app.services.job_intake_workspace import (
    get_workspace,
    singular_field_name,
)
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import workflow_definition_from_dict

logger = logging.getLogger(__name__)


class JobIntakeService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
    ):
        self.job_db = job_db
        self.settings = settings
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer
        # #724: the creation sequence itself is shared with the /runs path
        # (and the async queue below) via JobCreationPipeline.
        self.pipeline = JobCreationPipeline(
            job_db,
            settings,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        )

    def create_batch(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = get_workspace(self.job_db, workspace_id)
        workflow_key = payload["workflow_key"]
        active_revision = self.job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if active_revision is None:
            raise InvalidOperationError(
                "Workspace has no active workflow revision; publish a workflow revision before intake"
            )
        definition = workflow_definition_from_dict(json.loads(active_revision["definition_json"]))
        mode = definition.intake.modes.get(payload["source_kind"]) if definition.intake else None
        if mode is None:
            raise InvalidOperationError("Unsupported intake mode")

        raw_values = payload.get(mode.input_field)
        if not isinstance(raw_values, list):
            raise InvalidOperationError(f"Unsupported input field: {mode.input_field}")
        input_values = normalize_values(raw_values)
        if not input_values:
            raise InvalidOperationError(
                f"At least one {singular_field_name(mode.input_field)} is required"
            )

        workspace_entity = str(workspace.get("default_entity") or "question")
        entity = (payload.get("entity") or workspace_entity).strip() or "question"
        spec = RESOLVERS.get((entity, mode.key))
        if spec is None:
            raise InvalidOperationError("Unsupported entity and intake mode combination")

        # Freeze the published custom code versions into the batch snapshot so
        # running jobs are immune to later edits (EXEC-CODE-002); empty when
        # every node is builtin or the feature gate is off. #724: config
        # resolution + freeze are the shared pipeline stage.
        node_config, node_code_versions = self.pipeline.resolve_node_setup(
            definition, workspace, workspace_id, workflow_key
        )

        if payload.get("async_processing"):
            return enqueue_intake_batch(
                self.job_db,
                workspace_id,
                payload,
                entity,
                input_values,
                mode,
                active_revision,
                node_config,
                node_code_versions,
            )

        # Filter candidates that already exist in this workflow so duplicates
        # are reported as created_count=0 instead of failing the whole batch.
        # The dedup key set is a lightweight projection that grows with every
        # accepted candidate, so intra-request duplicates across chunk
        # boundaries are filtered exactly like pre-existing jobs.
        existing_keys = self.job_db.list_job_dedup_keys(workspace_id, workflow_key)
        candidates, resolved_any = resolve_fresh_candidates(
            spec,
            entity,
            input_values,
            payload["source_kind"],
            mode,
            self.settings,
            workspace,
            workspace_id,
            existing_keys,
        )

        if not candidates:
            raise InvalidOperationError("No tasks were resolved from input")

        # Same per-run cap as the /runs API (workflows.max_items_per_run,
        # #358/#349 P0-1): intake is the other active run-creation path and
        # an unbounded question_ids list would hit the same memory and
        # transaction-length wall before the first job executes.
        max_items = self.settings.executor_runtime.workflows.max_items_per_run
        if max_items and len(candidates) > max_items:
            raise InvalidOperationError(
                f"Run items exceed the per-run limit: {len(candidates)} > {max_items}."
                " Split the submission into smaller runs (the limit is"
                " workflows.max_items_per_run in instance settings)."
            )

        if mode.input_field == "question_ids":
            resolved_ids = input_values
        elif mode.input_field == "knowledge_codes":
            resolved_ids = [
                candidate["entity_id"]
                for candidate in candidates
                if candidate["entity_type"] == entity
            ]
        else:
            resolved_ids = []

        knowledge_codes = input_values if mode.input_field == "knowledge_codes" else []
        source_payload = dict(payload)
        source_payload["entity"] = entity
        source_payload["question_ids"] = resolved_ids
        source_payload["knowledge_codes"] = knowledge_codes
        source_payload["node_config"] = node_config
        source_payload["node_code_versions"] = node_code_versions
        source_payload["intake_mode"] = {
            "key": mode.key,
            "label": mode.label,
            "input_field": mode.input_field,
        }
        source_payload["task_candidates"] = candidates
        # The payload only derives the deterministic run id; the authoritative
        # freeze lands on the run/jobs columns (RUN-FREEZE-001). #724: the
        # create_run → create_jobs_bulk → #501 compensation → notify sequence
        # is the shared pipeline; intake_error_contract keeps this path's
        # pinned contract (the original exception type survives the empty-run
        # branch — no ValueError → 400 conversion).
        batch, job_ids = self.pipeline.create_run_and_jobs(
            workspace_id=workspace_id,
            workflow_key=workflow_key,
            source_kind=payload["source_kind"],
            source_payload=source_payload,
            node_code_versions=node_code_versions,
            candidates=candidates,
            revision=active_revision,
            definition=definition,
            node_config=node_config,
            notify=notify_schedulable_work,
            intake_error_contract=True,
        )

        # Legacy wire shape still materializes job rows (the /runs path
        # dropped them, #467 A4); chunked facade read bounds the query.
        jobs = self.job_db.fetch_jobs_by_ids(job_ids)

        for job in jobs:
            job["storage_dir"] = str(resolve_job_dir(job, self.settings.jobs_dir))
            # Wire compatibility: API/SSE consumers still read ``batch_id``
            # (route renames are a later slice); the value is the run id.
            job["batch_id"] = wire_batch_id(job)

        batch["created_count"] = len(jobs)
        self.pipeline.record_jobs_created(workspace_id, [str(job["id"]) for job in jobs], jobs=jobs)
        return {"batch": batch, "created_count": len(jobs), "jobs": jobs}
