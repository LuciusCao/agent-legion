import logging
from typing import Any

from server.app.cms.client import get_token
from server.app.cms.question import list_questions_by_knowledge
from server.app.events import JobEventManager
from server.app.jobs import JobQueries
from server.app.services.job_errors import (
    InvalidOperationError,
    NotFoundError,
    UnsupportedOperationError,
)
from server.app.services.pipeline_catalog import PipelineCatalogService
from server.app.settings import Settings
from server.app.workflows.resources import resolve_cms_resource

logger = logging.getLogger(__name__)

RESOLVER_MAP: dict[tuple[str, str], str] = {
    ("question", "direct_ids"): "direct.question_ids",
    ("question", "by_knowledge"): "cms.questions_by_knowledge",
    ("question", "batch_by_ids"): "direct.question_ids",
    ("question", "batch_by_knowledge"): "cms.questions_by_knowledge",
    ("video", "direct_ids"): "direct.video_ids",
    ("video", "by_knowledge"): "cms.videos_by_knowledge",
}


def _candidate(
    entity_type: str,
    entity_id: str,
    title: str,
    source_kind: str,
    source_value: str,
    stem: str = "",
) -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "title": title,
        "stem": stem,
        "source": {"kind": source_kind, "value": source_value},
    }


def _normalize_values(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _singular_field_name(value: str) -> str:
    if value.endswith("ies"):
        return f"{value[:-3]}y"
    if value.endswith("s"):
        return value[:-1]
    return value


class JobIntakeService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        pipelines: PipelineCatalogService,
        job_event_manager: JobEventManager | None = None,
    ):
        self.job_db = job_db
        self.settings = settings
        self.pipelines = pipelines
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
        definition = self.pipelines.definition(payload["pipeline_key"])
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
            raise InvalidOperationError("Intake mode is disabled for this workspace")

        raw_values = payload.get(mode.input_field)
        if not isinstance(raw_values, list):
            raise InvalidOperationError(f"Unsupported input field: {mode.input_field}")
        input_values = _normalize_values(raw_values)
        if not input_values:
            raise InvalidOperationError(
                f"At least one {_singular_field_name(mode.input_field)} is required"
            )

        workspace_entity = str(workspace.get("default_entity") or "question")
        entity = (payload.get("entity") or workspace_entity).strip() or "question"
        resolver = RESOLVER_MAP.get((entity, mode.key))
        if resolver is None:
            raise InvalidOperationError("Unsupported entity and intake mode combination")

        candidates: list[dict[str, Any]] = []
        if resolver.startswith("direct."):
            candidates = [
                _candidate(
                    entity,
                    value,
                    f"{entity.title()} {value}",
                    payload["source_kind"],
                    value,
                )
                for value in input_values
            ]
        elif resolver.startswith("cms."):
            if entity != "question":
                raise UnsupportedOperationError(f"{entity} resolver not yet implemented")
            list_resource = resolve_cms_resource(
                self.settings.config,
                workspace,
                None,
                mode.resource,
            )
            api_url = list_resource.get("api_url") or list_resource.get("question_list_url")
            logger.info(
                "CMS lookup for workspace=%s mode=%s: api_url=%s resource=%s",
                workspace_id,
                mode.key,
                api_url,
                mode.resource,
            )
            token = get_token(str(list_resource.get("env", "")), list_resource)
            seen_question_ids: set[str] = set()
            for knowledge_code in input_values:
                summaries = list_questions_by_knowledge(
                    knowledge_code,
                    api_url,
                    token,
                )
                logger.info(
                    "CMS returned %d questions for knowledge_code=%s",
                    len(summaries),
                    knowledge_code,
                )
                for summary in summaries:
                    if summary.question_id in seen_question_ids:
                        continue
                    seen_question_ids.add(summary.question_id)
                    stem = ""
                    body = summary.payload.get("body")
                    if isinstance(body, dict):
                        stem = str(body.get("content") or "").strip()
                    candidates.append(
                        _candidate(
                            entity,
                            summary.question_id,
                            summary.title or f"Question {summary.question_id}",
                            "knowledge_code",
                            knowledge_code,
                            stem=stem,
                        )
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
        # TODO(Task C): remove mapping once API contracts rename pipeline_key -> workflow_key
        batch = self.job_db.create_batch(
            payload["pipeline_key"],
            payload["source_kind"],
            source_payload,
            workspace_id=workspace_id,
        )
        jobs: list[dict[str, Any]] = []
        for candidate in candidates:
            jobs.append(
                self.job_db.create_job(
                    # TODO(Task C): remove mapping once API contracts rename pipeline_key -> workflow_key
                    workflow_key=payload["pipeline_key"],
                    source_type=str(candidate["entity_type"]),
                    source_id=str(candidate["entity_id"]),
                    batch_id=batch["id"],
                    title=str(candidate["title"]),
                    node_keys=list(definition.nodes),
                    workspace_id=workspace_id,
                    stem=str(candidate.get("stem", "")),
                )
            )

        batch["created_count"] = len(jobs)
        if self.job_event_manager is not None:
            stats = self.job_db.count_jobs_by_status(workspace_id)
            self.job_event_manager.broadcast_jobs_created(workspace_id, jobs, stats)
        return {"batch": batch, "created_count": len(jobs), "jobs": jobs}
