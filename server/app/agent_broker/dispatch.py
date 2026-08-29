from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from server.app.agent_broker.agent_artifacts import stage_agent_inputs
from server.app.agent_broker.agent_bundle import build_agent_bundle, cleanup_bundle_on_error
from server.app.agent_broker.broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.dispatch_pool import AgentEnqueuePool
from server.app.agent_catalog import AgentDefinition
from server.app.config_schema import manifest_safe_config
from server.app.executors.models import ExecutionContext
from server.app.services.artifact_store import ArtifactStore
from server.app.settings import Settings
from server.app.skills.runtime import build_skill_manager, get_skill_version, resolve_skill_dir
from server.app.workflows.pi_protocol import render_command_spec
from server.app.workflows.schema import WorkflowNode

# Retired workflows.pi.timeout_seconds (yaml governance): the execution
# timeout is a product constant now, not configuration.
EXECUTION_TIMEOUT_SECONDS = 1800

_RUNTIME_BINARIES = {"pi": "pi", "velites": "velites"}


def resolve_execution_block(node: WorkflowNode, runtime: str) -> dict[str, Any]:
    """Resolve the manifest ``execution`` block (strict, node-only source).

    provider/model resolve from the node-level execution only — the loader
    has already merged the workflow top-level execution defaults into the
    node, so the value seen here is the effective one; either one missing
    fails the enqueue with an actionable error (agent config governance,
    workspace-level defaults retired at schema v64). thinking stays optional
    — empty means the runtime decides. The runtime pins the command builder
    (EXEC-RUNTIME-DISPATCH-001); unknown runtimes fail fast so no manifest
    is ever frozen with an unbuildable command spec.
    """
    binary = _RUNTIME_BINARIES.get(runtime)
    if binary is None:
        raise ValueError(
            f"Agent runtime {runtime!r} is not implemented yet (supported runtimes: pi, velites)"
        )
    provider = node.execution.provider
    model = node.execution.model
    if not provider:
        raise ValueError(
            f"node {node.key} requires a provider: set the node execution provider "
            "in Studio or a workflow top-level execution default"
        )
    if not model:
        raise ValueError(
            f"node {node.key} requires a model: set the node execution model "
            "in Studio or a workflow top-level execution default"
        )
    return {
        "binary": binary,
        "provider": provider,
        "model": model,
        "thinking": node.execution.thinking,
        "timeout_seconds": EXECUTION_TIMEOUT_SECONDS,
        "no_sandbox": False,
    }


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
        # The broker carries the connect source (facade or DSN) in production
        # wiring; the settings DSN is the fallback for test-only brokers.
        broker_dsn = getattr(broker, "database_dsn", None) or settings.database_url
        self.skill_manager = build_skill_manager(broker_dsn, settings.skills_runs_dir)
        enqueue_config = settings.executor_runtime.agent_enqueue
        self.enqueue_pool = AgentEnqueuePool(
            workers=enqueue_config.workers, max_pending=enqueue_config.max_pending
        )

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
        pinned_agent_version: int | None = None,
    ) -> bool:
        if self.broker.has_active_request(str(job["id"]), node.key):
            return False
        execution = resolve_execution_block(node, definition.runtime)
        execution_id = str(uuid.uuid4())
        skill_dir = resolve_skill_dir(self.skill_manager, definition.skill, execution_id)
        try:
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
                "execution": execution,
            }
            # Quality replay audit trail: the pinned immutable version that
            # produced this manifest (absent on the normal published path).
            if pinned_agent_version is not None:
                manifest["agent_version"] = pinned_agent_version
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
            # D12: the object-storage artifact channel (presigned PUT/GET) is
            # injected at CLAIM time (routes/agent_worker_claims.py), memory
            # only — no URL ever persists in the queued manifest or bundle,
            # so a long queue backlog cannot strand expired URLs.
            manifest["command_spec"] = render_command_spec(manifest)
            if self.broker.bundle_dir is None:
                raise RuntimeError("Agent bundle directory is not configured")
            bundle_path = self.broker.bundle_dir / f"{execution_id}.tar.gz"
            with cleanup_bundle_on_error(bundle_path):
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
                        pinned_agent_version=pinned_agent_version,
                    )
                )
                if queued is None:
                    bundle_path.unlink(missing_ok=True)
                return queued is not None
        finally:
            self.skill_manager.cleanup_execution(execution_id)
