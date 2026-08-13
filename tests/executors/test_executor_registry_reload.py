"""ExecutorRegistry.replace_definitions: hot reload of published definitions."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.executors.config import CodeCapabilityConfig, CodeExecutorConfig
from server.app.executors.kinds import RuntimeDependencies
from server.app.executors.registry import ExecutorRegistry, ExecutorRegistryError


def _definition(
    capacity: int, capability: str = "do_thing", path: str = "node.py"
) -> CodeExecutorConfig:
    return CodeExecutorConfig(
        kind="code",
        global_capacity=capacity,
        capabilities={capability: CodeCapabilityConfig(path=path)},
    )


def _registry(tmp_path: Path) -> ExecutorRegistry:
    (tmp_path / "node.py").write_text(
        "def run(job, job_dir, runtime):\n    pass\n", encoding="utf-8"
    )
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


def test_replace_definitions_build_failure_keeps_old_state(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    broken = _definition(1, path="missing.py")

    with pytest.raises(ValueError, match="repository root"):
        registry.replace_definitions({"code-a": broken})

    # Nothing half-applied: the previous snapshot still serves reads.
    assert registry.global_capacity("code-a") == 1
    assert registry.require("code-a", "do_thing") is not None


def test_replace_definitions_requires_runtime(tmp_path: Path) -> None:
    registry = ExecutorRegistry({}, {}, {})

    with pytest.raises(ExecutorRegistryError, match="runtime"):
        registry.replace_definitions({"code-a": _definition(1)})
