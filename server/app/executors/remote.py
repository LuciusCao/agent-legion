from __future__ import annotations

import logging
import uuid
from typing import Any

from server.app.executors._pi_skill import get_skill_version, prepare_execution, resolve_skill_dir
from server.app.executors.config import RemoteCapabilityConfig
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.pi_node_execution import resolve_node_pi_config
from server.app.executors.remote_broker import (
    RemoteExecutionBroker,
    RemoteExecutionPayload,
    RemoteOutcome,
)
from server.app.executors.remote_bundle import build_bundle, extract_result_archive
from server.app.executors.runtime_config import PiRuntimeConfig
from server.app.skills.manager import SkillManager
from server.app.workflows.pi_config import PiConfig

logger = logging.getLogger(__name__)


class RemoteExecutor:
    """Runs pi executions on remote worker machines via the RemoteExecutionBroker.

    execute() packages the run into a bundle, enqueues it, and blocks until a
    worker reports the result; the scheduler/lease pipeline is unchanged.
    """

    kind = "remote"

    def __init__(
        self,
        id: str,
        config: PiRuntimeConfig,
        skill_manager: SkillManager,
        capabilities: dict[str, RemoteCapabilityConfig],
        broker: RemoteExecutionBroker,
    ) -> None:
        self.id = id
        self.config = PiConfig.from_runtime(config)
        self.skill_manager = skill_manager
        self.capabilities = capabilities
        self._broker = broker
        self._cancelled: set[str] = set()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        capability_config, early_result = prepare_execution(
            self._cancelled, self.capabilities, context
        )
        if early_result is not None:
            return early_result
        assert capability_config is not None
        bundle_path = self._broker.bundle_dir / f"{context.execution_id}.tar.gz"
        archive_path = self._broker.bundle_dir / f"{context.execution_id}.result.tar.gz"
        try:
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
            build_bundle(
                bundle_path,
                skill_dir=skill_dir,
                job_dir=context.job_dir,
                inputs=context.inputs,
                manifest=manifest,
            )
            self._broker.submit(
                RemoteExecutionPayload(
                    execution_id=context.execution_id,
                    lease_id=context.lease_id,
                    job_id=context.job_id,
                    node_key=context.node_key,
                    capability=context.capability,
                    bundle_name=bundle_path.name,
                    manifest=manifest,
                )
            )
            outcome = self._broker.wait_result(context.execution_id)
            return self._to_result(outcome, context, run_token, skill_version)
        except Exception as exc:
            logger.exception("remote dispatch failed for %s", context.execution_id)
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=f"remote dispatch error: {exc}",
                log_path=str(context.log_path),
            )
        finally:
            self.skill_manager.cleanup_execution(context.execution_id)
            bundle_path.unlink(missing_ok=True)
            archive_path.unlink(missing_ok=True)

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)
        self._broker.cancel(execution_id)

    def _to_result(
        self,
        outcome: RemoteOutcome,
        context: ExecutionContext,
        run_token: str,
        skill_version: str,
    ) -> ExecutionResult:
        run_dir = context.job_dir / "runs" / context.node_key / run_token
        session_dir = run_dir / "session"
        if outcome.status != "cancelled" and outcome.result_archive_name:
            try:
                extract_result_archive(
                    self._broker.bundle_dir / outcome.result_archive_name, context.job_dir
                )
            except Exception as exc:
                logger.exception("failed to unpack remote result for %s", context.execution_id)
                return ExecutionResult(
                    status="failed",
                    exit_code=1,
                    error_message=f"failed to unpack remote result: {exc}",
                    command=outcome.command,
                    log_path=str(context.log_path),
                    skill_version=skill_version,
                )
        produced = tuple(
            name for name in context.expected_outputs if (context.job_dir / name).is_file()
        )
        status = outcome.status
        error_message = outcome.error_message
        exit_code = outcome.exit_code
        if status == "completed":
            missing = [o for o in context.expected_outputs if o not in produced]
            if missing:
                status = "failed"
                exit_code = 1
                error_message = f"Missing outputs after Pi run: {', '.join(missing)}"
        return ExecutionResult(
            status=status,
            exit_code=exit_code,
            error_message=error_message,
            command=outcome.command,
            log_path=str(context.log_path),
            run_dir=str(run_dir) if run_dir.is_dir() else "",
            session_dir=str(session_dir) if session_dir.is_dir() else "",
            skill_version=skill_version,
            produced_artifacts=produced,
        )
