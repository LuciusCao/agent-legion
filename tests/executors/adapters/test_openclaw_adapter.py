from __future__ import annotations

from pathlib import Path

import pytest

from server.app.executors.config import (
    OpenClawCapabilityConfig,
    OpenClawExecutorConfig,
)
from server.app.executors.models import ExecutionContext
from server.app.executors.openclaw import OpenClawExecutor, build_openclaw_executor
from server.app.executors.openclaw_runner import OpenClawRunner
from server.app.executors.runtime_config import (
    OpenClawRuntimeConfig,
    OpenClawSkillSafetyRuntimeConfig,
)
from server.app.skills.errors import SkillConfigError
from server.app.skills.manager import SkillManager
from tests.helpers.skill_store import memory_skill_store


def test_openclaw_executor_supports_capability(tmp_path: Path) -> None:
    runner = OpenClawRunner(command_template=["echo"], cwd=tmp_path, timeout_seconds=10)
    executor = OpenClawExecutor(
        "oc-default",
        runner,
        {"interaction_generate": OpenClawCapabilityConfig(skill="interaction")},
    )
    assert executor.supports("interaction_generate")
    assert not executor.supports("other")


def test_openclaw_executor_returns_normalized_result(tmp_path: Path) -> None:
    command = [
        "python3",
        "-c",
        (
            "import pathlib, sys; "
            "out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); "
            "(out / 'interactions.json').write_text('{}', encoding='utf-8')"
        ),
        "{video_dir}",
    ]
    runner = OpenClawRunner(command_template=command, cwd=tmp_path, timeout_seconds=10)
    executor = OpenClawExecutor(
        "oc-default",
        runner,
        {"interaction_generate": OpenClawCapabilityConfig(skill="interaction")},
    )

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    ctx = ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="oc-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="question_comprehension_info",
        node_key="interaction_generate",
        capability="interaction_generate",
        workspace={"id": "ws-a"},
        job={"id": "job-1", "storage_dir": str(job_dir)},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=(),
        expected_outputs=("interactions.json",),
    )

    result = executor.execute(ctx)
    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.produced_artifacts == ("interactions.json",)


def test_openclaw_executor_fails_when_output_missing(tmp_path: Path) -> None:
    command = [
        "python3",
        "-c",
        (
            "import pathlib, sys; "
            "out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)"
        ),
        "{video_dir}",
    ]
    runner = OpenClawRunner(command_template=command, cwd=tmp_path, timeout_seconds=10)
    executor = OpenClawExecutor(
        "oc-default",
        runner,
        {"interaction_generate": OpenClawCapabilityConfig(skill="interaction")},
    )

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    ctx = ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="oc-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="question_comprehension_info",
        node_key="interaction_generate",
        capability="interaction_generate",
        workspace={"id": "ws-a"},
        job={"id": "job-1", "storage_dir": str(job_dir)},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=(),
        expected_outputs=("interactions.json",),
    )

    result = executor.execute(ctx)
    assert result.status == "failed"
    assert "interactions.json" in result.error_message


def test_openclaw_executor_cancel_records_intent(tmp_path: Path) -> None:
    runner = OpenClawRunner(command_template=["echo"], cwd=tmp_path, timeout_seconds=10)
    executor = OpenClawExecutor(
        "oc-default",
        runner,
        {"interaction_generate": OpenClawCapabilityConfig(skill="interaction")},
    )
    executor.cancel("exec-1")

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    ctx = ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="oc-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="question_comprehension_info",
        node_key="interaction_generate",
        capability="interaction_generate",
        workspace={"id": "ws-a"},
        job={"id": "job-1", "storage_dir": str(job_dir)},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=(),
        expected_outputs=(),
    )

    result = executor.execute(ctx)
    assert result.status == "cancelled"


