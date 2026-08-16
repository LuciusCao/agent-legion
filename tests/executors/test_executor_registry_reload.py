"""ExecutorRegistry.replace_definitions: hot reload of published definitions."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.executors.config import CodeCapabilityConfig, CodeExecutorConfig
from server.app.executors.kinds import RuntimeDependencies
from server.app.executors.registry import ExecutorRegistry, ExecutorRegistryError


def _definition(capacity: int, capability: str = "do_thing") -> CodeExecutorConfig:
    return CodeExecutorConfig(
        kind="code",
        global_capacity=capacity,
        capabilities={capability: CodeCapabilityConfig()},
    )


def _registry(tmp_path: Path) -> ExecutorRegistry:
    return ExecutorRegistry.build(
        {"code-a": _definition(1)}, RuntimeDependencies(repo_root=tmp_path)
    )


def test_replace_definitions_swaps_state(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    registry.replace_definitions(
        {"code-a": _definition(3), "code-b": _definition(1, capability="other")}
    )

    assert registry.global_capacity("code-a") == 3
    assert registry.require("code-b", "other").supports("other")
    assert set(registry.definitions()) == {"code-a", "code-b"}


def test_replace_definitions_build_failure_keeps_old_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry(tmp_path)
    # Post-#96 no kind factory fails on its own (the repo-file path
    # validation retired with the path binding), so the failure is injected
    # at the kind factory seam.
    import server.app.executors.registry as registry_module

    real_build = registry_module.build_executor

    def _flaky(executor_id, config, deps):
        if executor_id == "code-b":
            raise ValueError("boom")
        return real_build(executor_id, config, deps)

    monkeypatch.setattr(registry_module, "build_executor", _flaky)

    with pytest.raises(ValueError, match="boom"):
        registry.replace_definitions({"code-a": _definition(2), "code-b": _definition(1)})

    # Nothing half-applied: the previous snapshot still serves reads.
    assert registry.global_capacity("code-a") == 1
    assert registry.require("code-a", "do_thing") is not None
    assert registry.get("code-b") is None


def test_replace_definitions_requires_runtime(tmp_path: Path) -> None:
    registry = ExecutorRegistry({}, {}, {})

    with pytest.raises(ExecutorRegistryError, match="runtime"):
        registry.replace_definitions({"code-a": _definition(1)})
