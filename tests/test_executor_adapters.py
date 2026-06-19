import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.config import (
    OpenClawCapabilityConfig,
    PiCapabilityConfig,
)
from server.app.executors.local import LocalExecutor
from server.app.executors.models import ExecutionContext
from server.app.executors.openclaw import OpenClawExecutor
from server.app.executors.pi import PiExecutor
from server.app.pipeline.openclaw import OpenClawRunner
from server.app.skills.manager import SkillManager
from server.app.workflows.pi_runner import PiConfig


@pytest.fixture
def context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="test",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="reading_analysis",
        node_key="review_keywords",
        capability="review_keywords",
        workspace={"id": "ws-a"},
        job={
            "id": "job-1",
            "workspace_id": "ws-a",
            "workflow_key": "reading_analysis",
            "source_type": "question",
            "source_id": "q-1",
            "batch_id": "",
            "title": "Question 1",
            "storage_dir": str(tmp_path),
            "stem": "",
        },
        job_dir=tmp_path,
        log_path=tmp_path / "run.log",
        inputs=("in.json",),
        expected_outputs=("out.json",),
    )


# LocalExecutor


def noop_local_handler(
    _job: dict[str, Any], _job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    return None


def write_output_handler(
    _job: dict[str, Any], job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def raising_local_handler(
    _job: dict[str, Any], _job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    raise ValueError("boom")


def logging_local_handler(
    _job: dict[str, Any], job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    print("local handler log line")
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def record_runtime_handler(
    _job: dict[str, Any], job_dir: Path, runtime: dict[str, Any] | None
) -> None:
    runtime = runtime or {}
    payload = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in runtime.items()
        if key
        in {
            "job_dir",
            "log_path",
            "inputs",
            "expected_outputs",
            "capability",
            "node_key",
            "workflow_key",
            "execution_id",
            "workspace_id",
        }
    }
    (job_dir / "runtime.json").write_text(json.dumps(payload), encoding="utf-8")
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def test_local_executor_supports_capability() -> None:
    executor = LocalExecutor("local-default", {"fetch": noop_local_handler})
    assert executor.supports("fetch")
    assert not executor.supports("other")


def test_local_executor_returns_normalized_artifacts(context: ExecutionContext) -> None:
    executor = LocalExecutor("local-default", {"fetch": write_output_handler})
    result = executor.execute(replace(context, capability="fetch", expected_outputs=("out.json",)))
    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.produced_artifacts == ("out.json",)


def test_local_executor_fails_when_output_missing(context: ExecutionContext) -> None:
    executor = LocalExecutor("local-default", {"fetch": noop_local_handler})
    result = executor.execute(replace(context, capability="fetch"))
    assert result.status == "failed"
    assert "out.json" in result.error_message


def test_local_executor_catches_handler_exception(context: ExecutionContext) -> None:
    executor = LocalExecutor("local-default", {"fetch": raising_local_handler})
    result = executor.execute(replace(context, capability="fetch"))
    assert result.status == "failed"
    assert "boom" in result.error_message


def test_local_executor_writes_logs_to_log_path(context: ExecutionContext) -> None:
    executor = LocalExecutor("local-default", {"fetch": logging_local_handler})
    result = executor.execute(replace(context, capability="fetch", expected_outputs=("out.json",)))
    assert result.status == "completed"
    assert context.log_path.is_file()
    assert "local handler log line" in context.log_path.read_text(encoding="utf-8")


def test_local_executor_cancel_records_intent(context: ExecutionContext) -> None:
    executor = LocalExecutor("local-default", {"fetch": noop_local_handler})
    executor.cancel("exec-1")
    result = executor.execute(replace(context, capability="fetch"))
    assert result.status == "cancelled"


def test_local_executor_runtime_includes_expected_keys(context: ExecutionContext) -> None:
    executor = LocalExecutor("local-default", {"fetch": record_runtime_handler})
    executor.execute(
        replace(
            context,
            capability="fetch",
            expected_outputs=("out.json",),
            inputs=("a.json", "b.json"),
        )
    )

    captured = json.loads((context.job_dir / "runtime.json").read_text(encoding="utf-8"))
    assert captured["job_dir"] == str(context.job_dir)
    assert captured["log_path"] == str(context.log_path)
    assert captured["inputs"] == ["a.json", "b.json"]
    assert captured["expected_outputs"] == ["out.json"]
    assert captured["capability"] == "fetch"
    assert captured["node_key"] == context.node_key
    assert captured["workflow_key"] == context.workflow_key
    assert captured["execution_id"] == context.execution_id
    assert captured["workspace_id"] == context.workspace_id


# PiExecutor


def _make_skill_manager(
    tmp_path: Path,
    skill_key: str,
    validate_script: str | None = None,
) -> SkillManager:
    """Create a SkillManager backed by a temporary bare git repo for the given skill."""
    repo = tmp_path / "remote.git"
    repo.mkdir()
    subprocess.run(["git", "init", "--bare", str(repo)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "clone", str(repo), str(work / "clone")], check=True)
    clone = work / "clone"
    (clone / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    (clone / "references").mkdir()
    (clone / "references" / "output-contract.md").write_text("contract\n", encoding="utf-8")
    (clone / "scripts").mkdir()
    if validate_script is not None:
        (clone / "scripts" / "validate_output.py").write_text(validate_script, encoding="utf-8")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
    env = {
        **dict(os.environ),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(
        ["git", "-C", str(clone), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(clone), "push", "origin", "HEAD"], check=True)
    repo_uri = f"file://{repo.resolve()}"

    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  {skill_key}:\n    repo: {repo_uri}\n    ref: main\n",
        encoding="utf-8",
    )
    return SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )


def test_pi_executor_supports_capability(tmp_path: Path) -> None:
    executor = PiExecutor(
        "pi-default",
        PiConfig(binary="pi"),
        _make_skill_manager(tmp_path, "reading_analysis/review_keywords"),
        {"review_keywords": PiCapabilityConfig(skill="reading_analysis/review_keywords")},
    )
    assert executor.supports("review_keywords")
    assert not executor.supports("other")


def test_pi_executor_returns_normalized_result(tmp_path: Path) -> None:
    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text(
        '#!/bin/bash\necho \'{"event":"done"}\'\necho \'{"questions": []}\' > keywords_raw.json\n'
    )
    fake_pi.chmod(0o755)

    skill_manager = _make_skill_manager(
        tmp_path,
        "reading_analysis/extract_keywords",
        validate_script=(
            "#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n"
            "job_dir = Path(sys.argv[1])\n"
            "(job_dir / 'keywords_raw.json').write_text('{\"questions\": []}')\n"
        ),
    )

    executor = PiExecutor(
        "pi-default",
        PiConfig(binary=str(fake_pi)),
        skill_manager,
        {"extract_keywords": PiCapabilityConfig(skill="reading_analysis/extract_keywords")},
    )

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    ctx = ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="pi-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="reading_analysis",
        node_key="extract_keywords",
        capability="extract_keywords",
        workspace={"id": "ws-a"},
        job={"id": "job-1", "storage_dir": str(job_dir)},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=("questions_parsed.json",),
        expected_outputs=("keywords_raw.json",),
    )

    result = executor.execute(ctx)
    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.produced_artifacts == ("keywords_raw.json",)
    assert "fake_pi" in result.command[0]
    assert ctx.log_path.is_file()
    assert "event" in ctx.log_path.read_text(encoding="utf-8")
    assert not (skill_manager.runs_dir / ctx.execution_id).exists()


def test_pi_executor_cleans_snapshot_when_runner_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_manager = _make_skill_manager(
        tmp_path,
        "reading_analysis/extract_keywords",
        validate_script="#!/usr/bin/env python3\n",
    )
    executor = PiExecutor(
        "pi-default",
        PiConfig(binary="pi"),
        skill_manager,
        {"extract_keywords": PiCapabilityConfig(skill="reading_analysis/extract_keywords")},
    )
    monkeypatch.setattr(
        executor._runner,
        "run",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("runner failed")),
    )
    job_dir = tmp_path / "job"
    ctx = ExecutionContext(
        execution_id="exec-error",
        lease_id="lease-1",
        node_run_id=8,
        executor_id="pi-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="reading_analysis",
        node_key="extract_keywords",
        capability="extract_keywords",
        workspace={"id": "ws-a"},
        job={"id": "job-1", "storage_dir": str(job_dir)},
        job_dir=job_dir,
        log_path=tmp_path / "run-error.log",
        inputs=(),
        expected_outputs=(),
    )

    with pytest.raises(RuntimeError, match="runner failed"):
        executor.execute(ctx)

    assert not (skill_manager.runs_dir / ctx.execution_id).exists()


def test_pi_executor_fails_when_output_missing(tmp_path: Path) -> None:
    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text('#!/bin/bash\necho \'{"event":"done"}\'\n')
    fake_pi.chmod(0o755)

    skill_manager = _make_skill_manager(
        tmp_path,
        "reading_analysis/extract_keywords",
        validate_script="#!/usr/bin/env python3\nimport sys\n",
    )

    executor = PiExecutor(
        "pi-default",
        PiConfig(binary=str(fake_pi)),
        skill_manager,
        {"extract_keywords": PiCapabilityConfig(skill="reading_analysis/extract_keywords")},
    )

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    ctx = ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="pi-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="reading_analysis",
        node_key="extract_keywords",
        capability="extract_keywords",
        workspace={"id": "ws-a"},
        job={"id": "job-1", "storage_dir": str(job_dir)},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=("questions_parsed.json",),
        expected_outputs=("keywords_raw.json",),
    )

    result = executor.execute(ctx)
    assert result.status == "failed"
    assert "keywords_raw.json" in result.error_message


def test_pi_executor_cancel_records_intent(tmp_path: Path) -> None:
    executor = PiExecutor(
        "pi-default",
        PiConfig(binary="pi"),
        _make_skill_manager(tmp_path, "reading_analysis/extract_keywords"),
        {"extract_keywords": PiCapabilityConfig(skill="reading_analysis/extract_keywords")},
    )
    executor.cancel("exec-1")

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    ctx = ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="pi-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="reading_analysis",
        node_key="extract_keywords",
        capability="extract_keywords",
        workspace={"id": "ws-a"},
        job={"id": "job-1", "storage_dir": str(job_dir)},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=(),
        expected_outputs=(),
    )
    result = executor.execute(ctx)
    assert result.status == "cancelled"


# OpenClawExecutor


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
        workflow_key="reading_analysis",
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
        workflow_key="reading_analysis",
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
        workflow_key="reading_analysis",
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
        workflow_key="reading_analysis",
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
