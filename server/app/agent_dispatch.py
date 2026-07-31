from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from server.app.agent_artifacts import stage_agent_inputs
from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_bundle import build_agent_bundle
from server.app.agent_catalog import AgentDefinition
from server.app.agent_dispatch_pool import AgentEnqueuePool
from server.app.config_schema import manifest_safe_config
from server.app.executors._pi_skill import build_skill_manager, get_skill_version, resolve_skill_dir
from server.app.executors.models import ExecutionContext
from server.app.services.artifact_store import ArtifactStore
from server.app.settings import Settings
from server.app.workflows.pi_config import PiConfig
from server.app.workflows.pi_protocol import render_command_spec
from server.app.workflows.schema import WorkflowNode


class AgentDispatchService:
    """Build immutable Agent payloads and enqueue them without starting a node run."""

    def __init__(
        self,
        settings: Settings,
        broker: AgentExecutionBroker,
        artifact_store: ArtifactStore,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.artifact_store = artifact_store
        self.skill_manager = build_skill_manager(settings.root_dir)
        self.enqueue_pool = AgentEnqueuePool()

    def enqueue(
        self,
        *,
        agent_id: str,
        definition: AgentDefinition,
        workspace: dict[str, Any],
        job: dict[str, Any],
        workflow_key: str,
        node: WorkflowNode,
        job_dir: Path,
        log_path: Path,
        inputs: tuple[str, ...],
        node_config: dict[str, Any] | None = None,
    ) -> bool:
        if self.broker.has_active_request(str(job["id"]), node.key):
            return False
        if definition.runtime != "pi":
            raise ValueError(f"Agent runtime {definition.runtime!r} is not implemented yet")
        execution_id = str(uuid.uuid4())
        skill_dir = resolve_skill_dir(self.skill_manager, definition.skill, execution_id)
        try:
            pi = PiConfig.from_runtime(self.settings.executor_runtime.workflows.pi)
            manifest: dict[str, Any] = {
                "execution_id": execution_id,
                "workspace_id": workspace["id"],
                "job_id": job["id"],
                "workflow_key": workflow_key,
                "node_key": node.key,
                "agent_id": agent_id,
                "agent_definition_hash": definition.definition_hash(),
                "runtime": definition.runtime,
                "capability": definition.capability,
                "inputs": list(inputs),
                "expected_outputs": list(node.outputs),
                "additional_prompt": node.execution.prompt,
                # CONFIG-MANIFEST-001: only schema-whitelisted, non-secret keys.
                "config": manifest_safe_config(definition.config_schema, node_config or {}),
                "tools": list(definition.tools),
                "skill": definition.skill,
                "skill_version": get_skill_version(self.skill_manager, definition.skill),
                "log_path": str(log_path),
                "pi": {
                    "binary": pi.binary,
                    "provider": node.execution.provider or pi.provider,
                    "model": node.execution.model or pi.model,
                    "thinking": node.execution.thinking or pi.thinking,
                    "timeout_seconds": pi.timeout_seconds,
                },
                "pi_defaults": {
                    "provider": pi.provider,
                    "model": pi.model,
                    "thinking": pi.thinking,
                },
            }
            context = ExecutionContext(
                execution_id=execution_id,
                lease_id="",
                node_run_id=0,
                executor_id=f"agent:{agent_id}",
                workspace_id=str(workspace["id"]),
                job_id=str(job["id"]),
                workflow_key=workflow_key,
                node_key=node.key,
                capability=node.capability,
                workspace=workspace,
                job=job,
                job_dir=job_dir,
                log_path=log_path,
                inputs=inputs,
                expected_outputs=tuple(node.outputs),
                runtime={"node_execution": asdict(node.execution)},
            )
            stage_agent_inputs(self.artifact_store, context, manifest)
            manifest["command_spec"] = render_command_spec(manifest)
            if self.broker.bundle_dir is None:
                raise RuntimeError("Agent bundle directory is not configured")
            bundle_path = self.broker.bundle_dir / f"{execution_id}.tar.gz"
            build_agent_bundle(bundle_path, skill_dir=skill_dir, manifest=manifest)
            manifest["bundle_name"] = bundle_path.name
            queued = self.broker.enqueue(
                AgentExecutionRequest(
                    workspace_id=str(workspace["id"]),
                    job_id=str(job["id"]),
                    workflow_key=workflow_key,
                    node_key=node.key,
                    agent_id=agent_id,
                    agent_definition_hash=definition.definition_hash(),
                    manifest=manifest,
                    execution_id=execution_id,
                )
            )
            if queued is None:
                bundle_path.unlink(missing_ok=True)
            return queued is not None
        finally:
            self.skill_manager.cleanup_execution(execution_id)
