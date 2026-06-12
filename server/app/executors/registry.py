from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.app.executors.config import (
    ExecutorConfig,
    LocalCapabilityConfig,
    LocalExecutorConfig,
    OpenClawExecutorConfig,
    PiExecutorConfig,
)
from server.app.executors.local import LocalExecutor, LocalHandler
from server.app.executors.openclaw import OpenClawExecutor
from server.app.executors.pi import PiExecutor
from server.app.executors.protocol import Executor
from server.app.executors.runtime_config import OpenClawRuntimeConfig, PiRuntimeConfig
from server.app.pipeline.openclaw import OpenClawRunner, SkillSafetyConfig

logger = logging.getLogger(__name__)


class ExecutorRegistryError(Exception):
    """Base class for executor registry failures."""


class UnknownExecutorError(ExecutorRegistryError):
    """Raised when a requested executor ID is not registered."""


class UnsupportedCapabilityError(ExecutorRegistryError):
    """Raised when an executor does not implement a requested capability."""


@dataclass(frozen=True)
class RuntimeDependencies:
    """Runtime dependencies injected into executor adapters during registry build.

    The registry is intentionally free of import side effects: callers supply the
    concrete handler functions, Pi configuration, and OpenClaw settings.
    """

    local_handlers: Mapping[str, LocalHandler] = field(default_factory=dict)
    pi_runtime: PiRuntimeConfig = field(default_factory=PiRuntimeConfig)
    pi_skill_root: Path = field(default_factory=lambda: Path("."))
    openclaw_runtime: OpenClawRuntimeConfig = field(
        default_factory=lambda: OpenClawRuntimeConfig(command_template=("openclaw",))
    )
    settings_config: Mapping[str, Any] | None = None
    job_db: Any | None = None


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

        Adapter construction switches only on the typed ``kind`` union. Error
        messages include the executor ID, kind, and capability where applicable.
        """
        executors: dict[str, Executor] = {}
        global_capacities: dict[str, int] = {}

        for executor_id, config in definitions.items():
            if isinstance(config, LocalExecutorConfig):
                handlers = _resolve_local_handlers(
                    executor_id, config.capabilities, runtime.local_handlers
                )
                executor: Executor = LocalExecutor(
                    id=executor_id,
                    handlers=handlers,
                    settings_config=runtime.settings_config,
                    job_db=runtime.job_db,
                )
            elif isinstance(config, PiExecutorConfig):
                executor = PiExecutor(
                    id=executor_id,
                    config=runtime.pi_runtime,
                    skill_root=runtime.pi_skill_root,
                    capabilities=config.capabilities,
                )
            elif isinstance(config, OpenClawExecutorConfig):
                runner = _build_openclaw_runner(config, runtime)
                executor = OpenClawExecutor(
                    id=executor_id,
                    runner=runner,
                    capabilities=config.capabilities,
                )
            else:
                raise ExecutorRegistryError(
                    f"Executor {executor_id!r}: unsupported kind {getattr(config, 'kind', '?')!r}"
                )

            executors[executor_id] = executor
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


def _build_openclaw_runner(
    config: OpenClawExecutorConfig,
    runtime: RuntimeDependencies,
) -> OpenClawRunner:
    """Build an OpenClawRunner with the executor's agent_id injected."""
    oc = runtime.openclaw_runtime
    command_template = _inject_agent_id(list(oc.command_template), config.agent_id)
    skill_safety = (
        SkillSafetyConfig(enabled=oc.skill_safety.enabled, repos=list(oc.skill_safety.repos))
        if oc.skill_safety is not None
        else None
    )
    isolated_root = Path(oc.isolated_workspace_root) if oc.isolated_workspace_root else None
    return OpenClawRunner(
        command_template=command_template,
        cwd=Path(oc.cwd),
        timeout_seconds=oc.timeout_seconds,
        skill_safety=skill_safety,
        isolated_workspace_root=isolated_root,
        agent_id=config.agent_id,
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
