from __future__ import annotations

import json
import logging
from typing import Any

from server.app.events import JobEventManager
from server.app.jobs import JobQueries
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.agent_service import published_agent_definitions
from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake_chunks import resolve_fresh_candidates
from server.app.services.job_intake_enqueue import enqueue_intake_batch
from server.app.services.job_intake_registry import RESOLVERS
from server.app.services.job_intake_resolution import normalize_values
from server.app.services.job_intake_workspace import (
    enabled_intake_modes,
    get_workspace,
    singular_field_name,
)
from server.app.services.node_code_resolution import freeze_node_code_versions
from server.app.services.node_config import resolve_workflow_node_configs
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
        enabled_modes = enabled_intake_modes(workspace)
        if enabled_modes is not None and payload["source_kind"] not in enabled_modes:
            raise InvalidOperationError(
                "Intake mode is disabled for this workspace; "
                "configure enabled modes in workspace settings"
            )

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

        try:
            node_config = resolve_workflow_node_configs(
                definition,
                published_agent_definitions(self.settings.database_url, workspace_id),
                workspace,
            )
        except ValueError as exc:
            raise InvalidOperationError(f"Invalid node configuration: {exc}") from exc

        # Freeze the published custom code versions into the batch snapshot so
        # running jobs are immune to later edits (EXEC-CODE-002); empty when
        # every node is builtin or the feature gate is off.
        node_code_versions = freeze_node_code_versions(
            self.job_db.path,
            self.settings.executor_runtime.workflows.custom_nodes_enabled,
            workspace_id,
            workflow_key,
            [node.key for node in definition.nodes.values()],
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

        # Filter candidates that already exist in the workspace so duplicates are
        # reported as created_count=0 instead of failing the whole batch. The
        # dedup key set is a lightweight projection that grows with every
        # accepted candidate, so intra-request duplicates across chunk
        # boundaries are filtered exactly like pre-existing jobs.
        existing_keys = self.job_db.list_job_dedup_keys(workspace_id)
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
        batch = self.job_db.create_batch(
            workflow_key,
            payload["source_kind"],
            source_payload,
            workspace_id=workspace_id,
        )
        jobs = self.job_db.create_jobs_bulk(
            candidates=candidates,
            workflow_key=workflow_key,
            batch_id=batch["id"],
            node_keys=list(definition.nodes),
            workspace_id=workspace_id,
            revision=active_revision,
        )
        if jobs:
            notify_schedulable_work()

        for job in jobs:
            job["storage_dir"] = str(resolve_job_dir(job, self.settings.jobs_dir))

        batch["created_count"] = len(jobs)
        if self.job_event_buffer is not None:
            self.job_event_buffer.record_jobs_created(
                workspace_id, [str(job["id"]) for job in jobs]
            )
        elif self.job_event_manager is not None:
            stats = self.job_db.count_jobs_by_status(workspace_id)
            self.job_event_manager.broadcast_jobs_created(workspace_id, jobs, stats)
        return {"batch": batch, "created_count": len(jobs), "jobs": jobs}
