from typing import Any

from server.app.agents import AgentStatusManager
from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.pipeline_catalog import PipelineCatalogService
from server.app.services.workspace_executor_validation import (
    validate_workspace_executor_configuration,
)
from server.app.services.workspace_executor_warnings import configuration_with_warnings
from server.app.settings import Settings


class WorkspaceConfigurationService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        agent_manager: AgentStatusManager,
        pipelines: PipelineCatalogService,
    ):
        self.job_db = job_db
        self.settings = settings
        self.agent_manager = agent_manager
        self.pipelines = pipelines

    def _workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.job_db.get_workspace(workspace_id)
        if workspace is None:
            raise NotFoundError("Workspace not found")
        return workspace

    def _settings_payload(self, workspace: dict[str, Any]) -> dict[str, Any]:
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
        return {
            "entityType": str(workspace.get("default_entity") or "question"),
            "intakeModes": enabled_modes if isinstance(enabled_modes, list) else [],
            "labelOverrides": label_overrides if isinstance(label_overrides, dict) else {},
            "pipelineKey": str(workspace.get("default_pipeline_key") or "question_content"),
            "resources": resources,
        }

    def list_workspaces(self) -> list[dict[str, Any]]:
        return self.job_db.list_workspaces()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.pipelines.definition(payload["default_pipeline_key"])
        try:
            return self.job_db.create_workspace(
                payload["name"],
                default_pipeline_key=payload["default_pipeline_key"],
                default_entity=payload.get("default_entity", "question"),
                cms_config=payload.get("cms_config", {}),
                resource_config=payload.get("resource_config", {}),
                intake_config=payload.get("intake_config", {}),
            )
        except ValueError as exc:
            raise InvalidOperationError(str(exc)) from exc

    def get(self, workspace_id: str) -> dict[str, Any]:
        return self._workspace(workspace_id)

    def update(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._workspace(workspace_id)
        if payload.get("default_pipeline_key") is not None:
            self.pipelines.definition(payload["default_pipeline_key"])
        try:
            return self.job_db.update_workspace(
                workspace_id,
                name=payload.get("name"),
                description=payload.get("description"),
                default_pipeline_key=payload.get("default_pipeline_key"),
                default_entity=payload.get("default_entity"),
                cms_config=payload.get("cms_config"),
                resource_config=payload.get("resource_config"),
                intake_config=payload.get("intake_config"),
            )
        except ValueError as exc:
            raise InvalidOperationError(str(exc)) from exc

    def delete(self, workspace_id: str) -> str:
        self._workspace(workspace_id)
        try:
            self.job_db.delete_workspace(workspace_id)
        except ValueError as exc:
            raise InvalidOperationError(str(exc)) from exc
        return workspace_id

    def settings_payload(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        return self._settings_payload(workspace)

    def replace_configuration(
        self,
        workspace_id: str,
        workspace_patch: dict[str, Any],
        settings_patch: dict[str, Any],
        executor_allocations: list[dict[str, Any]],
        node_bindings: list[dict[str, Any]],
        node_limits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        current = self._settings_payload(workspace)
        pipeline_key = settings_patch.get("pipelineKey") or str(current["pipelineKey"])
        pipeline = self.pipelines.definition(pipeline_key)

        validate_workspace_executor_configuration(
            pipeline=pipeline,
            executor_definitions=self.settings.executor_definitions,
            allocations=executor_allocations,
            bindings=node_bindings,
            node_limits=node_limits,
        )
        name_value = workspace_patch.get("name")
        name: str = name_value if name_value is not None else str(workspace["name"])
        description_value = workspace_patch.get("description")
        description: str = (
            description_value
            if description_value is not None
            else str(workspace.get("description") or "")
        )
        try:
            saved_workspace = self.job_db.update_workspace_configuration(
                workspace_id,
                name=name,
                description=description,
                default_pipeline_key=pipeline_key,
                default_entity=settings_patch.get("entityType") or str(current["entityType"]),
                resource_config={
                    "resources": settings_patch.get("resources")
                    if settings_patch.get("resources") is not None
                    else current["resources"]
                },
                intake_config={
                    "enabled_modes": settings_patch.get("intakeModes")
                    if settings_patch.get("intakeModes") is not None
                    else current["intakeModes"],
                    "label_overrides": settings_patch.get("labelOverrides")
                    if settings_patch.get("labelOverrides") is not None
                    else current["labelOverrides"],
                },
                executor_allocations=executor_allocations,
                node_bindings=node_bindings,
                node_limits=node_limits,
            )
        except ValueError as exc:
            raise InvalidOperationError(str(exc)) from exc
        self._sync_pi_agents(workspace_id, executor_allocations)
        executor_configuration = self.job_db.get_workspace_executor_configuration(workspace_id)
        return {
            "workspace": saved_workspace,
            "settings": self._settings_payload(saved_workspace),
            "executor_configuration": configuration_with_warnings(
                self.job_db, workspace_id, executor_configuration
            ),
        }

    def update_section(
        self, workspace_id: str, section: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        if section == "connection" or section == "resources":
            resource_config = workspace.get("resource_config")
            next_resource_config = (
                dict(resource_config) if isinstance(resource_config, dict) else {}
            )
            if patch.get("resources") is not None:
                next_resource_config["resources"] = patch["resources"]
            if patch.get("cmsUrl") is not None or patch.get("cmsToken") is not None:
                cms_config = workspace.get("cms_config")
                next_cms_config = dict(cms_config) if isinstance(cms_config, dict) else {}
                if patch.get("cmsUrl") is not None:
                    next_cms_config["api_url"] = patch["cmsUrl"]
                if patch.get("cmsToken") is not None:
                    next_cms_config["token"] = patch["cmsToken"]
                workspace = self.job_db.update_workspace(
                    workspace_id,
                    resource_config=next_resource_config,
                    cms_config=next_cms_config,
                )
            else:
                workspace = self.job_db.update_workspace(
                    workspace_id, resource_config=next_resource_config
                )
        elif section == "intake":
            intake_config = workspace.get("intake_config")
            next_intake_config = dict(intake_config) if isinstance(intake_config, dict) else {}
            if patch.get("intakeModes") is not None:
                next_intake_config["enabled_modes"] = patch["intakeModes"]
            if patch.get("labelOverrides") is not None:
                next_intake_config["label_overrides"] = patch["labelOverrides"]
            workspace = self.job_db.update_workspace(
                workspace_id,
                default_entity=patch.get("entityType"),
                intake_config=next_intake_config,
            )
        elif section == "pipeline":
            if patch.get("pipelineKey") is not None:
                self.pipelines.definition(patch["pipelineKey"])
            workspace = self.job_db.update_workspace(
                workspace_id,
                default_pipeline_key=patch.get("pipelineKey"),
            )
        else:
            raise NotFoundError("Unknown settings section")
        return self._settings_payload(workspace)

    def test_connection(self, workspace_id: str) -> dict[str, Any]:
        self._workspace(workspace_id)
        cms_config = self.settings.config.get("cms", {}) or {}
        if not (cms_config.get("question_detail_url") or cms_config.get("question_list_url")):
            raise InvalidOperationError("Global CMS URL is not configured")
        return {"ok": True, "message": "全局配置已就绪"}

    def _sync_pi_agents(
        self,
        workspace_id: str,
        executor_allocations: list[dict[str, Any]],
    ) -> None:
        pi_allocation: dict[str, Any] | None = None
        for allocation in executor_allocations:
            executor_id = allocation.get("executor_id", "")
            definition = self.settings.executor_definitions.get(executor_id)
            if definition is not None and definition.kind == "pi":
                pi_allocation = allocation
                break
        if pi_allocation is not None:
            self.agent_manager.add_pi_agent_for_workspace(
                workspace_id, int(pi_allocation.get("concurrency_limit", 1))
            )
        else:
            self.agent_manager.remove_pi_agent_for_workspace(workspace_id)

    def stats(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        pipeline_key = workspace.get("default_pipeline_key", "question_content")
        latest_run = self.job_db.get_latest_node_run_for_workspace(workspace_id)
        executors = []
        for count in self.job_db.get_workspace_executor_runtime_counts(workspace_id):
            definition = self.settings.executor_definitions.get(count["executor_id"])
            global_capacity = definition.global_capacity if definition is not None else 0
            global_available = global_capacity - count["global_running"]
            available = max(0, min(count["workspace_limit"] - count["running"], global_available))
            executors.append(
                {
                    "executor_id": count["executor_id"],
                    "kind": definition.kind if definition is not None else "unknown",
                    "global_capacity": global_capacity,
                    "workspace_limit": count["workspace_limit"],
                    "running": count["running"],
                    "available": available,
                    "binding_count": count["binding_count"],
                }
            )
        return {
            "workspace_id": workspace_id,
            "name": workspace.get("name", ""),
            "pipeline_key": pipeline_key,
            "pipeline_label": self.pipelines.definition(pipeline_key).label,
            "job_stats": self.job_db.count_jobs_by_status(workspace_id),
            "executor_status": {"executors": executors},
            "latest_run": dict(latest_run) if latest_run else None,
        }


def sync_workspace_pi_agents(
    job_db: JobQueries,
    settings: Settings,
    agent_manager: AgentStatusManager,
) -> None:
    """Register pi agents for all workspaces that currently allocate a pi executor."""
    for workspace in job_db.list_workspaces():
        workspace_id = str(workspace["id"])
        config = job_db.get_workspace_executor_configuration(workspace_id)
        pi_allocation: dict[str, Any] | None = None
        for allocation in config.get("allocations", []):
            executor_id = allocation.get("executor_id", "")
            definition = settings.executor_definitions.get(executor_id)
            if definition is not None and definition.kind == "pi":
                pi_allocation = allocation
                break
        if pi_allocation is not None:
            agent_manager.add_pi_agent_for_workspace(
                workspace_id, int(pi_allocation.get("concurrency_limit", 1))
            )
        else:
            agent_manager.remove_pi_agent_for_workspace(workspace_id)
