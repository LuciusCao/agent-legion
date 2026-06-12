from typing import Any

from server.app.agents import AgentStatusManager
from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.pipeline_catalog import PipelineCatalogService
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

    def _effective_cms_config(self, workspace: dict[str, Any]) -> dict[str, Any]:
        base = self.settings.config.get("cms", {})
        config = dict(base) if isinstance(base, dict) else {}
        workspace_config = workspace.get("cms_config")
        if isinstance(workspace_config, dict):
            config.update(workspace_config)
        return config

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
        pipeline_config = workspace.get("pipeline_config")
        if not isinstance(pipeline_config, dict):
            pipeline_config = {}
        definition = self.pipelines.definition(
            str(workspace.get("default_pipeline_key") or "question_content")
        )
        assignments = self.job_db.list_workspace_agents(str(workspace.get("id") or ""))
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
                pipeline_config=payload.get("pipeline_config", {}),
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
                pipeline_config=payload.get("pipeline_config"),
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
        agents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        current = self._settings_payload(workspace)
        pipeline_key = settings_patch.get("pipelineKey") or str(current["pipelineKey"])
        self.pipelines.definition(pipeline_key)

        local_concurrency = settings_patch.get("localConcurrency")
        agent_concurrency = settings_patch.get("agentConcurrency")
        node_concurrency = settings_patch.get("nodeLocalConcurrency")
        if local_concurrency is not None and local_concurrency < 1:
            raise InvalidOperationError("localConcurrency must be at least 1")
        if agent_concurrency is not None and agent_concurrency < 1:
            raise InvalidOperationError("agentConcurrency must be at least 1")
        if node_concurrency is not None and any(limit < 1 for limit in node_concurrency.values()):
            raise InvalidOperationError("Node concurrency must be at least 1")

        pipeline_config = {
            "local": local_concurrency
            if local_concurrency is not None
            else current["localConcurrency"],
            "agent": agent_concurrency
            if agent_concurrency is not None
            else current["agentConcurrency"],
            "nodes": node_concurrency
            if node_concurrency is not None
            else current["nodeLocalConcurrency"],
        }
        name_value = workspace_patch.get("name")
        name: str = name_value if name_value is not None else str(workspace["name"])
        description_value = workspace_patch.get("description")
        description: str = (
            description_value
            if description_value is not None
            else str(workspace.get("description") or "")
        )
        try:
            saved_workspace, assignments = self.job_db.update_workspace_configuration(
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
                pipeline_config=pipeline_config,
                agent_assignments=agents,
            )
        except ValueError as exc:
            raise InvalidOperationError(str(exc)) from exc
        return {
            "workspace": saved_workspace,
            "settings": self._settings_payload(saved_workspace),
            "agents": assignments,
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
            pipeline_config = workspace.get("pipeline_config")
            if not isinstance(pipeline_config, dict):
                pipeline_config = {}
            if patch.get("localConcurrency") is not None:
                if patch["localConcurrency"] < 1:
                    raise InvalidOperationError("localConcurrency must be at least 1")
                pipeline_config["local"] = patch["localConcurrency"]
            if patch.get("agentConcurrency") is not None:
                if patch["agentConcurrency"] < 1:
                    raise InvalidOperationError("agentConcurrency must be at least 1")
                pipeline_config["agent"] = patch["agentConcurrency"]
            if patch.get("nodeLocalConcurrency") is not None:
                valid_nodes: dict[str, int] = {}
                for node_key, limit in patch["nodeLocalConcurrency"].items():
                    if isinstance(limit, int) and limit >= 1:
                        valid_nodes[node_key] = limit
                pipeline_config["nodes"] = valid_nodes
            workspace = self.job_db.update_workspace(
                workspace_id,
                default_pipeline_key=patch.get("pipelineKey"),
                pipeline_config=pipeline_config if pipeline_config else None,
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

    def stats(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        pipeline_key = workspace.get("default_pipeline_key", "question_content")
        allowed = self.agent_manager.get_allowed_agents(workspace_id)
        all_agents = self.agent_manager.get_all()
        agents = all_agents if allowed is None else [a for a in all_agents if a.id in allowed]
        busy = sum(1 for a in agents if a.busy)
        latest_run = self.job_db.get_latest_node_run_for_workspace(workspace_id)
        return {
            "workspace_id": workspace_id,
            "name": workspace.get("name", ""),
            "pipeline_key": pipeline_key,
            "pipeline_label": self.pipelines.definition(pipeline_key).label,
            "job_stats": self.job_db.count_jobs_by_status(workspace_id),
            "agent_status": {
                "total": len(agents),
                "busy": busy,
                "idle": len(agents) - busy,
                "agents": [{"id": a.id, "name": a.name or a.id, "busy": a.busy} for a in agents],
            },
            "latest_run": dict(latest_run) if latest_run else None,
        }
