from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from server.app.executors.config import (
    ExecutorConfig,
    LocalCapabilityConfig,
    LocalExecutorConfig,
    OpenClawCapabilityConfig,
    OpenClawExecutorConfig,
    PiCapabilityConfig,
    PiExecutorConfig,
)
from server.app.executors.local import LocalExecutor
from server.app.executors.openclaw import OpenClawExecutor
from server.app.executors.registry import (
    ExecutorRegistry,
    ExecutorRegistryError,
    RuntimeDependencies,
    UnknownExecutorError,
    UnsupportedCapabilityError,
)
from server.app.executors.runtime_config import (
    OpenClawRuntimeConfig,
    OpenClawSkillSafetyRuntimeConfig,
    PiRuntimeConfig,
)
from server.app.skills.manager import SkillManager


def _write_handler_artifact(artifact_dir: Path, name: str) -> None:
    (artifact_dir / f"{name}.txt").write_text(name, encoding="utf-8")


def _fetch_questions_handler(
    job: dict[str, Any], artifact_dir: Path, context: dict[str, Any] | None
) -> None:
    _write_handler_artifact(artifact_dir, "fetch_questions")


def _clean_and_parse_handler(
    job: dict[str, Any], artifact_dir: Path, context: dict[str, Any] | None
) -> None:
    _write_handler_artifact(artifact_dir, "clean_and_parse")


def _available_handler(
    job: dict[str, Any], artifact_dir: Path, context: dict[str, Any] | None
) -> None:
    _write_handler_artifact(artifact_dir, "available")


def _sample_pi_runtime() -> PiRuntimeConfig:
    return PiRuntimeConfig(
        binary="pi",
        provider="",
        model="",
        thinking="low",
        timeout_seconds=600,
        environment={},
    )


@pytest.fixture
def definitions() -> dict[str, ExecutorConfig]:
    return {
        "local-default": LocalExecutorConfig(
            kind="local",
            global_capacity=4,
            capabilities={
                "fetch_questions": LocalCapabilityConfig(
                    handler="question_comprehension_info.fetch_questions"
                ),
                "clean_and_parse": LocalCapabilityConfig(
                    handler="question_comprehension_info.clean_and_parse"
                ),
            },
        ),
        "pi-default": PiExecutorConfig(
            kind="pi",
            global_capacity=8,
            capabilities={
                "review_keywords": PiCapabilityConfig(
                    skill="question_comprehension_info/review_key_info",
                    tools=("read", "write", "bash"),
                ),
                "extract_keywords": PiCapabilityConfig(
                    skill="question_comprehension_info/generate_key_info",
                    tools=("read", "write"),
                ),
            },
        ),
        "openclaw-default": OpenClawExecutorConfig(
            kind="openclaw",
            agent_id="main",
            global_capacity=2,
            capabilities={
                "interaction_generate": OpenClawCapabilityConfig(skill="generate-interactions"),
            },
        ),
    }


@pytest.fixture
def runtime_dependencies(tmp_path: Path) -> RuntimeDependencies:
    skill_manager = SkillManager(
        config_path=tmp_path / "skills.yaml",
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
    )
    return RuntimeDependencies(
        local_handlers={
            "question_comprehension_info.fetch_questions": _fetch_questions_handler,
            "question_comprehension_info.clean_and_parse": _clean_and_parse_handler,
        },
        pi_runtime=_sample_pi_runtime(),
        skill_manager=skill_manager,
        openclaw_runtime=OpenClawRuntimeConfig(
            command_template=(
                "openclaw",
                "agent",
                "--local",
                "--agent",
                "{agent_id}",
                "--message",
                "{prompt_text}",
            ),
            skill_safety=OpenClawSkillSafetyRuntimeConfig(enabled=False, repos=[]),
        ),
    )


@pytest.fixture
def registry(
    definitions: dict[str, ExecutorConfig],
    runtime_dependencies: RuntimeDependencies,
) -> ExecutorRegistry:
    return ExecutorRegistry.build(definitions, runtime_dependencies)


def test_registry_get_returns_executor(registry: ExecutorRegistry) -> None:
    executor = registry.get("pi-default")
    assert executor is not None
    assert executor.id == "pi-default"
    assert executor.kind == "pi"


