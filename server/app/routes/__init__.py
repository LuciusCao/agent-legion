from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth.studio_authoring import require_studio_authoring
from ..auth.workspace_access import require_workspace_access
from .agent_definitions import create_agent_definitions_router
from .agent_workers import create_agent_workers_router
from .agents import create_agents_router
from .artifacts import create_artifacts_router
from .common import create_common_router
from .connections import create_connections_router
from .deps import RouterDeps
from .instance_settings import create_instance_settings_router
from .job_route_group import include_job_routes
from .materials import create_materials_router
from .metrics import create_metrics_router
from .packages import create_packages_router
from .quality import create_quality_router
from .quality_replays import create_quality_replays_router
from .skill_sources import create_skill_sources_router
from .skills import create_skills_router
from .studio_agent_context import create_studio_agent_context_router
from .studio_agent_tokens import create_studio_agent_tokens_router
from .studio_agent_tools import create_studio_agent_tools_router
from .studio_agents_admin import create_studio_agents_admin_router
from .studio_chat import create_studio_chat_router
from .token_usage_pricing import create_token_usage_pricing_router
from .worker import create_worker_router
from .workflow_node_codes import create_workflow_node_codes_router
from .workflow_revisions import create_workflow_revisions_router
from .workspace_agent_catalog import create_workspace_agent_catalog_router
from .workspace_agent_routes import create_workspace_agent_routes_router
from .workspace_configuration import create_workspace_configuration_router
from .workspace_settings import create_workspace_settings_router
from .workspaces import create_workspaces_router


def _require_all_or_none(parts: tuple, names: tuple[str, ...]) -> tuple:
    """Fail loudly on a partially-wired plane (issue #189): mounting must be
    all-or-nothing, otherwise the routes vanish silently."""
    if any(part is None for part in parts) != all(part is None for part in parts):
        raise ValueError(f"partially wired: {', '.join(names)} must be provided together")
    return parts


def create_router(deps: RouterDeps) -> APIRouter:
    router = APIRouter(prefix="/api")

    def secured(sub_router: APIRouter) -> None:
        router.include_router(sub_router, dependencies=[Depends(require_workspace_access)])

    def studio_secured(sub_router: APIRouter) -> None:
        deps_list = [Depends(require_workspace_access), Depends(require_studio_authoring)]
        router.include_router(sub_router, dependencies=deps_list)

    router.include_router(create_common_router())
    router.include_router(create_agents_router(deps.agent_manager))
    router.include_router(create_token_usage_pricing_router(deps.job_db, deps.settings))
    # Global admin endpoints (not workspace-scoped): the sub-routers enforce
    # require_admin themselves, so they must not go through secured().
    router.include_router(create_instance_settings_router(deps.job_db, deps.settings))
    router.include_router(create_skill_sources_router(deps.settings))
    router.include_router(create_connections_router(deps.settings))
    secured(create_packages_router(deps.job_db, deps.settings, deps.job_packages))
    secured(create_worker_router(deps.workspace_worker_control))
    # The worker control plane is one surface (broker + registry + completion)
    # and so is the quality loop: each mounts together or not at all.
    worker_plane = _require_all_or_none(
        (deps.agent_broker, deps.agent_worker_registry, deps.agent_completion),
        ("agent_broker", "agent_worker_registry", "agent_completion"),
    )
    if all(part is not None for part in worker_plane):
        broker, registry, completion = worker_plane
        workers_router = create_agent_workers_router(  # fmt: skip
            broker,
            registry,
            completion,
            deps.settings,
            deps.ops_metrics,
            deps.job_artifact_objects,
        )
        router.include_router(workers_router)
    if deps.artifact_store is not None:
        router.include_router(
            create_artifacts_router(deps.artifact_store, deps.settings, deps.agent_worker_registry)
        )
    if deps.ops_metrics is not None:
        secured(create_metrics_router(deps.ops_metrics))
    quality_plane = _require_all_or_none(
        (
            deps.quality_sampling,
            deps.quality_labels,
            deps.quality_stats,
            deps.quality_replays,
        ),
        ("quality_sampling", "quality_labels", "quality_stats", "quality_replays"),
    )
    if all(part is not None for part in quality_plane):
        sampling, labels, stats, replays = quality_plane
        secured(create_quality_router(sampling, labels, stats))
        secured(create_quality_replays_router(replays))
    workspaces_router = create_workspaces_router(
        deps.workspace_configuration,
        deps.settings,
        job_event_manager=deps.job_event_manager,
    )
    secured(workspaces_router)
    secured(create_workspace_settings_router(deps.workspace_configuration, deps.settings))
    if deps.materials_service is not None:
        secured(create_materials_router(deps.materials_service))
    studio_secured(create_workflow_revisions_router(deps.job_db, deps.settings))
    studio_secured(create_workflow_node_codes_router(deps.job_db, deps.settings))
    studio_secured(create_agent_definitions_router(deps.job_db, deps.settings))
    secured(create_skills_router(deps.settings))
    secured(create_workspace_configuration_router(deps.workspace_configuration, deps.settings))
    agent_catalog_router = create_workspace_agent_catalog_router(
        deps.agent_catalog, deps.workspace_execution_configuration, deps.settings
    )
    secured(agent_catalog_router)
    secured(create_workspace_agent_routes_router(deps.job_db, deps.settings))
    secured(create_studio_agent_tools_router(deps.job_db, deps.settings))
    secured(create_studio_agent_context_router(deps.job_db))
    secured(create_studio_agent_tokens_router(deps.job_db))
    if deps.studio_chat_service is not None:
        router.include_router(create_studio_agents_admin_router(deps.job_db))
        chat = create_studio_chat_router(
            deps.studio_chat_service, job_event_manager=deps.job_event_manager
        )
        studio_secured(chat)
    job_group = APIRouter(dependencies=[Depends(require_workspace_access)])
    include_job_routes(
        job_group,
        deps.job_db,
        deps.settings,
        deps.workspace_execution_configuration,
        deps.job_event_manager,
        deps.job_event_buffer,
        artifact_store=deps.artifact_store,
        object_store=deps.job_artifact_objects,
    )
    router.include_router(job_group)

    return router
