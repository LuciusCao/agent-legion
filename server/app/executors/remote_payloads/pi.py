"""Pi payload builder: manifest/bundle construction for remote pi executions.

Extracted from ``RemoteExecutor`` (Task 4 of the phase3 execution-decoupling
plan). The manifest layout and bundle contents are identical to the previous
inline construction in ``executors/remote.py``; ``run_token`` stays
``uuid.uuid4().hex[:12]``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors._pi_skill import get_skill_version, resolve_skill_dir
from server.app.executors.config import RemoteCapabilityConfig
from server.app.executors.remote_bundle import build_bundle
from server.app.executors.runtime_config import PiRuntimeConfig
from server.app.skills.manager import SkillManager
from server.app.workflows.pi_protocol import detect_model_error, render_command_spec

if TYPE_CHECKING:
    from server.app.executors.kinds import RuntimeDependencies
    from server.app.executors.models import ExecutionContext
    from server.app.executors.remote_payloads import PayloadBuilder


class PiPayloadBuilder:
    """Builds the pi manifest and bundle shipped to remote workers."""

    name = "pi"

    def __init__(
        self,
        config: PiRuntimeConfig,
        skill_manager: SkillManager,
        capabilities: dict[str, RemoteCapabilityConfig],
    ) -> None:
        # Lazy import: workflows.pi_config imports executors submodules, so a
        # module-level import here would cycle once executors/__init__ pulls in
        # the remote kind (same constraint as the old RemoteExecutor).
        from server.app.workflows.pi_config import PiConfig

        self.config = PiConfig.from_runtime(config)
        self.skill_manager = skill_manager
        self.capabilities = capabilities
        # Per-execution staging: build_manifest resolves the skill dir and
        # builds the manifest; build_bundle_for consumes the same objects so
        # the bundle and the submitted manifest stay identical.
        self._prepared: dict[str, tuple[Path, dict[str, Any]]] = {}

    def build_manifest(self, context: ExecutionContext) -> dict[str, Any]:
        # See __init__ for why this import is function-local.
        from server.app.executors.pi_node_execution import resolve_node_pi_config

        capability_config = self.capabilities[context.capability]
        skill_dir = resolve_skill_dir(
            self.skill_manager, capability_config.skill, context.execution_id
        )
        skill_version = get_skill_version(self.skill_manager, capability_config.skill)
        run_config, additional_prompt = resolve_node_pi_config(self.config, context.runtime)
        run_token = uuid.uuid4().hex[:12]
        manifest: dict[str, Any] = {
            "job_id": context.job_id,
            "node_key": context.node_key,
            "capability": context.capability,
            "inputs": list(context.inputs),
            "expected_outputs": list(context.expected_outputs),
            "additional_prompt": additional_prompt,
            "tools": list(capability_config.tools),
            "skill": capability_config.skill,
            "skill_version": skill_version,
            "run_token": run_token,
            "pi": {
                "binary": run_config.binary,
                "provider": run_config.provider,
                "model": run_config.model,
                "thinking": run_config.thinking,
                "timeout_seconds": run_config.timeout_seconds,
                "environment": dict(run_config.environment),
            },
        }
        self._prepared[context.execution_id] = (skill_dir, manifest)
        return manifest

    def build_bundle_for(self, context: ExecutionContext, bundle_path: Path) -> None:
        skill_dir, manifest = self._prepared[context.execution_id]
        build_bundle(
            bundle_path,
            skill_dir=skill_dir,
            job_dir=context.job_dir,
            inputs=context.inputs,
            manifest=manifest,
        )

    def build_command_spec(self, manifest: dict[str, Any]) -> dict[str, Any]:
        return render_command_spec(manifest)

    def scan_error(self, events_file: Path) -> str | None:
        return detect_model_error(events_file)

    def cleanup(self, context: ExecutionContext) -> None:
        self._prepared.pop(context.execution_id, None)
        self.skill_manager.cleanup_execution(context.execution_id)


def build_pi_payload(
    deps: RuntimeDependencies,
    capabilities: dict[str, RemoteCapabilityConfig],
    *,
    agent_id: str = "",
) -> PayloadBuilder:
    # agent_id is only consumed by the openclaw payload; pi ignores it.
    return PiPayloadBuilder(deps.pi_runtime, deps.skill_manager, capabilities)
