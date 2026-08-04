from __future__ import annotations

import logging
import multiprocessing
import threading
from collections.abc import Iterable, Mapping
from typing import Any

from server.app.executors import _local_isolated, _local_thread
from server.app.executors._local_isolated import (  # noqa: F401  (re-exports)
    LocalHandler,
    _handler_key,
    _resolve_handler,
    _run_handler,
    _watch_parent_token,
)
from server.app.executors.cancellation import CancellationToken
from server.app.executors.config import LocalCapabilityConfig, LocalExecutorConfig
from server.app.executors.kinds import ExecutorKind, RuntimeDependencies, register_kind
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.workflows.resource_providers import ResourceProviderDeclarations

logger = logging.getLogger(__name__)

# Fallback wall-clock limit for one isolated local run; a capability's
# LocalCapabilityConfig.timeout_seconds overrides it. This is the backstop
# that keeps a hung handler from living forever as an orphaned child.
DEFAULT_TIMEOUT_SECONDS = 3600.0


class LocalExecutor:
    """Adapter that runs repository-owned local handlers inside the workspace runtime."""

    kind = "local"

    def __init__(
        self,
        id: str,
        handlers: Mapping[str, LocalHandler],
        settings_config: Mapping[str, Any] | None = None,
        job_db: Any | None = None,
        cancellation_grace_seconds: float = 5,
        resource_providers: ResourceProviderDeclarations | None = None,
        capability_timeouts: Mapping[str, float] | None = None,
        default_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        thread_capabilities: Iterable[str] | None = None,
    ) -> None:
        self.id = id
        self.handlers = dict(handlers)
        self._handler_keys: dict[str, str] = {}
        unsafe_capabilities: list[str] = []
        for capability, handler in self.handlers.items():
            key = _handler_key(handler)
            if key is None:
                unsafe_capabilities.append(capability)
                continue
            try:
                resolved = _resolve_handler(key)
            except (AttributeError, ImportError, ValueError):
                unsafe_capabilities.append(capability)
                continue
            if resolved is not handler:
                unsafe_capabilities.append(capability)
                continue
            self._handler_keys[capability] = key
        if unsafe_capabilities:
            raise ValueError(
                "Local executor handlers must be importable module-level functions: "
                + ", ".join(sorted(unsafe_capabilities))
            )
        self.settings_config = dict(settings_config) if settings_config is not None else {}
        self.resource_providers = resource_providers or ResourceProviderDeclarations()
        self.job_db = job_db
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self._capability_timeouts = dict(capability_timeouts or {})
        self._default_timeout_seconds = default_timeout_seconds
        self._thread_capabilities = set(thread_capabilities or ())
        self._cancelled: set[str] = set()
        self._tokens: dict[str, CancellationToken] = {}
        self._watchers: dict[str, threading.Thread] = {}

    def supports(self, capability: str) -> bool:
        return capability in self.handlers

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        if context.execution_id in self._cancelled:
            self._cancelled.discard(context.execution_id)
            return ExecutionResult(
                status="cancelled",
                exit_code=-1,
                error_message="execution was cancelled before starting",
                log_path=str(context.log_path),
            )

        handler = self.handlers.get(context.capability)
        if handler is None:
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=f"capability {context.capability!r} is not supported",
                log_path=str(context.log_path),
            )

        if context.capability in self._thread_capabilities:
            return _local_thread.execute_in_thread(self, context, handler)

        key = self._handler_keys.get(context.capability)
        if key is None:  # guarded by constructor validation
            raise RuntimeError(f"Local handler for {context.capability!r} is not importable")
        return self._execute_isolated(context, key)

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)
        token = self._tokens.get(execution_id)
        if token is not None:
            token.cancel()

    def _build_runtime(self, context: ExecutionContext, token: CancellationToken) -> dict[str, Any]:
        runtime: dict[str, Any] = {
            "job_dir": context.job_dir,
            "log_path": context.log_path,
            "inputs": context.inputs,
            "expected_outputs": context.expected_outputs,
            "capability": context.capability,
            "node_key": context.node_key,
            "workflow_key": context.workflow_key,
            "execution_id": context.execution_id,
            "workspace_id": context.workspace_id,
            "workspace": dict(context.workspace),
            "job": dict(context.job),
            "settings_config": self.settings_config,
            "resource_providers": self.resource_providers,
            "node_config": dict(context.node_config),
            "cancellation": token,
        }
        if self.job_db is not None:
            runtime["_job_db_path"] = str(getattr(self.job_db, "path", ""))
            runtime["_jobs_dir"] = str(getattr(self.job_db, "jobs_dir", ""))
        return runtime

    def _execute_isolated(self, context: ExecutionContext, handler_key: str) -> ExecutionResult:
        return _local_isolated.execute_isolated(self, context, handler_key)

    def _terminate_child(self, process: multiprocessing.process.BaseProcess) -> None:
        process.terminate()
        process.join(timeout=self.cancellation_grace_seconds)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)

    def _check_outputs(self, context: ExecutionContext) -> ExecutionResult:
        missing = [
            name for name in context.expected_outputs if not (context.job_dir / name).is_file()
        ]
        if missing:
            error_message = f"Missing outputs after local run: {', '.join(missing)}"
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=error_message,
                log_path=str(context.log_path),
            )

        produced = tuple(
            name for name in context.expected_outputs if (context.job_dir / name).is_file()
        )
        return ExecutionResult(
            status="completed",
            exit_code=0,
            log_path=str(context.log_path),
            produced_artifacts=produced,
        )


def _resolve_local_handlers(
    executor_id: str,
    capabilities: dict[str, LocalCapabilityConfig],
    available_handlers: Mapping[str, LocalHandler],
) -> dict[str, LocalHandler]:
    """Map capability names to handler functions supplied by the caller."""
    handlers: dict[str, LocalHandler] = {}
    for capability, cap_config in capabilities.items():
        handler = available_handlers.get(cap_config.handler)
        if handler is None:
            logger.warning(
                "Executor %s (kind=local) capability %s: handler %s is not available; skipping",
                executor_id,
                capability,
                cap_config.handler,
            )
            continue
        handlers[capability] = handler
    return handlers


def build_local_executor(
    executor_id: str, config: LocalExecutorConfig, deps: RuntimeDependencies
) -> LocalExecutor:
    handlers = _resolve_local_handlers(executor_id, config.capabilities, deps.local_handlers)
    return LocalExecutor(
        id=executor_id,
        handlers=handlers,
        settings_config=deps.settings_config,
        job_db=deps.job_db,
        cancellation_grace_seconds=deps.cancellation_grace_seconds,
        resource_providers=deps.resource_providers,
        capability_timeouts={
            capability: cap_config.timeout_seconds
            for capability, cap_config in config.capabilities.items()
            if cap_config.timeout_seconds is not None
        },
        thread_capabilities={
            capability
            for capability, cap_config in config.capabilities.items()
            if cap_config.isolation == "thread"
        },
    )


register_kind(
    ExecutorKind(name="local", config_model=LocalExecutorConfig, factory=build_local_executor)
)
