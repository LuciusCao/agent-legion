from __future__ import annotations

import glob
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.app.agents import AgentStatusManager
from server.app.cms.client import get_token
from server.app.cms.question import list_questions_by_knowledge
from server.app.jobs import JobQueries
from server.app.pipelines.artifacts import clear_rerun_outputs
from server.app.pipelines.definition import PipelineDefinition
from server.app.pipelines.registry import list_registered_pipelines, load_registered_pipeline
from server.app.pipelines.resources import (
    RESOURCE_PARAM_KEYS,
    RESOURCE_PROVIDERS,
    resolve_cms_resource,
)
from server.app.pipelines.scheduler import downstream_nodes
from server.app.settings import Settings

RESOLVER_MAP: dict[tuple[str, str], str] = {
    ("question", "direct_ids"): "direct.question_ids",
    ("question", "by_knowledge"): "cms.questions_by_knowledge",
    ("question", "batch_by_ids"): "direct.question_ids",
    ("question", "batch_by_knowledge"): "cms.questions_by_knowledge",
    ("video", "direct_ids"): "direct.video_ids",
    ("video", "by_knowledge"): "cms.videos_by_knowledge",
}

logger = logging.getLogger(__name__)


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


class JobBatchRequest(BaseModel):
    pipeline_key: str = "reading_analysis"
    entity: str | None = None
    source_kind: str
    question_ids: list[str] = Field(default_factory=list)
    knowledge_codes: list[str] = Field(default_factory=list)


class JobBatchResponse(BaseModel):
    batch: dict[str, Any]
    created_count: int
    jobs: list[dict[str, Any]]


class JobsResponse(BaseModel):
    jobs: list[dict[str, Any]]


class PipelineResponse(BaseModel):
    pipeline: dict[str, Any]


class PipelinesListResponse(BaseModel):
    pipelines: list[dict[str, Any]]


class WorkspaceCreateRequest(BaseModel):
    name: str
    default_pipeline_key: str = "reading_analysis"
    default_entity: str = "question"
    cms_config: dict[str, Any] = Field(default_factory=dict)
    resource_config: dict[str, Any] = Field(default_factory=dict)
    intake_config: dict[str, Any] = Field(default_factory=dict)
    pipeline_config: dict[str, Any] = Field(default_factory=dict)


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    default_pipeline_key: str | None = None
    default_entity: str | None = None
    cms_config: dict[str, Any] | None = None
    resource_config: dict[str, Any] | None = None
    intake_config: dict[str, Any] | None = None
    pipeline_config: dict[str, Any] | None = None


class WorkspaceSettingsResponse(BaseModel):
    settings: dict[str, Any]


class WorkspaceSettingsSectionRequest(BaseModel):
    cmsUrl: str | None = None
    cmsToken: str | None = None
    entityType: str | None = None
    intakeModes: list[str] | None = None
    labelOverrides: dict[str, str] | None = None
    pipelineKey: str | None = None
    localConcurrency: int | None = None
    agentConcurrency: int | None = None
    nodeLocalConcurrency: dict[str, int] | None = None
    resources: dict[str, Any] | None = None


class WorkspaceSettingsTestResponse(BaseModel):
    ok: bool
    message: str


class WorkspaceResponse(BaseModel):
    workspace: dict[str, Any]


class WorkspacesResponse(BaseModel):
    workspaces: list[dict[str, Any]]


class WorkspaceAgentsResponse(BaseModel):
    agents: list[dict[str, Any]]


class JobDetailResponse(BaseModel):
    job: dict[str, Any]
    nodes: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    artifacts: list[str]


class ArtifactResponse(BaseModel):
    name: str
    content: str


class RerunNodeResponse(BaseModel):
    job_id: str
    node_key: str
    stale_nodes: list[str]


class BatchJobRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class BatchJobResponse(BaseModel):
    results: list[dict[str, Any]]


class WorkspaceRunsResponse(BaseModel):
    runs: list[dict[str, Any]]


class WorkspaceDagResponse(BaseModel):
    pipeline: dict[str, Any]
    nodes: list[dict[str, Any]]


class WorkspaceAgentConfig(BaseModel):
    agent_id: str
    concurrency_limit: int


class WorkspaceAgentStatus(BaseModel):
    id: str
    name: str
    busy: bool


