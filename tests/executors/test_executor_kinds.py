from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from server.app.executors import kinds
from server.app.executors.kinds import (
    ExecutorKind,
    ExecutorKindError,
    RuntimeDependencies,
    UnknownExecutorKindError,
    build_executor,
    get_kind,
    load_executor_config,
    register_kind,
    registered_kind_names,
)


class _FakeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str
    global_capacity: int = Field(gt=0, strict=True)


class _FakeExecutor:
    kind = "fake"

    def __init__(self, executor_id: str, config: BaseModel) -> None:
        self.id = executor_id
        self.config = config

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: Any) -> None:
        return None

    def cancel(self, execution_id: str) -> None:
        return None


@pytest.fixture
def fake_kind() -> ExecutorKind:
    kind = ExecutorKind(
        name="fake",
        config_model=_FakeConfig,
        factory=lambda executor_id, config, deps: _FakeExecutor(executor_id, config),
    )
    register_kind(kind)
    yield kind
    # 模块级注册表为全局状态，每个用例结束后反注册，保证用例间隔离。
    kinds._KIND_REGISTRY.pop(kind.name, None)


def test_register_and_get(fake_kind: ExecutorKind) -> None:
    assert get_kind("fake") is fake_kind
    assert "fake" in registered_kind_names()


def test_duplicate_registration_rejected(fake_kind: ExecutorKind) -> None:
    with pytest.raises(ExecutorKindError, match="fake"):
        register_kind(fake_kind)


def test_load_executor_config_unknown_kind() -> None:
    with pytest.raises(UnknownExecutorKindError, match="my-exec.*nope"):
        load_executor_config("my-exec", {"kind": "nope", "global_capacity": 1})


def test_load_executor_config_validates_with_model(fake_kind: ExecutorKind) -> None:
    config = load_executor_config("my-exec", {"kind": "fake", "global_capacity": 2})
    assert isinstance(config, _FakeConfig)
    assert config.global_capacity == 2


def test_load_executor_config_model_error_propagates(fake_kind: ExecutorKind) -> None:
    with pytest.raises(Exception, match="global_capacity"):
        load_executor_config("my-exec", {"kind": "fake", "global_capacity": 0})


def test_build_executor_invokes_factory(fake_kind: ExecutorKind) -> None:
    config = load_executor_config("my-exec", {"kind": "fake", "global_capacity": 2})
    executor = build_executor("my-exec", config, RuntimeDependencies())
    assert isinstance(executor, _FakeExecutor)
    assert executor.id == "my-exec"


def test_runtime_dependencies_defaults() -> None:
    deps = RuntimeDependencies()
    assert deps.pi_runtime.binary == "pi"
    assert deps.cancellation_grace_seconds == 5
