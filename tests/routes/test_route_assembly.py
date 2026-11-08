from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import APIRouter

from server.app.auth.studio_authoring import require_studio_authoring
from server.app.auth.workspace_access import (
    require_job_workspace_access,
    require_workspace_access,
)
from server.app.routes import create_router

pytestmark = pytest.mark.no_db

_FACTORY_NAMES = [
    "create_common_router",
    "create_agents_router",
    "create_token_usage_pricing_router",
    "create_instance_settings_router",
    "create_connections_router",
    "create_infra_connections_router",
    "create_packages_router",
    "create_worker_router",
    "create_agent_workers_router",
    "create_artifacts_router",
    "create_metrics_router",
    "create_runtime_profile_router",
    "create_quality_router",
    "create_quality_replays_router",
    "create_workspaces_router",
    "create_workspace_settings_router",
    "create_workspace_api_tokens_router",
    "create_materials_router",
    "create_workflow_revisions_router",
    "create_workflow_node_codes_router",
    "create_agent_definitions_router",
    "create_skills_router",
    "create_skill_directories_router",
    "create_workspace_configuration_router",
    "create_workspace_agent_catalog_router",
    "create_workspace_agent_routes_router",
    "create_workspace_shared_materials_router",
    "create_workspace_shared_materials_propagate_router",
    "create_preview_panels_router",
    "create_studio_agent_tools_router",
    "create_studio_agent_job_tools_router",
    "create_studio_agent_context_router",
    "create_studio_agent_tokens_router",
    "create_studio_agents_admin_router",
    "create_studio_chat_router",
]

_DEP_NAMES = [
    "job_db",
    "settings",
    "agent_manager",
    "agent_catalog",
    "workspace_execution_configuration",
    "workspace_configuration",
    "job_packages",
    "workspace_worker_control",
    "job_event_manager",
    "job_event_buffer",
    "artifact_store",
    "agent_broker",
    "agent_worker_registry",
    "agent_completion",
    "ops_metrics",
    "quality_sampling",
    "quality_labels",
    "quality_stats",
    "quality_replays",
    "studio_chat_service",
    "materials_service",
    "job_artifact_objects",
    "workspace_api_token_store",
]


def _dependencies(router: APIRouter, path: str) -> list[object]:
    route = next(route for route in router.routes if route.path == path)
    return [dependency.dependency for dependency in route.dependencies]


def test_route_assembly_preserves_mount_order_and_auth_scope(monkeypatch) -> None:
    import server.app.routes.assembly as assembly

    def fake_factory(name: str):
        def create(*args, **kwargs) -> APIRouter:
            router = APIRouter()

            async def endpoint() -> None:
                return None

            router.add_api_route(f"/{name}", endpoint, methods=["GET"])
            return router

        return create

    for name in _FACTORY_NAMES:
        monkeypatch.setattr(assembly, name, fake_factory(name))

    def fake_job_routes(group: APIRouter, *args, **kwargs) -> None:
        async def endpoint() -> None:
            return None

        group.add_api_route("/job-group", endpoint, methods=["GET"])

    monkeypatch.setattr(assembly, "include_job_routes", fake_job_routes)
    deps = SimpleNamespace(**{name: object() for name in _DEP_NAMES})

    router = create_router(deps)

    expected_names = [*_FACTORY_NAMES, "job-group"]
    assert [route.path for route in router.routes] == [f"/api/{name}" for name in expected_names]
    for name in (
        "create_common_router",
        "create_agents_router",
        "create_token_usage_pricing_router",
        "create_instance_settings_router",
        "create_connections_router",
        "create_infra_connections_router",
        "create_agent_workers_router",
        "create_artifacts_router",
        "create_studio_agents_admin_router",
    ):
        assert require_workspace_access not in _dependencies(router, f"/api/{name}")
        assert require_studio_authoring not in _dependencies(router, f"/api/{name}")
    for name in (
        "create_packages_router",
        "create_metrics_router",
        "create_skills_router",
        "create_preview_panels_router",
        "create_studio_agent_job_tools_router",
    ):
        assert _dependencies(router, f"/api/{name}") == [require_workspace_access]
    for name in (
        "create_workflow_revisions_router",
        "create_workflow_node_codes_router",
        "create_agent_definitions_router",
        "create_studio_chat_router",
    ):
        assert _dependencies(router, f"/api/{name}") == [
            require_workspace_access,
            require_studio_authoring,
        ]
    assert _dependencies(router, "/api/job-group") == [require_job_workspace_access]
