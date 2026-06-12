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
from server.app.executors.local import LocalExecutor, LocalHandler
from server.app.executors.openclaw import OpenClawExecutor
from server.app.executors.registry import (
    ExecutorRegistry,
    ExecutorRegistryError,
    RuntimeDependencies,
    UnknownExecutorError,
    UnsupportedCapabilityError,
)
from server.app.pipeline.openclaw import SkillSafetyConfig
from server.app.pipelines.pi_runner import PiConfig


def _make_local_handler(name: str) -> LocalHandler:
    def handler(job: dict[str, Any], artifact_dir: Path, context: dict[str, Any] | None) -> None:
        (artifact_dir / f"{name}.txt").write_text(name, encoding="utf-8")

    return handler


def _sample_pi_config() -> PiConfig:
    return PiConfig(
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
                    handler="reading_analysis.fetch_questions"
                ),
                "clean_and_parse": LocalCapabilityConfig(
                    handler="reading_analysis.clean_and_parse"
                ),
            },
        ),
        "pi-default": PiExecutorConfig(
            kind="pi",
            global_capacity=8,
            capabilities={
                "review_keywords": PiCapabilityConfig(
                    skill="reading_analysis/review_keywords",
                    tools=("read", "write", "bash"),
                ),
                "extract_keywords": PiCapabilityConfig(
                    skill="reading_analysis/extract_keywords",
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
def runtime_dependencies() -> RuntimeDependencies:
    return RuntimeDependencies(
        local_handlers={
            "reading_analysis.fetch_questions": _make_local_handler("fetch_questions"),
            "reading_analysis.clean_and_parse": _make_local_handler("clean_and_parse"),
        },
        pi_config=_sample_pi_config(),
        pi_skill_root=Path("."),
        openclaw_command_template=[
            "openclaw",
            "agent",
            "--local",
            "--agent",
            "{agent_id}",
            "--message",
            "{prompt_text}",
        ],
        openclaw_cwd=Path("."),
        openclaw_timeout_seconds=600,
        openclaw_skill_safety=SkillSafetyConfig(enabled=False, repos=[]),
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
                "available": LocalCapabilityConfig(handler="reading_analysis.available"),
                "missing": LocalCapabilityConfig(handler="reading_analysis.missing"),
            },
        ),
    }
    runtime = RuntimeDependencies(
        local_handlers={"reading_analysis.available": _make_local_handler("available")},
        pi_config=runtime_dependencies.pi_config,
        pi_skill_root=runtime_dependencies.pi_skill_root,
        openclaw_command_template=runtime_dependencies.openclaw_command_template,
        openclaw_cwd=runtime_dependencies.openclaw_cwd,
        openclaw_timeout_seconds=runtime_dependencies.openclaw_timeout_seconds,
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
        pi_config=runtime_dependencies.pi_config,
        pi_skill_root=runtime_dependencies.pi_skill_root,
        openclaw_command_template=runtime_dependencies.openclaw_command_template,
        openclaw_cwd=runtime_dependencies.openclaw_cwd,
        openclaw_timeout_seconds=runtime_dependencies.openclaw_timeout_seconds,
        settings_config=settings_config,
        job_db=job_db,
    )

    registry = ExecutorRegistry.build(definitions, runtime)
    executor = registry.require("local-default", "fetch_questions")
    assert isinstance(executor, LocalExecutor)
    assert executor.settings_config == settings_config
    assert executor.job_db is job_db
