from __future__ import annotations

import json
import logging
from typing import Any

from server.app.events import JobEventManager
from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake_resolution import RESOLVER_MAP, normalize_values
from server.app.services.job_intake_resolver import resolve_candidates
from server.app.services.job_intake_video import (
    exclude_existing_candidates,
    write_video_input,
)
from server.app.services.job_intake_workspace import (
    check_resource_enabled,
    effective_cms_config,
    enabled_intake_modes,
    get_workspace,
    singular_field_name,
)
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import workflow_definition_from_dict

logger = logging.getLogger(__name__)


class JobIntakeService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        workflows: WorkflowCatalogService,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
    ):
        self.job_db = job_db
        self.settings = settings
        self.workflows = workflows
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
        resource_key = mode.resource if mode.resource else None
        if resource_key:
            check_resource_enabled(workspace, resource_key)
        cms_config = effective_cms_config(self.settings, workspace)
        resource_config = workspace.get("resource_config")
        if not isinstance(resource_config, dict):
            resource_config = {}
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
        if entity == "video" and workflow_key != "video_knowledge":
            raise InvalidOperationError("Unsupported entity and intake mode combination")
        resolver = RESOLVER_MAP.get((entity, mode.key))
        if resolver is None:
            raise InvalidOperationError("Unsupported entity and intake mode combination")

        candidates = resolve_candidates(
            resolver,
            entity,
            input_values,
            payload["source_kind"],
            cms_config,
            mode,
            self.settings,
            workspace,
            workspace_id,
        )

        # Filter candidates that already exist in the workspace so duplicates are
        # reported as created_count=0 instead of failing the whole batch.
        original_candidates = candidates
        existing_jobs = self.job_db.list_jobs(workspace_id=workspace_id)
        existing_keys = {(str(job["source_type"]), str(job["source_id"])) for job in existing_jobs}
        candidates = exclude_existing_candidates(candidates, existing_keys)

        if not candidates:
            if entity == "video" and original_candidates:
                return {"created_count": 0, "jobs": []}
            detail = "No tasks were resolved from input"
            if resolver.startswith("cms.") and mode.input_field == "knowledge_codes":
                detail += f". Checked {len(input_values)} knowledge code(s) via CMS; ensure the codes are correct and the resource API URL is configured."
            raise InvalidOperationError(detail)

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
        source_payload["cms_config"] = cms_config
        source_payload["resource_config"] = resource_config
        source_payload["intake_mode"] = {
            "key": mode.key,
            "label": mode.label,
            "input_field": mode.input_field,
            "resource": mode.resource,
        }
        source_payload["task_candidates"] = candidates
        batch = self.job_db.create_batch(
            workflow_key,
            payload["source_kind"],
            source_payload,
            workspace_id=workspace_id,
        )
        jobs: list[dict[str, Any]] = []
        for candidate in candidates:
            jobs.append(
                self.job_db.create_job(
                    workflow_key=workflow_key,
                    source_type=str(candidate["entity_type"]),
                    source_id=str(candidate["entity_id"]),
                    batch_id=batch["id"],
                    title=str(candidate["title"]),
                    node_keys=list(definition.nodes),
                    workspace_id=workspace_id,
                    stem=str(candidate.get("stem", "")),
                    workflow_revision_id=active_revision["id"],
                    workflow_version=int(active_revision["version"]),
                    workflow_definition_hash=active_revision["definition_hash"],
                    workflow_definition_snapshot_json=active_revision["definition_json"],
                )
            )

        if entity == "video" and workflow_key == "video_knowledge":
            for candidate, job in zip(candidates, jobs, strict=True):
                write_video_input(resolve_job_dir(job, self.settings.jobs_dir), candidate)

        resolved_jobs: list[dict[str, Any]] = []
        for job in jobs:
            projected = dict(job)
            projected["storage_dir"] = str(resolve_job_dir(projected, self.settings.jobs_dir))
            resolved_jobs.append(projected)
        jobs = resolved_jobs

        batch["created_count"] = len(jobs)
        if self.job_event_buffer is not None:
            for job in jobs:
                self.job_event_buffer.record_job_created(workspace_id, str(job["id"]))
        elif self.job_event_manager is not None:
            stats = self.job_db.count_jobs_by_status(workspace_id)
            self.job_event_manager.broadcast_jobs_created(workspace_id, jobs, stats)
        return {"batch": batch, "created_count": len(jobs), "jobs": jobs}
