from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.app.executors._pi_skill import prepare_execution
from server.app.executors.config import RemoteCapabilityConfig, RemoteExecutorConfig
from server.app.executors.kinds import (
    ExecutorKind,
    ExecutorKindError,
    RuntimeDependencies,
    register_kind,
)
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.remote_bundle import extract_result_archive
from server.app.executors.remote_payloads import PayloadBuilder, get_payload_builder

if TYPE_CHECKING:
    from server.app.executors.remote_broker import RemoteExecutionBroker, RemoteOutcome

logger = logging.getLogger(__name__)


class RemoteExecutor:
    """Runs executions on remote worker machines via the RemoteExecutionBroker.

    execute() asks the injected payload builder for the manifest and bundle,
    enqueues the run, and blocks until a worker reports the result; the
    scheduler/lease pipeline is unchanged.
    """

    kind = "remote"

    def __init__(
        self,
        id: str,
        payload_builder: PayloadBuilder,
        capabilities: dict[str, RemoteCapabilityConfig],
        broker: RemoteExecutionBroker,
    ) -> None:
        self.id = id
        self._payload_builder = payload_builder
        self.capabilities = capabilities
        self._broker = broker
        self._cancelled: set[str] = set()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        # Lazy import: remote_broker transitively imports executors submodules,
        # so a module-level import here would create an import cycle.
        from server.app.executors.remote_broker import RemoteExecutionPayload

        capability_config, early_result = prepare_execution(
            self._cancelled, self.capabilities, context
        )
        if early_result is not None:
            return early_result
        assert capability_config is not None
        bundle_path = self._broker.bundle_dir / f"{context.execution_id}.tar.gz"
        archive_path = self._broker.bundle_dir / f"{context.execution_id}.result.tar.gz"
        try:
            manifest = self._payload_builder.build_manifest(context)
            self._payload_builder.build_bundle_for(context, bundle_path)
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
            # A cancel landing between prepare_execution and submit is dropped by the
            # broker (unknown execution id); re-check here so it is not lost.
            if context.execution_id in self._cancelled:
                self._broker.cancel(context.execution_id)
            outcome = self._broker.wait_result(context.execution_id)
            if outcome.result_archive_name:
                archive_path = self._broker.bundle_dir / outcome.result_archive_name
            return self._to_result(
                outcome,
                context,
                str(manifest["run_token"]),
                str(manifest["skill_version"]),
            )
        except Exception as exc:
            logger.exception("remote dispatch failed for %s", context.execution_id)
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=f"remote dispatch error: {exc}",
                log_path=str(context.log_path),
            )
        finally:
            self._payload_builder.cleanup(context)
            self._cancelled.discard(context.execution_id)
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
                    runner=outcome.worker_id,
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
            runner=outcome.worker_id,
        )


def build_remote_executor(
    executor_id: str, config: RemoteExecutorConfig, deps: RuntimeDependencies
) -> RemoteExecutor:
    if deps.remote_broker is None:
        raise ExecutorKindError(
            f"remote executor {executor_id!r} requires a remote execution broker"
        )
    builder = get_payload_builder(config.payload)(deps, config.capabilities)
    return RemoteExecutor(
        id=executor_id,
        payload_builder=builder,
        capabilities=config.capabilities,
        broker=deps.remote_broker,
    )


register_kind(
    ExecutorKind(name="remote", config_model=RemoteExecutorConfig, factory=build_remote_executor)
)
