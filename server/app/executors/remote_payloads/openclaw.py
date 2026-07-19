"""OpenClaw payload builder: manifest/bundle construction for remote openclaw runs.

The prompt text is moved verbatim from the local ``OpenClawExecutor`` prompt
construction (``executors/openclaw.py``), rendered manifest-driven with the
``{job_dir}`` placeholder so workers substitute local paths. ``agent_id`` is
injected into ``command_template`` at manifest time via the shared
``executors.openclaw._inject_agent_id``; the command spec never carries an
environment section or secrets. OpenClaw skills run installed on the worker,
so the bundle packs no skill snapshot and ``skill_version`` stays empty.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.config import RemoteCapabilityConfig
from server.app.executors.openclaw import _inject_agent_id
from server.app.executors.remote_bundle import build_bundle
from server.app.executors.runtime_config import OpenClawRuntimeConfig
from server.app.workflows.pi_protocol import JOB_DIR_PLACEHOLDER, PROMPT_FILE_PLACEHOLDER

if TYPE_CHECKING:
    from server.app.executors.kinds import RuntimeDependencies
    from server.app.executors.models import ExecutionContext
    from server.app.executors.remote_payloads import PayloadBuilder

# OpenClaw command templates may reference the prompt inline instead of a file.
PROMPT_TEXT_PLACEHOLDER = "{prompt_text}"


def _build_prompt(manifest: dict[str, Any]) -> str:
    prompt_lines = [
        f"Use the installed skill {manifest['skill']}.",
        "",
        f"Execution ID: {manifest['execution_id']}",
        f"Workspace ID: {manifest['workspace_id']}",
        f"Job ID: {manifest['job_id']}",
        f"Workflow: {manifest['workflow_key']}",
        f"Node: {manifest['node_key']}",
        f"Capability: {manifest['capability']}",
        f"Working directory: {JOB_DIR_PLACEHOLDER}",
        "",
        "Declared inputs:",
    ]
    for name in manifest["inputs"]:
        prompt_lines.append(f"- {name}")
    prompt_lines.append("")
    prompt_lines.append("Required outputs:")
    for name in manifest["expected_outputs"]:
        prompt_lines.append(f"- {name}")
    prompt_lines.append("")
    prompt_lines.append(
        "Write required outputs directly into the working directory. "
        "Do not modify inputs or create undeclared root-level artifacts. "
        "Finish after all required outputs are written and correct."
    )
    return "\n".join(prompt_lines) + "\n"


class OpenClawPayloadBuilder:
    """Builds the openclaw manifest and bundle shipped to remote workers."""

    name = "openclaw"

    def __init__(
        self,
        runtime: OpenClawRuntimeConfig,
        agent_id: str,
        capabilities: dict[str, RemoteCapabilityConfig],
    ) -> None:
        self._runtime = runtime
        self._agent_id = agent_id
        self.capabilities = capabilities
        # Per-execution staging: build_manifest renders the manifest;
        # build_bundle_for consumes the same object so the bundle and the
        # submitted manifest stay identical.
        self._prepared: dict[str, dict[str, Any]] = {}

    def build_manifest(self, context: ExecutionContext) -> dict[str, Any]:
        capability_config = self.capabilities[context.capability]
        command_template = _inject_agent_id(list(self._runtime.command_template), self._agent_id)
        manifest: dict[str, Any] = {
            "job_id": context.job_id,
            "node_key": context.node_key,
            "capability": context.capability,
            "execution_id": context.execution_id,
            "workspace_id": context.workspace_id,
            "workflow_key": context.workflow_key,
            "inputs": list(context.inputs),
            "expected_outputs": list(context.expected_outputs),
            "skill": capability_config.skill,
            # The skill is installed worker-side; its version is unknown here.
            "skill_version": "",
            "run_token": uuid.uuid4().hex[:12],
            "openclaw": {
                "command_template": command_template,
                "cwd": self._runtime.cwd,
                "timeout_seconds": self._runtime.timeout_seconds,
                "cancellation_grace_seconds": self._runtime.cancellation_grace_seconds,
                "isolated_workspace_root": self._runtime.isolated_workspace_root,
            },
        }
        self._prepared[context.execution_id] = manifest
        return manifest

    def build_bundle_for(self, context: ExecutionContext, bundle_path: Path) -> None:
        build_bundle(
            bundle_path,
            job_dir=context.job_dir,
            inputs=context.inputs,
            manifest=self._prepared[context.execution_id],
        )

    def build_command_spec(self, manifest: dict[str, Any]) -> dict[str, Any]:
        command = list(manifest["openclaw"]["command_template"])
        if not any(
            PROMPT_FILE_PLACEHOLDER in part or PROMPT_TEXT_PLACEHOLDER in part for part in command
        ):
            command.append(PROMPT_FILE_PLACEHOLDER)
        return {
            "version": 1,
            "prompt": _build_prompt(manifest),
            "command": command,
        }

    def scan_error(self, events_file: Path) -> str | None:
        # OpenClaw has no events protocol; nothing to scan.
        return None

    def cleanup(self, context: ExecutionContext) -> None:
        self._prepared.pop(context.execution_id, None)


def build_openclaw_payload(
    deps: RuntimeDependencies,
    capabilities: dict[str, RemoteCapabilityConfig],
    *,
    agent_id: str = "",
) -> PayloadBuilder:
    return OpenClawPayloadBuilder(deps.openclaw_runtime, agent_id, capabilities)