def test_registry_get_returns_none_for_unknown(registry: ExecutorRegistry) -> None:
    assert registry.get("unknown") is None


def test_registry_require_returns_matching_executor(registry: ExecutorRegistry) -> None:
    executor = registry.require("pi-default", "review_keywords")
    assert executor.id == "pi-default"
    assert executor.supports("review_keywords")


def test_registry_resolves_only_supported_capabilities(registry: ExecutorRegistry) -> None:
    executor = registry.require("pi-default", "review_keywords")
    assert executor.id == "pi-default"
    with pytest.raises(UnsupportedCapabilityError, match="pi-default.*fetch_questions"):
        registry.require("pi-default", "fetch_questions")


def test_registry_require_unknown_executor_raises(registry: ExecutorRegistry) -> None:
    with pytest.raises(UnknownExecutorError, match="unknown-exec"):
        registry.require("unknown-exec", "review_keywords")


def test_registry_global_capacity(registry: ExecutorRegistry) -> None:
    assert registry.global_capacity("local-default") == 4
    assert registry.global_capacity("pi-default") == 8
    assert registry.global_capacity("openclaw-default") == 2
    assert registry.global_capacity("unknown") is None


def test_registry_definitions_returns_original_definitions(
    registry: ExecutorRegistry, definitions: dict[str, ExecutorConfig]
) -> None:
    assert registry.definitions() == definitions


def test_registry_rejects_unknown_kind_factory(
    definitions: dict[str, ExecutorConfig],
    runtime_dependencies: RuntimeDependencies,
) -> None:
    raw_unknown: dict[str, Any] = {
        "kind": "unknown",
        "global_capacity": 1,
        "capabilities": {},
    }
    definitions_with_unknown = {**definitions, "weird": raw_unknown}

    with pytest.raises(ExecutorRegistryError):
        ExecutorRegistry.build(
            cast(dict[str, ExecutorConfig], definitions_with_unknown),
            runtime_dependencies,
        )


def test_registry_skips_unavailable_local_handlers(
    runtime_dependencies: RuntimeDependencies,
) -> None:
    definitions: dict[str, ExecutorConfig] = {
        "local-default": LocalExecutorConfig(
            kind="local",
            global_capacity=4,
            capabilities={
                "available": LocalCapabilityConfig(handler="question_comprehension_info.available"),
                "missing": LocalCapabilityConfig(handler="question_comprehension_info.missing"),
            },
        ),
    }
    runtime = RuntimeDependencies(
        local_handlers={"question_comprehension_info.available": _available_handler},
        pi_runtime=runtime_dependencies.pi_runtime,
        skill_manager=runtime_dependencies.skill_manager,
        openclaw_runtime=runtime_dependencies.openclaw_runtime,
    )

    registry = ExecutorRegistry.build(definitions, runtime)
    executor = registry.require("local-default", "available")
    assert executor.supports("available")
    assert not executor.supports("missing")


def test_registry_builds_openclaw_executor_with_agent_id_substitution(
    definitions: dict[str, ExecutorConfig],
    runtime_dependencies: RuntimeDependencies,
) -> None:
    registry = ExecutorRegistry.build(definitions, runtime_dependencies)
    executor = registry.require("openclaw-default", "interaction_generate")
    assert isinstance(executor, OpenClawExecutor)
    assert executor.id == "openclaw-default"
    assert executor.runner.agent_id == "main"
    assert "--agent" in executor.runner.command_template
    assert "main" in executor.runner.command_template


def test_registry_builds_local_executor_with_settings_and_job_db(
    definitions: dict[str, ExecutorConfig],
    runtime_dependencies: RuntimeDependencies,
) -> None:
    settings_config = {"cms": {"base_url": "http://example.com"}}
    job_db = object()
    runtime = RuntimeDependencies(
        local_handlers=runtime_dependencies.local_handlers,
        pi_runtime=runtime_dependencies.pi_runtime,
        skill_manager=runtime_dependencies.skill_manager,
        openclaw_runtime=runtime_dependencies.openclaw_runtime,
        settings_config=settings_config,
        job_db=job_db,
    )

    registry = ExecutorRegistry.build(definitions, runtime)
    executor = registry.require("local-default", "fetch_questions")
    assert isinstance(executor, LocalExecutor)
    assert executor.settings_config == settings_config
    assert executor.job_db is job_db