class WorkspaceStatsResponse(BaseModel):
    workspace_id: str
    name: str
    pipeline_key: str
    pipeline_label: str
    job_stats: dict[str, int]
    agent_status: dict[str, Any]
    latest_run: dict[str, Any] | None


class DeleteWorkspaceResponse(BaseModel):
    deleted: str


class ResourceProvidersResponse(BaseModel):
    providers: list[dict[str, Any]]


class GlobalServicesResponse(BaseModel):
    cms: dict[str, Any]


def _pipelines_enabled(settings: Settings) -> bool:
    pipelines = settings.config.get("pipelines", {})
    return isinstance(pipelines, dict) and bool(pipelines.get("enabled"))


def _require_enabled(settings: Settings) -> None:
    if not _pipelines_enabled(settings):
        raise HTTPException(status_code=404, detail="Pipelines are disabled")


def _definition(settings: Settings, pipeline_key: str) -> PipelineDefinition:
    try:
        return load_registered_pipeline(settings.root_dir, pipeline_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown pipeline") from exc


def _workspace_or_404(job_db: JobQueries, workspace_id: str) -> dict[str, Any]:
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def _effective_cms_config(settings: Settings, workspace: dict[str, Any]) -> dict[str, Any]:
    base = settings.config.get("cms", {})
    config = dict(base) if isinstance(base, dict) else {}
    workspace_config = workspace.get("cms_config")
    if isinstance(workspace_config, dict):
        config.update(workspace_config)
    return config


def _normalize_values(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _singular_field_name(value: str) -> str:
    if value.endswith("ies"):
        return f"{value[:-3]}y"
    if value.endswith("s"):
        return value[:-1]
    return value


def _artifact_path(job: dict[str, Any], artifact_name: str) -> Path:
    if "/" in artifact_name or "\\" in artifact_name or artifact_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid artifact name")

    base = Path(str(job["storage_dir"])).resolve()
    path = (base / artifact_name).resolve()
    if path.parent != base:
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    return path


def _job_has_running_nodes(job_db: JobQueries, job_id: str) -> bool:
    return any(node["status"] == "running" for node in job_db.list_job_nodes(job_id))


def _artifact_names(job: dict[str, Any]) -> list[str]:
    base = Path(str(job["storage_dir"]))
    if not base.exists():
        return []
    return sorted(path.name for path in base.iterdir() if path.is_file())


def _pipeline_label(settings: Settings, pipeline_key: str) -> str:
    return _definition(settings, pipeline_key).label


def _url_to_params(url: str) -> dict[str, str]:
    from urllib.parse import parse_qsl, urlparse

    parsed = urlparse(url)
    return {k: v for k, v in parse_qsl(parsed.query) if v not in (None, "")}


def _resource_provider_payload(settings: Settings) -> list[dict[str, Any]]:
    providers_config = settings.config.get("resource_providers")
    if not isinstance(providers_config, dict):
        return []
    cms_config = settings.config.get("cms", {}) or {}

    result: list[dict[str, Any]] = []
    for key, meta in RESOURCE_PROVIDERS.items():
        provider = str(meta.get("provider") or "")
        provider_config = providers_config.get(provider) or {}
        path = str(provider_config.get("path", ""))
        param_keys = list(RESOURCE_PARAM_KEYS)
        if key == "question_detail" and "page_size" in param_keys:
            param_keys.remove("page_size")
        default_params: dict[str, str] = {}
        for param_key in param_keys:
            if param_key in cms_config and cms_config[param_key] not in (None, ""):
                default_params[param_key] = str(cms_config[param_key])
        result.append(
            {
                "key": key,
                "provider": provider,
                "path": path,
                "defaultParams": default_params,
                "paramKeys": param_keys,
            }
        )
    return result


def _mask_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    parts = hostname.split(".")
    if len(parts) >= 3:
        masked = f"{parts[0]}.***.{parts[-1]}"
    elif len(parts) == 2:
        masked = f"***.{parts[1]}"
    else:
        masked = hostname
    return f"{parsed.scheme}://{masked}{parsed.path}"


def _token_available(cms_config: dict[str, Any]) -> bool:
    import os

    if cms_config.get("token"):
        return True
    if os.environ.get("BASECMS_TOKEN"):
        return True
    token_gen = cms_config.get("token_gen") or {}
    if all(token_gen.get(k) for k in ("app_id", "nonce", "secret", "url")):
        return True
    return all(
        os.environ.get(env_key)
        for env_key in ("BASECMS_APP_ID", "BASECMS_NONCE", "BASECMS_SECRET", "BASECMS_TOKEN_URL")
    )


def _global_services_payload(settings: Settings) -> dict[str, Any]:
    cms_config = settings.config.get("cms", {}) or {}
    base_url = str(cms_config.get("base_url", ""))
    return {
        "cms": {
            "baseUrl": _mask_url(base_url) if base_url else "",
            "tokenConfigured": _token_available(cms_config),
            "env": str(cms_config.get("env", "")),
            "healthy": None,
            "lastCheckedAt": None,
        }
    }


def _enabled_intake_modes(workspace: dict[str, Any]) -> set[str] | None:
    intake_config = workspace.get("intake_config")
    if not isinstance(intake_config, dict) or "enabled_modes" not in intake_config:
        return None
    enabled_modes = intake_config.get("enabled_modes")
    if not isinstance(enabled_modes, list):
        return None
    return {str(mode) for mode in enabled_modes}


def _pipeline_payload(settings: Settings, pipeline_key: str) -> dict[str, Any]:
    definition = _definition(settings, pipeline_key)
    nodes: list[dict[str, Any]] = []
    for node in definition.nodes.values():
        node_payload: dict[str, Any] = {
            "key": node.key,
            "label": node.label,
            "runner": node.runner,
            "after": node.after,
            "inputs": node.inputs,
            "outputs": node.outputs,
        }
        if node.agent is not None:
            node_payload["agent"] = {
                "engine": node.agent.engine,
                "skill": node.agent.skill,
                "tools": node.agent.tools,
            }
        nodes.append(node_payload)
    return {
        "key": definition.key,
        "label": definition.label,
        "concurrency": {
            "local": definition.concurrency.local,
            "agent": definition.concurrency.agent,
            "nodes": definition.concurrency.nodes,
        },
        "intake": {
            "modes": [
                {
                    "key": mode.key,
                    "label": mode.label,
                    "input_field": mode.input_field,
                    "resource": mode.resource,
                }
                for mode in definition.intake.modes.values()
            ]
        },
        "nodes": nodes,
    }


def _workspace_settings_payload(
    workspace: dict[str, Any], settings: Settings, job_db: JobQueries
) -> dict[str, Any]:
    intake_config = workspace.get("intake_config")
    if not isinstance(intake_config, dict):
        intake_config = {}
    enabled_modes = intake_config.get("enabled_modes")
    label_overrides = intake_config.get("label_overrides")
    resource_config = workspace.get("resource_config")
    if not isinstance(resource_config, dict):
        resource_config = {}
    resources = resource_config.get("resources")
    if not isinstance(resources, dict):
        resources = {}
    pipeline_config = workspace.get("pipeline_config")
    if not isinstance(pipeline_config, dict):
        pipeline_config = {}
    definition = _definition(
        settings, str(workspace.get("default_pipeline_key") or "question_content")
    )
    assignments = job_db.list_workspace_agents(str(workspace.get("id") or ""))
    return {
        "entityType": str(workspace.get("default_entity") or "question"),
        "intakeModes": enabled_modes if isinstance(enabled_modes, list) else [],
        "labelOverrides": label_overrides if isinstance(label_overrides, dict) else {},
        "pipelineKey": str(workspace.get("default_pipeline_key") or "question_content"),
        "agentIds": [a["agent_id"] for a in assignments],
        "concurrencyLimit": max((a["concurrency_limit"] for a in assignments), default=1),
        "resources": resources,
        "localConcurrency": pipeline_config.get("local", definition.concurrency.local),
        "agentConcurrency": pipeline_config.get("agent", definition.concurrency.agent),
        "nodeLocalConcurrency": pipeline_config.get("nodes", {}),
    }


def _job_nodes_with_definition(
    job_db: JobQueries, settings: Settings, job: dict[str, Any]
) -> list[dict[str, Any]]:
    definition = _definition(settings, str(job["pipeline_key"]))
    nodes = job_db.list_job_nodes(str(job["id"]))
    return [
        {
            **node,
            "label": definition.nodes[node["node_key"]].label
            if node["node_key"] in definition.nodes
            else node["node_key"],
            "after": definition.nodes[node["node_key"]].after
            if node["node_key"] in definition.nodes
            else [],
        }
        for node in nodes
    ]


def create_jobs_router(
    job_db: JobQueries, settings: Settings, agent_manager: AgentStatusManager
) -> APIRouter:
    router = APIRouter()

    def create_batch_for_workspace(
        workspace_id: str,
        payload: JobBatchRequest,
    ) -> JobBatchResponse:
        _require_enabled(settings)
        workspace = _workspace_or_404(job_db, workspace_id)
        definition = _definition(settings, payload.pipeline_key)
        intake_mode = (
            definition.intake.modes.get(payload.source_kind) if definition.intake else None
        )
        resource_key = intake_mode.resource if intake_mode else None
        if resource_key:
            ws_resource_config = workspace.get("resource_config") or {}
            resources = ws_resource_config.get("resources") or {}
            binding = resources.get(resource_key) or {}
            if binding.get("enabled") is False:
                raise HTTPException(
                    status_code=400,
                    detail=f"Resource provider '{resource_key}' is disabled for this workspace",
                )
        cms_config = _effective_cms_config(settings, workspace)
        resource_config = workspace.get("resource_config")
        if not isinstance(resource_config, dict):
            resource_config = {}
        mode = definition.intake.modes.get(payload.source_kind) if definition.intake else None
        if mode is None:
            raise HTTPException(status_code=400, detail="Unsupported intake mode")
        enabled_modes = _enabled_intake_modes(workspace)
        if enabled_modes is not None and payload.source_kind not in enabled_modes:
            raise HTTPException(
                status_code=400,
                detail="Intake mode is disabled for this workspace",
            )

        raw_values = getattr(payload, mode.input_field, None)
        if not isinstance(raw_values, list):
            raise HTTPException(
                status_code=400, detail=f"Unsupported input field: {mode.input_field}"
            )
        input_values = _normalize_values(raw_values)
        if not input_values:
            raise HTTPException(
                status_code=400,
                detail=f"At least one {_singular_field_name(mode.input_field)} is required",
            )

        workspace_entity = str(workspace.get("default_entity") or "question")
        entity = (payload.entity or workspace_entity).strip() or "question"
        resolver = RESOLVER_MAP.get((entity, mode.key))
        if resolver is None:
            raise HTTPException(
                status_code=400,
                detail="Unsupported entity and intake mode combination",
            )

        candidates: list[dict[str, Any]] = []
        if resolver.startswith("direct."):
            candidates = [
                _candidate(
                    entity,
                    value,
                    f"{entity.title()} {value}",
                    payload.source_kind,
                    value,
                )
                for value in input_values
            ]
        elif resolver.startswith("cms."):
            if entity != "question":
                raise HTTPException(
                    status_code=501,
                    detail=f"{entity} resolver not yet implemented",
                )
            list_resource = resolve_cms_resource(
                settings.config,
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
            raise HTTPException(status_code=400, detail=f"Unsupported resolver: {resolver}")

        if not candidates:
            detail = "No tasks were resolved from input"
            if resolver.startswith("cms.") and mode.input_field == "knowledge_codes":
                detail += f". Checked {len(input_values)} knowledge code(s) via CMS; ensure the codes are correct and the resource API URL is configured."
            raise HTTPException(status_code=400, detail=detail)

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
        source_payload = payload.model_dump()
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
        batch = job_db.create_batch(
            payload.pipeline_key,
            payload.source_kind,
            source_payload,
            workspace_id=workspace_id,
        )
        jobs: list[dict[str, Any]] = []
        for candidate in candidates:
            jobs.append(
                job_db.create_job(
                    pipeline_key=payload.pipeline_key,
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
        return JobBatchResponse(batch=batch, created_count=len(jobs), jobs=jobs)

    @router.get("/resource-providers", response_model=ResourceProvidersResponse)
    def get_resource_providers() -> ResourceProvidersResponse:
        _require_enabled(settings)
        return ResourceProvidersResponse(providers=_resource_provider_payload(settings))

    @router.get("/global-services", response_model=GlobalServicesResponse)
    def get_global_services() -> GlobalServicesResponse:
        return GlobalServicesResponse(**_global_services_payload(settings))

    @router.get("/pipelines", response_model=PipelinesListResponse)
    def list_pipelines() -> PipelinesListResponse:
        _require_enabled(settings)
        pipelines = []
        for definition in list_registered_pipelines(settings.root_dir):
            pipelines.append(
                {
                    "key": definition.key,
                    "label": definition.label,
                    "concurrency": {
                        "local": definition.concurrency.local,
                        "agent": definition.concurrency.agent,
                        "nodes": definition.concurrency.nodes,
                    },
                }
            )
        return PipelinesListResponse(pipelines=pipelines)

    @router.get("/pipelines/{pipeline_key}", response_model=PipelineResponse)
    def get_pipeline(pipeline_key: str) -> PipelineResponse:
        _require_enabled(settings)
        return PipelineResponse(pipeline=_pipeline_payload(settings, pipeline_key))

    @router.get("/workspaces", response_model=WorkspacesResponse)
    def list_workspaces() -> WorkspacesResponse:
        _require_enabled(settings)
        return WorkspacesResponse(workspaces=job_db.list_workspaces())

    @router.post("/workspaces", response_model=WorkspaceResponse)
    def create_workspace(payload: WorkspaceCreateRequest) -> WorkspaceResponse:
        _require_enabled(settings)
        _definition(settings, payload.default_pipeline_key)
        try:
            workspace = job_db.create_workspace(
                payload.name,
                default_pipeline_key=payload.default_pipeline_key,
                default_entity=payload.default_entity,
                cms_config=payload.cms_config,
                resource_config=payload.resource_config,
                intake_config=payload.intake_config,
                pipeline_config=payload.pipeline_config,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkspaceResponse(workspace=workspace)

    @router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def get_workspace(workspace_id: str) -> WorkspaceResponse:
        _require_enabled(settings)
        return WorkspaceResponse(workspace=_workspace_or_404(job_db, workspace_id))

    @router.get("/workspaces/{workspace_id}/agents")
    def get_workspace_agents(
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        return job_db.list_workspace_agents(workspace_id)

    @router.post("/workspaces/{workspace_id}/agents")
    def set_workspace_agent(
        workspace_id: str,
        config: WorkspaceAgentConfig,
    ) -> dict[str, Any]:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        return job_db.upsert_workspace_agent_assignment(
            workspace_id, config.agent_id, config.concurrency_limit
        )

    @router.get("/workspaces/{workspace_id}/settings", response_model=WorkspaceSettingsResponse)
    def get_workspace_settings(workspace_id: str) -> WorkspaceSettingsResponse:
        _require_enabled(settings)
        workspace = _workspace_or_404(job_db, workspace_id)
        return WorkspaceSettingsResponse(
            settings=_workspace_settings_payload(workspace, settings, job_db)
        )

    @router.patch(
        "/workspaces/{workspace_id}/settings/{section}",
        response_model=WorkspaceSettingsResponse,
    )
    def update_workspace_settings_section(
        workspace_id: str,
        section: str,
        payload: WorkspaceSettingsSectionRequest,
    ) -> WorkspaceSettingsResponse:
        _require_enabled(settings)
        workspace = _workspace_or_404(job_db, workspace_id)
        if section == "connection" or section == "resources":
            resource_config = workspace.get("resource_config")
            next_resource_config = (
                dict(resource_config) if isinstance(resource_config, dict) else {}
            )
            if payload.resources is not None:
                next_resource_config["resources"] = payload.resources
            # Backward compat: if cmsUrl/cmsToken passed, also save to cms_config
            if payload.cmsUrl is not None or payload.cmsToken is not None:
                cms_config = workspace.get("cms_config")
                next_cms_config = dict(cms_config) if isinstance(cms_config, dict) else {}
                if payload.cmsUrl is not None:
                    next_cms_config["api_url"] = payload.cmsUrl
                if payload.cmsToken is not None:
                    next_cms_config["token"] = payload.cmsToken
                workspace = job_db.update_workspace(
                    workspace_id,
                    resource_config=next_resource_config,
                    cms_config=next_cms_config,
                )
            else:
                workspace = job_db.update_workspace(
                    workspace_id, resource_config=next_resource_config
                )
        elif section == "intake":
            intake_config = workspace.get("intake_config")
            next_intake_config = dict(intake_config) if isinstance(intake_config, dict) else {}
            if payload.intakeModes is not None:
                next_intake_config["enabled_modes"] = payload.intakeModes
            if payload.labelOverrides is not None:
                next_intake_config["label_overrides"] = payload.labelOverrides
            workspace = job_db.update_workspace(
                workspace_id,
                default_entity=payload.entityType,
                intake_config=next_intake_config,
            )
        elif section == "pipeline":
            if payload.pipelineKey is not None:
                _definition(settings, payload.pipelineKey)
            pipeline_config = workspace.get("pipeline_config")
            if not isinstance(pipeline_config, dict):
                pipeline_config = {}
            if payload.localConcurrency is not None:
                if payload.localConcurrency < 1:
                    raise HTTPException(
                        status_code=400, detail="localConcurrency must be at least 1"
                    )
                pipeline_config["local"] = payload.localConcurrency
            if payload.agentConcurrency is not None:
                if payload.agentConcurrency < 1:
                    raise HTTPException(
                        status_code=400, detail="agentConcurrency must be at least 1"
                    )
                pipeline_config["agent"] = payload.agentConcurrency
            if payload.nodeLocalConcurrency is not None:
                valid_nodes: dict[str, int] = {}
                for node_key, limit in payload.nodeLocalConcurrency.items():
                    if isinstance(limit, int) and limit >= 1:
                        valid_nodes[node_key] = limit
                pipeline_config["nodes"] = valid_nodes
            workspace = job_db.update_workspace(
                workspace_id,
                default_pipeline_key=payload.pipelineKey,
                pipeline_config=pipeline_config if pipeline_config else None,
            )
        else:
            raise HTTPException(status_code=404, detail="Unknown settings section")
        return WorkspaceSettingsResponse(
            settings=_workspace_settings_payload(workspace, settings, job_db)
        )

    @router.post(
        "/workspaces/{workspace_id}/settings/test-connection",
        response_model=WorkspaceSettingsTestResponse,
    )
    def test_workspace_connection(workspace_id: str) -> WorkspaceSettingsTestResponse:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        cms_config = settings.config.get("cms", {}) or {}
        if not (cms_config.get("question_detail_url") or cms_config.get("question_list_url")):
            raise HTTPException(status_code=400, detail="Global CMS URL is not configured")
        return WorkspaceSettingsTestResponse(ok=True, message="全局配置已就绪")

    @router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def update_workspace(
        workspace_id: str,
        payload: WorkspaceUpdateRequest,
    ) -> WorkspaceResponse:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        if payload.default_pipeline_key is not None:
            _definition(settings, payload.default_pipeline_key)
        try:
            workspace = job_db.update_workspace(
                workspace_id,
                name=payload.name,
                description=payload.description,
                default_pipeline_key=payload.default_pipeline_key,
                default_entity=payload.default_entity,
                cms_config=payload.cms_config,
                resource_config=payload.resource_config,
                intake_config=payload.intake_config,
                pipeline_config=payload.pipeline_config,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkspaceResponse(workspace=workspace)

    @router.get("/workspaces/{workspace_id}/stats", response_model=WorkspaceStatsResponse)
    def get_workspace_stats(workspace_id: str) -> WorkspaceStatsResponse:
        _require_enabled(settings)
        workspace = _workspace_or_404(job_db, workspace_id)
        pipeline_key = workspace.get("default_pipeline_key", "question_content")
        allowed = agent_manager.get_allowed_agents(workspace_id)
        all_agents = agent_manager.get_all()
        agents = all_agents if allowed is None else [a for a in all_agents if a.id in allowed]
        busy = sum(1 for a in agents if a.busy)
        latest_run = job_db.get_latest_node_run_for_workspace(workspace_id)
        return WorkspaceStatsResponse(
            workspace_id=workspace_id,
            name=workspace.get("name", ""),
            pipeline_key=pipeline_key,
            pipeline_label=_pipeline_label(settings, pipeline_key),
            job_stats=job_db.count_jobs_by_status(workspace_id),
            agent_status={
                "total": len(agents),
                "busy": busy,
                "idle": len(agents) - busy,
                "agents": [{"id": a.id, "name": a.name or a.id, "busy": a.busy} for a in agents],
            },
            latest_run=dict(latest_run) if latest_run else None,
        )

    @router.delete("/workspaces/{workspace_id}", response_model=DeleteWorkspaceResponse)
    def delete_workspace(workspace_id: str) -> DeleteWorkspaceResponse:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        try:
            job_db.delete_workspace(workspace_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return DeleteWorkspaceResponse(deleted=workspace_id)

    @router.post("/workspaces/{workspace_id}/job-batches", response_model=JobBatchResponse)
    def create_workspace_job_batch(
        workspace_id: str,
        payload: JobBatchRequest,
    ) -> JobBatchResponse:
        return create_batch_for_workspace(workspace_id, payload)

    @router.get("/workspaces/{workspace_id}/jobs", response_model=JobsResponse)
    def list_workspace_jobs(
        workspace_id: str,
        pipeline_key: str | None = None,
        status: str | None = None,
    ) -> JobsResponse:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        return JobsResponse(
            jobs=job_db.list_jobs(
                workspace_id=workspace_id,
                pipeline_key=pipeline_key,
                status=status,
            )
        )

    @router.post("/workspaces/{workspace_id}/jobs/batch-rerun", response_model=BatchJobResponse)
    def batch_rerun_workspace_jobs(
        workspace_id: str,
        payload: BatchJobRequest,
    ) -> BatchJobResponse:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        results: list[dict[str, Any]] = []
        for job_id in _normalize_values(payload.job_ids):
            job = job_db.get_job(job_id)
            if job is None or job["workspace_id"] != workspace_id:
                results.append({"job_id": job_id, "status": "not_found"})
                continue
            if _job_has_running_nodes(job_db, job_id):
                results.append({"job_id": job_id, "status": "skipped", "reason": "running"})
                continue
            definition = _definition(settings, str(job["pipeline_key"]))
            root_nodes = [key for key, node in definition.nodes.items() if not node.after]
            if not root_nodes:
                results.append({"job_id": job_id, "status": "skipped", "reason": "no_root_node"})
                continue
            first_node = root_nodes[0]
            stale_nodes = downstream_nodes(definition, first_node)
            try:
                clear_rerun_outputs(definition, first_node, Path(str(job["storage_dir"])))
            except ValueError as exc:
                results.append(
                    {
                        "job_id": job_id,
                        "status": "skipped",
                        "reason": f"cleanup_failed: {exc}",
                    }
                )
                continue
            job_db.mark_node_for_rerun(job_id, first_node, stale_nodes)
            results.append({"job_id": job_id, "status": "rerun", "node_key": first_node})
        return BatchJobResponse(results=results)

    @router.delete("/workspaces/{workspace_id}/jobs/batch", response_model=BatchJobResponse)
    def batch_delete_workspace_jobs(
        workspace_id: str,
        payload: BatchJobRequest,
    ) -> BatchJobResponse:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        results: list[dict[str, Any]] = []
        for job_id in _normalize_values(payload.job_ids):
            job = job_db.get_job(job_id)
            if job is None or job["workspace_id"] != workspace_id:
                results.append({"job_id": job_id, "status": "not_found"})
                continue
            if _job_has_running_nodes(job_db, job_id):
                results.append({"job_id": job_id, "status": "skipped", "reason": "running"})
                continue
            storage_dir = Path(str(job["storage_dir"]))
            job_db.delete_job(job_id)
            if storage_dir.exists() and storage_dir.is_dir():
                shutil.rmtree(storage_dir)
            for log_path in glob.glob(str(settings.logs_dir / "jobs" / f"{job_id}-*.log")):
                Path(log_path).unlink(missing_ok=True)
            results.append({"job_id": job_id, "status": "deleted"})
        return BatchJobResponse(results=results)

    @router.get("/workspaces/{workspace_id}/runs", response_model=WorkspaceRunsResponse)
    def list_workspace_runs(
        workspace_id: str,
        status: str | None = None,
        node_key: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> WorkspaceRunsResponse:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        return WorkspaceRunsResponse(
            runs=job_db.list_workspace_node_runs(
                workspace_id,
                status=status,
                node_key=node_key,
                job_id=job_id,
                limit=limit,
            )
        )

    @router.get("/workspaces/{workspace_id}/dag", response_model=WorkspaceDagResponse)
    def get_workspace_dag(workspace_id: str) -> WorkspaceDagResponse:
        _require_enabled(settings)
        workspace = _workspace_or_404(job_db, workspace_id)
        pipeline_key = str(workspace.get("default_pipeline_key") or "question_content")
        definition = _definition(settings, pipeline_key)
        counts = job_db.count_workspace_job_nodes_by_status(workspace_id, pipeline_key)
        statuses = ["pending", "running", "completed", "failed", "stale"]
        return WorkspaceDagResponse(
            pipeline={
                "key": definition.key,
                "label": definition.label,
                "concurrency": {
                    "local": definition.concurrency.local,
                    "agent": definition.concurrency.agent,
                },
            },
            nodes=[
                {
                    "key": node.key,
                    "label": node.label,
                    "runner": node.runner,
                    "after": node.after,
                    "inputs": node.inputs,
                    "outputs": node.outputs,
                    "status_counts": {
                        status: counts.get(node.key, {}).get(status, 0) for status in statuses
                    },
                }
                for node in definition.nodes.values()
            ],
        )

    @router.post("/job-batches", response_model=JobBatchResponse)
    def create_job_batch(payload: JobBatchRequest) -> JobBatchResponse:
        return create_batch_for_workspace("default", payload)

    @router.get("/jobs", response_model=JobsResponse)
    def list_jobs(pipeline_key: str | None = None, status: str | None = None) -> JobsResponse:
        _require_enabled(settings)
        return JobsResponse(
            jobs=job_db.list_jobs(
                workspace_id="default",
                pipeline_key=pipeline_key,
                status=status,
            )
        )

    @router.get("/jobs/{job_id}", response_model=JobDetailResponse)
    def get_job(job_id: str) -> JobDetailResponse:
        _require_enabled(settings)
        job = job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return JobDetailResponse(
            job=job,
            nodes=_job_nodes_with_definition(job_db, settings, job),
            runs=job_db.list_node_runs(job_id),
            artifacts=_artifact_names(job),
        )

    @router.get("/jobs/{job_id}/artifacts/{artifact_name:path}", response_model=ArtifactResponse)
    def get_artifact(job_id: str, artifact_name: str) -> ArtifactResponse:
        _require_enabled(settings)
        job = job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        path = _artifact_path(job, artifact_name)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        return ArtifactResponse(name=artifact_name, content=path.read_text(encoding="utf-8"))

    @router.post("/jobs/{job_id}/nodes/{node_key}/rerun", response_model=RerunNodeResponse)
    def rerun_node(job_id: str, node_key: str) -> RerunNodeResponse:
        _require_enabled(settings)
        job = job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        definition = _definition(settings, str(job["pipeline_key"]))
        if node_key not in definition.nodes:
            raise HTTPException(status_code=404, detail="Node not found")
        if _job_has_running_nodes(job_db, job_id):
            raise HTTPException(status_code=400, detail="Cannot rerun a running job")
        stale_nodes = downstream_nodes(definition, node_key)
        try:
            clear_rerun_outputs(definition, node_key, Path(str(job["storage_dir"])))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Cleanup failed: {exc}") from exc
        try:
            job_db.mark_node_for_rerun(job_id, node_key, stale_nodes)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RerunNodeResponse(job_id=job_id, node_key=node_key, stale_nodes=stale_nodes)

    @router.delete("/jobs/{job_id}")
    def delete_job(job_id: str) -> dict[str, str]:
        _require_enabled(settings)
        job = job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if _job_has_running_nodes(job_db, job_id):
            raise HTTPException(status_code=400, detail="Cannot delete a running job")
        storage_dir = Path(str(job["storage_dir"]))
        try:
            job_db.delete_job(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if storage_dir.exists() and storage_dir.is_dir():
            shutil.rmtree(storage_dir)
        for log_path in glob.glob(str(settings.logs_dir / "jobs" / f"{job_id}-*.log")):
            Path(log_path).unlink(missing_ok=True)
        return {"deleted": job_id}

    @router.get("/jobs/{job_id}/{invalid_path:path}", response_model=ArtifactResponse)
    def reject_invalid_job_subpath(job_id: str, invalid_path: str) -> None:
        _require_enabled(settings)
        if job_db.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(status_code=400, detail="Invalid job path")

    return router
