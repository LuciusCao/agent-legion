import logging
from typing import Any

from server.app.events import JobEventManager
from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.job_intake_resolution import (
    RESOLVER_MAP,
    normalize_values,
    resolve_cms_question_candidates,
    resolve_direct_candidates,
    singular_field_name,
)
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir

logger = logging.getLogger(__name__)


class JobIntakeService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        workflows: WorkflowCatalogService,
        job_event_manager: JobEventManager | None = None,
    ):
        self.job_db = job_db
        self.settings = settings
        self.workflows = workflows
        self.job_event_manager = job_event_manager

    def _workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.job_db.get_workspace(workspace_id)
        if workspace is None:
            raise NotFoundError("Workspace not found")
        return workspace

    def _effective_cms_config(self, workspace: dict[str, Any]) -> dict[str, Any]:
        base = self.settings.config.get("cms", {})
        config = dict(base) if isinstance(base, dict) else {}
        workspace_config = workspace.get("cms_config")
        if isinstance(workspace_config, dict):
            config.update(workspace_config)
        return config

    def _enabled_intake_modes(self, workspace: dict[str, Any]) -> set[str] | None:
        intake_config = workspace.get("intake_config")
        if not isinstance(intake_config, dict) or "enabled_modes" not in intake_config:
            return None
        enabled_modes = intake_config.get("enabled_modes")
        if not isinstance(enabled_modes, list):
            return None
        return {str(mode) for mode in enabled_modes}

    def create_batch(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        workflow_key = payload["workflow_key"]
        definition = self.workflows.definition(workflow_key)
        intake_mode = (
            definition.intake.modes.get(payload["source_kind"]) if definition.intake else None
        )
        resource_key = intake_mode.resource if intake_mode else None
        if resource_key:
            ws_resource_config = workspace.get("resource_config") or {}
            resources = ws_resource_config.get("resources") or {}
            binding = resources.get(resource_key) or {}
            if binding.get("enabled") is False:
                raise InvalidOperationError(
                    f"Resource provider '{resource_key}' is disabled for this workspace"
                )
        cms_config = self._effective_cms_config(workspace)
        resource_config = workspace.get("resource_config")
        if not isinstance(resource_config, dict):
            resource_config = {}
        mode = definition.intake.modes.get(payload["source_kind"]) if definition.intake else None
        if mode is None:
            raise InvalidOperationError("Unsupported intake mode")
        enabled_modes = self._enabled_intake_modes(workspace)
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
        resolver = RESOLVER_MAP.get((entity, mode.key))
        if resolver is None:
            raise InvalidOperationError("Unsupported entity and intake mode combination")

        candidates: list[dict[str, Any]] = []
        if resolver.startswith("direct."):
            candidates = resolve_direct_candidates(entity, input_values, payload["source_kind"])
        elif resolver.startswith("cms."):
            candidates = resolve_cms_question_candidates(
                entity,
                input_values,
                payload["source_kind"],
                resolver,
                mode,
                self.settings,
                workspace,
                workspace_id,
            )
        else:
            raise InvalidOperationError(f"Unsupported resolver: {resolver}")

        if not candidates:
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
                )
            )

        resolved_jobs: list[dict[str, Any]] = []
        for job in jobs:
            projected = dict(job)
            projected["storage_dir"] = str(resolve_job_dir(projected, self.settings.jobs_dir))
            resolved_jobs.append(projected)
        jobs = resolved_jobs

        batch["created_count"] = len(jobs)
        if self.job_event_manager is not None:
            stats = self.job_db.count_jobs_by_status(workspace_id)
            self.job_event_manager.broadcast_jobs_created(workspace_id, jobs, stats)
        return {"batch": batch, "created_count": len(jobs), "jobs": jobs}
