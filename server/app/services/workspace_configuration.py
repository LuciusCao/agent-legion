from typing import Any

from server.app.agents import AgentStatusManager
from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_executor_validation import (
    validate_workspace_executor_configuration,
)
from server.app.services.workspace_settings_payload import workspace_settings_payload
from server.app.services.workspace_stats import build_workspace_stats
from server.app.settings import Settings


def _build_settings_config(
    current: dict[str, Any], patch: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "resources": patch["resources"]
            if patch.get("resources") is not None
            else current["resources"]
        },
        {
            "enabled_modes": patch["intakeModes"]
            if patch.get("intakeModes") is not None
            else current["intakeModes"],
            "label_overrides": patch["labelOverrides"]
            if patch.get("labelOverrides") is not None
            else current["labelOverrides"],
        },
    )


class WorkspaceConfigurationService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        agent_manager: AgentStatusManager,
        workflows: WorkflowCatalogService,
    ):
        self.job_db = job_db
        self.settings = settings
        self.agent_manager = agent_manager
        self.workflows = workflows

    def _workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.job_db.get_workspace(workspace_id)
        if workspace is None:
            raise NotFoundError("Workspace not found")
        return workspace

    def list_workspaces(self) -> list[dict[str, Any]]:
        return self.job_db.list_workspaces()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        definition = self.workflows.definition(payload["default_workflow_key"])
        try:
            workspace = self.job_db.create_workspace(
                payload["name"],
                default_workflow_key=payload["default_workflow_key"],
                default_entity=payload.get("default_entity", "question"),
                cms_config=payload.get("cms_config", {}),
                resource_config=payload.get("resource_config", {}),
                intake_config=payload.get("intake_config", {}),
            )
        except ValueError as exc:
            raise InvalidOperationError(str(exc)) from exc
        WorkflowRevisionService(self.job_db).ensure_active_revision(workspace["id"], definition)
        return workspace

    def get(self, workspace_id: str) -> dict[str, Any]:
        return self._workspace(workspace_id)

    def update(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._workspace(workspace_id)
        if payload.get("default_workflow_key") is not None:
            self.workflows.definition(payload["default_workflow_key"])
        try:
            return self.job_db.update_workspace(
                workspace_id,
                name=payload.get("name"),
                description=payload.get("description"),
                default_workflow_key=payload.get("default_workflow_key"),
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
        return workspace_settings_payload(workspace)

    def replace_configuration(
        self,
        workspace_id: str,
        workspace_patch: dict[str, Any],
        settings_patch: dict[str, Any],
        executor_allocations: list[dict[str, Any]],
        node_bindings: list[dict[str, Any]],
        node_limits: list[dict[str, Any]],
        agent_capacity: int | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        current = workspace_settings_payload(workspace)
        workflow_key = settings_patch.get("workflowKey") or str(current["workflowKey"])
        if not workflow_key:
            raise InvalidOperationError("Workspace workflow is not set")
        workflow = self.workflows.definition(workflow_key)

        validate_workspace_executor_configuration(
            workflow=workflow,
            executor_definitions=self.settings.executor_definitions,
            allocations=executor_allocations,
            bindings=node_bindings,
            node_limits=node_limits,
            agent_capabilities={
                definition.capability for definition in self.settings.agent_definitions.values()
            },
        )
        name_value = workspace_patch.get("name")
        name: str = name_value if name_value is not None else str(workspace["name"])
        description_value = workspace_patch.get("description")
        description: str = (
            description_value
            if description_value is not None
            else str(workspace.get("description") or "")
        )
        resource_config, intake_config = _build_settings_config(current, settings_patch)
        if agent_capacity is not None and agent_capacity <= 0:
            raise InvalidOperationError("Agent capacity must be a positive integer")
        try:
            saved_workspace = self.job_db.update_workspace_configuration(
                workspace_id,
                name=name,
                description=description,
                default_workflow_key=workflow_key,
                default_entity=settings_patch.get("entityType") or str(current["entityType"]),
                resource_config=resource_config,
                intake_config=intake_config,
                executor_allocations=executor_allocations,
                node_bindings=node_bindings,
                node_limits=node_limits,
            )
            # None means "leave unchanged" — the workspace keeps any
            # previously saved (or schema-seeded) Agent capacity.
            if agent_capacity is not None:
                self.job_db.set_workspace_agent_capacity(workspace_id, agent_capacity)
        except ValueError as exc:
            raise InvalidOperationError(str(exc)) from exc
        WorkflowRevisionService(self.job_db).ensure_active_revision(workspace_id, workflow)
        executor_configuration = self.job_db.get_workspace_executor_configuration(workspace_id)
        return {
            "workspace": saved_workspace,
            "settings": workspace_settings_payload(saved_workspace),
            "executor_configuration": {**executor_configuration, "migration_warnings": []},
            "agent_capacity": self.job_db.get_workspace_agent_capacity(workspace_id),
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
        elif section == "workflow":
            if patch.get("workflowKey") is not None:
                definition = self.workflows.definition(patch["workflowKey"])
            workspace = self.job_db.update_workspace(
                workspace_id,
                default_workflow_key=patch.get("workflowKey"),
            )
            if patch.get("workflowKey") is not None:
                WorkflowRevisionService(self.job_db).ensure_active_revision(
                    workspace_id, definition
                )

        else:
            raise NotFoundError("Unknown settings section")
        return workspace_settings_payload(workspace)

    def test_connection(self, workspace_id: str) -> dict[str, Any]:
        self._workspace(workspace_id)
        cms_config = self.settings.config.get("cms", {}) or {}
        if not (cms_config.get("question_detail_url") or cms_config.get("question_list_url")):
            raise InvalidOperationError("Global CMS URL is not configured")
        return {"ok": True, "message": "全局配置已就绪"}

    def stats(self, workspace_id: str) -> dict[str, Any]:
        return build_workspace_stats(
            self._workspace(workspace_id),
            workspace_id,
            self.job_db,
            self.workflows,
            self.settings,
        )
