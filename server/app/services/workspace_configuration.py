from typing import Any

from server.app.events.agents import AgentStatusManager
from server.app.jobs import JobQueries
from server.app.services.agent_service import published_agent_definitions
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.vault import VaultService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_connection_test import test_workspace_connection
from server.app.services.workspace_executor_validation import (
    validate_workspace_executor_configuration,
)
from server.app.services.workspace_node_config import update_workspace_node_config
from server.app.services.workspace_settings_payload import workspace_settings_payload
from server.app.services.workspace_settings_schemas import (
    workspace_settings_payload_with_schemas,
)
from server.app.services.workspace_stats import build_workspace_stats
from server.app.settings import Settings


def _build_intake_config(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled_modes": patch["intakeModes"]
        if patch.get("intakeModes") is not None
        else current["intakeModes"],
        "label_overrides": patch["labelOverrides"]
        if patch.get("labelOverrides") is not None
        else current["labelOverrides"],
    }


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

    def _vault(self) -> VaultService:
        return VaultService(self.job_db.path, self.settings.config)

    def _payload(self, workspace: dict[str, Any]) -> dict[str, Any]:
        return workspace_settings_payload_with_schemas(
            self.workflows,
            published_agent_definitions(self.settings.database_url),
            workspace,
            self.settings.executor_definitions,
        )

    def list_workspaces(self) -> list[dict[str, Any]]:
        return self.job_db.list_workspaces()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        definition = self.workflows.definition(payload["default_workflow_key"])
        try:
            workspace = self.job_db.create_workspace(
                payload["name"],
                default_workflow_key=payload["default_workflow_key"],
                default_entity=payload.get("default_entity", "question"),
                resource_config=payload.get("resource_config", {}),
                intake_config=payload.get("intake_config", {}),
            )
        except ValueError as exc:
            raise InvalidOperationError(str(exc)) from exc
        WorkflowRevisionService(
            self.job_db, self.settings.executor_runtime.workflows.custom_nodes_enabled
        ).ensure_active_revision(workspace["id"], definition)
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
        return self._payload(workspace)

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
                definition.capability
                for definition in published_agent_definitions(self.settings.database_url).values()
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
        # Resource bindings are retired (v19); the stored column is left
        # untouched so legacy rows keep whatever the migration left behind.
        raw_resource_config = workspace.get("resource_config")
        resource_config = dict(raw_resource_config) if isinstance(raw_resource_config, dict) else {}
        intake_config = _build_intake_config(current, settings_patch)
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
        WorkflowRevisionService(
            self.job_db, self.settings.executor_runtime.workflows.custom_nodes_enabled
        ).ensure_active_revision(workspace_id, workflow)
        executor_configuration = self.job_db.get_workspace_executor_configuration(workspace_id)
        return {
            "workspace": saved_workspace,
            "settings": self._payload(saved_workspace),
            "executor_configuration": {**executor_configuration, "migration_warnings": []},
            "agent_capacity": self.job_db.get_workspace_agent_capacity(workspace_id),
        }

    def update_section(
        self, workspace_id: str, section: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        if section == "intake":
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
                WorkflowRevisionService(
                    self.job_db, self.settings.executor_runtime.workflows.custom_nodes_enabled
                ).ensure_active_revision(workspace_id, definition)
        elif section == "nodes":
            workspace = update_workspace_node_config(
                self.job_db,
                self.workflows,
                published_agent_definitions(self.settings.database_url),
                workspace,
                patch,
                self.settings.executor_definitions,
            )
        elif section == "agent-defaults":
            defaults = patch.get("agentDefaults")
            if not isinstance(defaults, dict):
                raise InvalidOperationError("agentDefaults payload is required")
            values: dict[str, Any] = {}
            for key in ("provider", "model", "thinking"):
                value = defaults.get(key)
                if value is not None and not isinstance(value, str):
                    raise InvalidOperationError(f"agentDefaults.{key} must be a string")
                values[key] = value
            workspace = self.job_db.update_workspace(
                workspace_id,
                default_agent_provider=values["provider"],
                default_agent_model=values["model"],
                default_agent_thinking=values["thinking"],
            )

        else:
            raise NotFoundError("Unknown settings section")
        return self._payload(workspace)

    def test_connection(self, workspace_id: str) -> dict[str, Any]:
        return test_workspace_connection(
            workspace_id,
            self._workspace(workspace_id),
            self.settings,
        )

    def stats(self, workspace_id: str) -> dict[str, Any]:
        return build_workspace_stats(
            self._workspace(workspace_id),
            workspace_id,
            self.job_db,
            self.workflows,
            self.settings,
        )