def test_openclaw_executor_prepends_skill_to_prompt(tmp_path: Path) -> None:
    command = [
        "python3",
        "-c",
        (
            "import pathlib, sys; "
            "out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); "
            "(out / 'interactions.json').write_text('{}', encoding='utf-8')"
        ),
        "{video_dir}",
    ]
    runner = OpenClawRunner(command_template=command, cwd=tmp_path, timeout_seconds=10)
    executor = OpenClawExecutor(
        "oc-default",
        runner,
        {"interaction_generate": OpenClawCapabilityConfig(skill="interaction")},
    )

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    ctx = ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="oc-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="question_comprehension_info",
        node_key="interaction_generate",
        capability="interaction_generate",
        workspace={"id": "ws-a"},
        job={"id": "job-1", "storage_dir": str(job_dir)},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=("input.json",),
        expected_outputs=("interactions.json",),
    )

    executor.execute(ctx)

    prompt_file = job_dir / "prompts" / "exec-1.md"
    assert prompt_file.is_file()
    prompt_text = prompt_file.read_text(encoding="utf-8")
    assert "Use the installed skill interaction." in prompt_text


def _executor_config() -> OpenClawExecutorConfig:
    return OpenClawExecutorConfig(
        kind="openclaw",
        agent_id="main",
        global_capacity=1,
        capabilities={"interaction_generate": OpenClawCapabilityConfig(skill="interaction")},
    )


def _skill_manager(tmp_path: Path, skills: dict[str, dict[str, str]]) -> SkillManager:
    return SkillManager(
        store=memory_skill_store(lock={"version": "1", "skills": skills}),
        base_dir=tmp_path / "skill-cache",
    )


def test_build_openclaw_executor_resolves_skill_safety_refs_from_lock(tmp_path: Path) -> None:
    skill_repo = tmp_path / "skills" / "generate_chapters"
    manager = _skill_manager(
        tmp_path,
        {
            "video_knowledge/generate_chapters": {
                "repo": f"file://{skill_repo}",
                "ref": "v1.0.1",
                "commit": "abc123",
            }
        },
    )
    runtime = OpenClawRuntimeConfig(
        command_template=("openclaw",),
        skill_safety=OpenClawSkillSafetyRuntimeConfig(
            enabled=True,
            repos=[{"path": str(skill_repo)}],
        ),
    )

    executor = build_openclaw_executor("oc", runtime, _executor_config(), skill_manager=manager)

    assert executor.runner.skill_safety is not None
    assert executor.runner.skill_safety.repos == [
        {"path": str(skill_repo.expanduser().resolve()), "ref": "abc123"}
    ]


def test_build_openclaw_executor_rejects_safety_repo_missing_from_lock(tmp_path: Path) -> None:
    manager = _skill_manager(tmp_path, {})
    runtime = OpenClawRuntimeConfig(
        command_template=("openclaw",),
        skill_safety=OpenClawSkillSafetyRuntimeConfig(
            enabled=True,
            repos=[{"path": str(tmp_path / "unknown")}],
        ),
    )

    with pytest.raises(SkillConfigError, match="not declared in the skill lock"):
        build_openclaw_executor("oc", runtime, _executor_config(), skill_manager=manager)


def test_build_openclaw_executor_requires_skill_manager_for_safety(tmp_path: Path) -> None:
    runtime = OpenClawRuntimeConfig(
        command_template=("openclaw",),
        skill_safety=OpenClawSkillSafetyRuntimeConfig(
            enabled=True,
            repos=[{"path": str(tmp_path / "skills" / "s1")}],
        ),
    )

    with pytest.raises(SkillConfigError, match="no skill manager"):
        build_openclaw_executor("oc", runtime, _executor_config())


def test_build_openclaw_executor_disabled_safety_skips_lock(tmp_path: Path) -> None:
    runtime = OpenClawRuntimeConfig(
        command_template=("openclaw",),
        skill_safety=OpenClawSkillSafetyRuntimeConfig(enabled=False, repos=[]),
    )

    executor = build_openclaw_executor("oc", runtime, _executor_config())

    assert executor.runner.skill_safety is not None
    assert executor.runner.skill_safety.enabled is False
    assert executor.runner.skill_safety.repos == []
