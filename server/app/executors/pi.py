from __future__ import annotations

import logging
from collections.abc import Mapping

from server.app.executors._pi_result import to_execution_result
from server.app.executors._pi_skill import get_skill_version, prepare_execution, resolve_skill_dir
from server.app.executors._shard_contract import shard_prompt_section
from server.app.executors.cancellation import CancellationToken, SubprocessTracker
from server.app.executors.config import PiCapabilityConfig, PiExecutorConfig
from server.app.executors.kinds import ExecutorKind, RuntimeDependencies, register_kind
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.pi_node_execution import resolve_node_pi_config
from server.app.executors.runtime_config import PiRuntimeConfig
from server.app.skills.manager import SkillManager
from server.app.workflows.pi_runner import PiConfig, PiRunner

logger = logging.getLogger(__name__)


class PiExecutor:
    """Adapter that runs Pi agent nodes through the persistence-neutral PiRunner."""

    kind = "pi"

    def __init__(
        self,
        id: str,
        config: PiRuntimeConfig,
        skill_manager: SkillManager,
        capabilities: dict[str, PiCapabilityConfig],
    ) -> None:
        self.id = id
        self.config = PiConfig.from_runtime(config)
        self.skill_manager = skill_manager
        self.capabilities = capabilities
        self._runner = PiRunner(self.config, skill_manager.base_dir)
        self._cancelled: set[str] = set()
        self._tracker = SubprocessTracker(grace_seconds=self.config.cancellation_grace_seconds)

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        capability_config, early_result = prepare_execution(
            self._cancelled,
            self.capabilities,
            context,
        )
        if early_result is not None:
            return early_result
        assert capability_config is not None

        try:
            skill_dir = resolve_skill_dir(
                self.skill_manager,
                capability_config.skill,
                context.execution_id,
            )
        except Exception as exc:
            logger.exception(
                "Failed to resolve Pi skill %s for execution %s",
                capability_config.skill,
                context.execution_id,
            )
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=str(exc),
                log_path=str(context.log_path),
            )

        try:
            context.job_dir.mkdir(parents=True, exist_ok=True)
            context.log_path.parent.mkdir(parents=True, exist_ok=True)

            maybe_token = (
                context.runtime.get("cancellation")
                if isinstance(context.runtime, Mapping)
                else None
            )
            token = maybe_token if isinstance(maybe_token, CancellationToken) else None

            skill_version = get_skill_version(self.skill_manager, capability_config.skill)
            run_config, additional_prompt = resolve_node_pi_config(self.config, context.runtime)
            additional_prompt = (additional_prompt or "") + shard_prompt_section(context.runtime)

            result = self._runner.run(
                job=dict(context.job),
                node_key=context.node_key,
                skill_dir=skill_dir,
                inputs=list(context.inputs),
                outputs=list(context.expected_outputs),
                tools=list(capability_config.tools),
                persist_run=False,
                job_dir=context.job_dir,
                execution_id=context.execution_id,
                cancellation_token=token,
                tracker=self._tracker,
                skill_version=skill_version,
                config=run_config,
                additional_prompt=additional_prompt,
            )

            return to_execution_result(result, context)
        finally:
            self.skill_manager.cleanup_execution(context.execution_id)

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)
        self._tracker.cancel(execution_id)


def build_pi_executor(
    executor_id: str, config: PiExecutorConfig, deps: RuntimeDependencies
) -> PiExecutor:
    return PiExecutor(
        id=executor_id,
        config=deps.pi_runtime,
        skill_manager=deps.skill_manager,
        capabilities=config.capabilities,
    )


register_kind(ExecutorKind(name="pi", config_model=PiExecutorConfig, factory=build_pi_executor))
