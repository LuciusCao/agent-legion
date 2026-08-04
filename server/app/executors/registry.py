from __future__ import annotations

from collections.abc import Mapping

from server.app.executors import registration as _registration  # noqa: F401
from server.app.executors.config import ExecutorConfig
from server.app.executors.kinds import ExecutorKindError, RuntimeDependencies, build_executor
from server.app.executors.protocol import Executor


class ExecutorRegistryError(Exception):
    """Base class for executor registry failures."""


class UnknownExecutorError(ExecutorRegistryError):
    """Raised when a requested executor ID is not registered."""


class UnsupportedCapabilityError(ExecutorRegistryError):
    """Raised when an executor does not implement a requested capability."""


class ExecutorRegistry:
    """Registry of configured Executor adapters indexed by executor ID."""

    def __init__(
        self,
        executors: Mapping[str, Executor],
        global_capacities: Mapping[str, int],
        definitions: Mapping[str, ExecutorConfig],
    ) -> None:
        self._executors = dict(executors)
        self._global_capacities = dict(global_capacities)
        self._definitions = dict(definitions)

    @classmethod
    def build(
        cls,
        definitions: Mapping[str, ExecutorConfig],
        runtime: RuntimeDependencies,
    ) -> ExecutorRegistry:
        """Construct executor adapters from typed definitions and runtime dependencies.

        Adapter construction dispatches through the kind registry; kind errors
        are surfaced as ``ExecutorRegistryError`` with the executor ID in the
        message.
        """
        executors: dict[str, Executor] = {}
        global_capacities: dict[str, int] = {}

        for executor_id, config in definitions.items():
            try:
                executors[executor_id] = build_executor(executor_id, config, runtime)
            except ExecutorKindError as exc:
                raise ExecutorRegistryError(str(exc)) from exc
            global_capacities[executor_id] = config.global_capacity

        return cls(executors, global_capacities, definitions)

    def get(self, executor_id: str) -> Executor | None:
        """Return the executor with *executor_id* or ``None`` if not registered."""
        return self._executors.get(executor_id)

    def require(self, executor_id: str, capability: str) -> Executor:
        """Return the executor with *executor_id* after verifying *capability*."""
        executor = self._executors.get(executor_id)
        if executor is None:
            raise UnknownExecutorError(f"Executor {executor_id!r} is not registered")
        if not executor.supports(capability):
            raise UnsupportedCapabilityError(
                f"Executor {executor_id!r} (kind={executor.kind!r}) "
                f"does not support capability {capability!r}"
            )
        return executor

    def definitions(self) -> dict[str, ExecutorConfig]:
        """Return the executor definitions used to build the registry."""
        return dict(self._definitions)

    def global_capacity(self, executor_id: str) -> int | None:
        """Return the configured global capacity for *executor_id*."""
        return self._global_capacities.get(executor_id)
