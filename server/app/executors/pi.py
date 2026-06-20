from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from server.app.executors._log_utils import copy_pi_logs
from server.app.executors._pi_result import to_execution_result
from server.app.executors.cancellation import CancellationToken, SubprocessTracker
from server.app.executors.config import PiCapabilityConfig
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.runtime_config import PiRuntimeConfig
from server.app.workflows.pi_runner import PiConfig, PiRunner
from server.app.workflows.skills import resolve_workflow_skill

logger = logging.getLogger(__name__)


class PiExecutor:
    """Adapter that runs Pi agent nodes through the persistence-neutral PiRunner."""

    kind = "pi"

    def __init__(
        self,
        id: str,
        config: PiRuntimeConfig,
        skill_root: Path,
        capabilities: dict[str, PiCapabilityConfig],
    ) -> None:
        self.id = id
        self.config = PiConfig.from_runtime(config)
        self.skill_root = skill_root
        self.capabilities = capabilities
        self._runner = PiRunner(self.config, skill_root)
        self._cancelled: set[str] = set()
        self._tracker = SubprocessTracker(grace_seconds=self.config.cancellation_grace_seconds)

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

        try:
            skill_dir = resolve_workflow_skill(self.skill_root, capability_config.skill)
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

        context.job_dir.mkdir(parents=True, exist_ok=True)
        context.log_path.parent.mkdir(parents=True, exist_ok=True)

        raw_token = (
            context.runtime.get("cancellation") if isinstance(context.runtime, Mapping) else None
        )
        token = raw_token if isinstance(raw_token, CancellationToken) else None
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
        )

        copy_pi_logs(result.run_dir, context.log_path)
        return to_execution_result(result, context)

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)
        self._tracker.cancel(execution_id)
