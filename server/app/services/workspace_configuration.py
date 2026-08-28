from typing import Any

from server.app.jobs import JobQueries
from server.app.services.agent_service import published_agent_definitions
from server.app.services.demo_material_seed import seed_demo_workspace_materials
from server.app.services.demo_node_seed import seed_demo_workspace_node_codes
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.vault import VaultService
from server.app.services.workflow_definitions import (
    builtin_definition_or_none,
    workspace_active_definition,
)
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_node_config import update_workspace_node_config
from server.app.services.workspace_node_limit_validation import (
    validate_workspace_node_limits,
)
from server.app.services.workspace_settings_payload import workspace_settings_payload
from server.app.services.workspace_settings_schemas import (
    workspace_settings_payload_with_schemas,
)
from server.app.services.workspace_stats import build_workspace_stats
from server.app.settings import Settings
from server.app.workflows.builtin_demo import DEMO_WORKFLOW_KEY
from server.app.workflows.definition import WorkflowDefinition


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
    ):
        self.job_db = job_db
        self.settings = settings

    def _workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.job_db.get_workspace(workspace_id)
        if workspace is None:
            raise NotFoundError("Workspace not found")
        return workspace

    def _vault(self) -> VaultService:
        return VaultService(self.job_db.path, self.settings.config)

    def _payload(self, workspace: dict[str, Any]) -> dict[str, Any]:
        return workspace_settings_payload_with_schemas(
            self.job_db,
            published_agent_definitions(self.settings.database_url, str(workspace["id"])),
            workspace,
        )

    def _ensure_active_revision(self, workspace_id: str, definition: WorkflowDefinition) -> None:
        if definition.key == DEMO_WORKFLOW_KEY:
            seed_demo_workspace_node_codes(self.settings, workspace_id)
            # Demo materials (design §9): seed-if-absent, skipped with a
            # warning when object storage is not configured.
            seed_demo_workspace_materials(self.settings, workspace_id)
        WorkflowRevisionService(
            self.job_db, self.settings.executor_runtime.workflows.custom_nodes_enabled
        ).ensure_active_revision(workspace_id, definition)

    def list_workspaces(self) -> list[dict[str, Any]]:
        return self.job_db.list_workspaces()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Schema v62: the caller-provided id is the workflow key — bound at
        # creation and immutable. No sample-template seeding on this path;
        # demo workspaces come from `make import-demo` (scripts/seed_demo.py).
        workspace_id = str(payload.get("id") or "").strip()
        if not workspace_id:
            raise InvalidOperationError("Workspace id is required")
        clean_name = str(payload.get("name") or "").strip()
        if not clean_name:
            raise InvalidOperationError("Workspace name is required")
        try:
            workspace = self.job_db.create_workspace(
                clean_name,
                default_workflow_key=workspace_id,
                default_entity=payload.get("default_entity", "question"),
                resource_config=payload.get("resource_config", {}),
                intake_config=payload.get("intake_config", {}),
                workspace_id=workspace_id,
            )
        except ValueError as exc:
            # 409 is reserved for real conflicts (id already exists); every
            # other validation error stays a 400.
            if "already exists" in str(exc):
                raise ConflictError(str(exc)) from exc
            raise InvalidOperationError(str(exc)) from exc
        return workspace

    def get(self, workspace_id: str) -> dict[str, Any]:
        return self._workspace(workspace_id)

    def update(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._workspace(workspace_id)
        # Schema v62: the workflow key (= workspace id) is immutable.
        if payload.get("default_workflow_key") is not None:
            raise InvalidOperationError("Workflow key is bound to the workspace id and immutable")
        try:
            return self.job_db.update_workspace(
                workspace_id,
                name=payload.get("name"),
                description=payload.get("description"),
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

    def _definition_for_seed(self, workspace_id: str, workflow_key: str):
        """Active revision definition, falling back to the sample template.

        The builtin fallback keeps the legacy re-seed path: a workspace bound
        to the sample workflow key gets the factory DAG published on save when
        it has no active revision yet (ensure_active_revision is
        seed-if-absent).
        """
        return workspace_active_definition(
            self.job_db, workspace_id, workflow_key
        ) or builtin_definition_or_none(workflow_key)

    def replace_configuration(
        self,
        workspace_id: str,
        workspace_patch: dict[str, Any],
        settings_patch: dict[str, Any],
        node_limits: list[dict[str, Any]],
        agent_capacity: int | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        current = workspace_settings_payload(workspace)
        # Schema v62: the workflow key is bound to the workspace id and
        # immutable. The settings payload still carries workflowKey for
        # compatibility; a matching value is a no-op round-trip.
        workflow_key = settings_patch.get("workflowKey") or str(current["workflowKey"])
        if not workflow_key:
            raise InvalidOperationError("Workspace workflow is not set")
        if workflow_key != str(workspace["default_workflow_key"]):
            raise InvalidOperationError("Workflow key is bound to the workspace id and immutable")
        workflow = self._definition_for_seed(workspace_id, workflow_key)
        # workflow is None before the first publish; the validator then runs
        # only the definition-independent checks, and publish-time validation
        # enforces node correctness — this unblocks the first-publish
        # chicken-and-egg.
        validate_workspace_node_limits(
            workflow=workflow,
            node_limits=node_limits,
            agent_capabilities={
                definition.capability
                for definition in published_agent_definitions(
                    self.settings.database_url, workspace_id
                ).values()
            },
            code_capacity=self.settings.executor_runtime.code_capacity,
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
                node_limits=node_limits,
            )
            # None means "leave unchanged" — the workspace keeps any
            # previously saved (or schema-seeded) Agent capacity.
            if agent_capacity is not None:
                self.job_db.set_workspace_agent_capacity(workspace_id, agent_capacity)
        except ValueError as exc:
            raise InvalidOperationError(str(exc)) from exc
        if workflow is not None:
            self._ensure_active_revision(workspace_id, workflow)
        return {
            "workspace": saved_workspace,
            "settings": self._payload(saved_workspace),
            "execution_configuration": {
                "node_limits": self.job_db.get_workspace_node_limits(workspace_id),
                "migration_warnings": [],
            },
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
            # Schema v62: the workflow key is immutable (bound to the id).
            # The section stays so legacy clients sending an unchanged key
            # keep working; any change is rejected.
            workflow_key = patch.get("workflowKey")
            if workflow_key is not None and str(workflow_key) != str(
                workspace["default_workflow_key"]
            ):
                raise InvalidOperationError(
                    "Workflow key is bound to the workspace id and immutable"
                )
        elif section == "nodes":
            workspace = update_workspace_node_config(
                self.job_db,
                self.settings,
                published_agent_definitions(self.settings.database_url, workspace_id),
                workspace,
                patch,
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

    def stats(self, workspace_id: str) -> dict[str, Any]:
        return build_workspace_stats(
            self._workspace(workspace_id),
            workspace_id,
            self.job_db,
            self.settings,
        )
