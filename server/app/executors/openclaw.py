from __future__ import annotations

import logging
from pathlib import Path

from server.app.executors.config import OpenClawCapabilityConfig, OpenClawExecutorConfig
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.runtime_config import OpenClawRuntimeConfig
from server.app.pipeline.openclaw import OpenClawRunner, SkillSafetyConfig

logger = logging.getLogger(__name__)


def build_openclaw_executor(
    executor_id: str,
    runtime: OpenClawRuntimeConfig,
    config: OpenClawExecutorConfig,
) -> OpenClawExecutor:
    """Build an OpenClawExecutor with the executor's agent_id injected into the runner."""
    command_template = _inject_agent_id(list(runtime.command_template), config.agent_id)
    skill_safety = (
        SkillSafetyConfig(
            enabled=runtime.skill_safety.enabled,
            repos=list(runtime.skill_safety.repos),
        )
        if runtime.skill_safety is not None
        else None
    )
    isolated_root = (
        Path(runtime.isolated_workspace_root) if runtime.isolated_workspace_root else None
    )
    runner = OpenClawRunner(
        command_template=command_template,
        cwd=Path(runtime.cwd),
        timeout_seconds=runtime.timeout_seconds,
        skill_safety=skill_safety,
        isolated_workspace_root=isolated_root,
        agent_id=config.agent_id,
    )
    return OpenClawExecutor(
        id=executor_id,
        runner=runner,
        capabilities=config.capabilities,
    )


def _inject_agent_id(command_template: list[str], agent_id: str) -> list[str]:
    """Return a copy of *command_template* with the agent id set to *agent_id*."""
    template = list(command_template)
    for i, part in enumerate(template):
        if part == "{agent_id}":
            template[i] = agent_id
    for i, part in enumerate(template):
        if part == "--agent" and i + 1 < len(template):
            template[i + 1] = agent_id
            break
    return template


class OpenClawExecutor:
    """Adapter that runs OpenClaw agent nodes through a configured OpenClawRunner."""

    kind = "openclaw"

    def __init__(
        self,
        id: str,
        runner: OpenClawRunner,
        capabilities: dict[str, OpenClawCapabilityConfig],
    ) -> None:
        self.id = id
        self.runner = runner
        self.capabilities = capabilities
        self._cancelled: set[str] = set()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        if context.execution_id in self._cancelled:
            self._cancelled.discard(context.execution_id)
            return ExecutionResult(
                status="cancelled",
                exit_code=-1,
                error_message="execution was cancelled before starting",
                log_path=str(context.log_path),
            )

        capability_config = self.capabilities.get(context.capability)
        if capability_config is None:
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=f"capability {context.capability!r} is not supported",
                log_path=str(context.log_path),
            )

        prompt_lines = [
            f"Use the installed skill {capability_config.skill}.",
            "",
            f"Execution ID: {context.execution_id}",
            f"Workspace ID: {context.workspace_id}",
            f"Job ID: {context.job_id}",
            f"Pipeline: {context.pipeline_key}",
            f"Node: {context.node_key}",
            f"Capability: {context.capability}",
            f"Working directory: {context.job_dir}",
            "",
            "Declared inputs:",
        ]
        for name in context.inputs:
            prompt_lines.append(f"- {name}")
        prompt_lines.append("")
        prompt_lines.append("Required outputs:")
        for name in context.expected_outputs:
            prompt_lines.append(f"- {name}")
        prompt_lines.append("")
        prompt_lines.append(
            "Write required outputs directly into the working directory. "
            "Do not modify inputs or create undeclared root-level artifacts. "
            "Finish after all required outputs are written and correct."
        )
        prompt_text = "\n".join(prompt_lines) + "\n"

        context.job_dir.mkdir(parents=True, exist_ok=True)
        context.log_path.parent.mkdir(parents=True, exist_ok=True)

        result = self.runner.run_prompt(
            execution_id=context.execution_id,
            work_dir=context.job_dir,
            prompt_text=prompt_text,
            expected_outputs=context.expected_outputs,
            log_path=context.log_path,
        )

        produced = tuple(
            name for name in context.expected_outputs if (context.job_dir / name).is_file()
        )
        return ExecutionResult(
            status=result.status,
            exit_code=result.exit_code,
            error_message=result.error_message,
            command=tuple(result.command),
            log_path=str(context.log_path),
            produced_artifacts=produced,
        )

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)
