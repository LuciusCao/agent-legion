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
from server.app.executors.remote_payloads import PayloadBuilder, get_payload_builder

if TYPE_CHECKING:
    from server.app.executors.remote_broker import RemoteExecutionBroker

logger = logging.getLogger(__name__)


class RemoteExecutor:
    """Submits executions to remote worker machines via the RemoteExecutionBroker.

    execute() asks the injected payload builder for the manifest and bundle,
    enqueues the run, and returns None immediately: it never waits for the
    result. Completion is driven by broker completion callbacks (see
    ``remote_completion.RemoteCompletionHandler``), which unpack the result
    archive and finish the lease.
    """

    kind = "remote"
    submit_only = True

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

    def execute(self, context: ExecutionContext) -> ExecutionResult | None:
        # Lazy import: remote_broker transitively imports executors submodules,
        # so a module-level import here would create an import cycle.
        from server.app.executors.remote_broker import RemoteExecutionPayload

        capability_config, early_result = prepare_execution(
            self._cancelled, self.capabilities, context
        )
        if early_result is not None:
            return early_result
        assert capability_config is not None
        try:
            manifest = self._payload_builder.build_manifest(context)
            bundle_path = self._broker.bundle_dir / f"{context.execution_id}.tar.gz"
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
            return None
        except Exception as exc:
            logger.exception("remote dispatch failed for %s", context.execution_id)
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=f"remote dispatch error: {exc}",
                log_path=str(context.log_path),
            )
        finally:
            # The builder releases the per-execution skill snapshot; bundle and
            # result-archive files are owned by the broker/completion callback.
            self._payload_builder.cleanup(context)
            self._cancelled.discard(context.execution_id)

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)
        self._broker.cancel(execution_id)


def build_remote_executor(
    executor_id: str, config: RemoteExecutorConfig, deps: RuntimeDependencies
) -> RemoteExecutor:
    if deps.remote_broker is None:
        raise ExecutorKindError(
            f"remote executor {executor_id!r} requires a remote execution broker"
        )
    builder = get_payload_builder(config.payload)(
        deps, config.capabilities, agent_id=config.agent_id
    )
    return RemoteExecutor(
        id=executor_id,
        payload_builder=builder,
        capabilities=config.capabilities,
        broker=deps.remote_broker,
    )


register_kind(
    ExecutorKind(name="remote", config_model=RemoteExecutorConfig, factory=build_remote_executor)
)
