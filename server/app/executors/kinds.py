from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from pydantic import BaseModel

from server.app.executors.protocol import Executor
from server.app.executors.runtime_config import OpenClawRuntimeConfig, PiRuntimeConfig
from server.app.skills.manager import SkillManager

if TYPE_CHECKING:
    from server.app.services.artifact_store import ArtifactStore


class ExecutorKindError(Exception):
    """Base class for executor kind registry failures."""


class UnknownExecutorKindError(ExecutorKindError):
    """Raised when a configuration references an unregistered executor kind."""


ConfigModelT = TypeVar("ConfigModelT", bound=BaseModel)


@dataclass(frozen=True)
class ExecutorKind(Generic[ConfigModelT]):
    name: str
    config_model: type[ConfigModelT]
    factory: Callable[[str, ConfigModelT, RuntimeDependencies], Executor]


@dataclass(frozen=True)
class RuntimeDependencies:
    local_handlers: Mapping[str, Any] = field(default_factory=dict)
    pi_runtime: PiRuntimeConfig = field(default_factory=PiRuntimeConfig)
    skill_manager: SkillManager = field(
        default_factory=lambda: SkillManager(
            config_path=Path("config") / "skills.yaml",
            lock_path=Path("config") / "skills.lock",
            base_dir=Path.home() / ".agents" / "skills" / "agent-legion",
        )
    )
    openclaw_runtime: OpenClawRuntimeConfig = field(
        default_factory=lambda: OpenClawRuntimeConfig(command_template=("openclaw",))
    )
    settings_config: Mapping[str, Any] | None = None
    job_db: Any | None = None
    cancellation_grace_seconds: int = 5
    artifact_store: ArtifactStore | None = None


_KIND_REGISTRY: dict[str, ExecutorKind[Any]] = {}


def register_kind(kind: ExecutorKind[Any]) -> None:
    if kind.name in _KIND_REGISTRY:
        raise ExecutorKindError(f"executor kind {kind.name!r} is already registered")
    _KIND_REGISTRY[kind.name] = kind


def get_kind(name: str) -> ExecutorKind[Any] | None:
    return _KIND_REGISTRY.get(name)


def registered_kind_names() -> tuple[str, ...]:
    return tuple(sorted(_KIND_REGISTRY))


def load_executor_config(executor_id: str, raw: dict[str, Any]) -> BaseModel:
    kind_name = raw.get("kind")
    kind = _KIND_REGISTRY.get(str(kind_name))
    if kind is None:
        raise UnknownExecutorKindError(
            f"Executor {executor_id!r}: unknown kind {kind_name!r} "
            f"(registered: {', '.join(registered_kind_names()) or 'none'})"
        )
    return cast(BaseModel, kind.config_model.model_validate(raw))


def build_executor(executor_id: str, config: BaseModel, deps: RuntimeDependencies) -> Executor:
    kind = _KIND_REGISTRY.get(getattr(config, "kind", ""))
    if kind is None:
        raise UnknownExecutorKindError(
            f"Executor {executor_id!r}: unknown kind {getattr(config, 'kind', '?')!r}"
        )
    return kind.factory(executor_id, config, deps)
