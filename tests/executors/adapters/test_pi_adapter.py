from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.executors.config import (
    PiCapabilityConfig,
)
from server.app.executors.models import ExecutionContext
from server.app.executors.pi import PiExecutor
from server.app.executors.runtime_config import PiRuntimeConfig
from tests.executors.adapters.helpers import (
    _make_skill_manager,
)


def test_pi_executor_supports_capability(tmp_path: Path) -> None:
    executor = PiExecutor(
        "pi-default",
        PiRuntimeConfig(binary="pi"),
        _make_skill_manager(tmp_path, "question_comprehension_info/review_key_info"),
        {
            "review_keywords": PiCapabilityConfig(
                skill="question_comprehension_info/review_key_info"
            )
        },
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
        "question_comprehension_info/generate_key_info",
        validate_script=(
            "#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n"
            "job_dir = Path(sys.argv[1])\n"
            "(job_dir / 'keywords_raw.json').write_text('{\"questions\": []}')\n"
        ),
    )

    executor = PiExecutor(
        "pi-default",
        PiRuntimeConfig(binary=str(fake_pi)),
        skill_manager,
        {
            "extract_keywords": PiCapabilityConfig(
                skill="question_comprehension_info/generate_key_info"
            )
        },
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
        workflow_key="question_comprehension_info",
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
    assert Path(result.run_dir).is_relative_to(job_dir / "runs" / "extract_keywords")
    assert Path(result.session_dir) == Path(result.run_dir) / "session"
    events_path = Path(result.run_dir) / "events.jsonl"
    assert events_path.is_file()
    assert "event" in events_path.read_text(encoding="utf-8")
    assert not ctx.log_path.is_file()
    assert not (skill_manager.runs_dir / ctx.execution_id).exists()

    run_json_text = (Path(result.run_dir) / "run.json").read_text(encoding="utf-8")
    run_json = json.loads(run_json_text)
    assert run_json["skill_version"] != ""
    assert len(run_json["skill_version"]) == 40


def test_pi_executor_cleans_snapshot_when_runner_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_manager = _make_skill_manager(
        tmp_path,
        "question_comprehension_info/generate_key_info",
        validate_script="#!/usr/bin/env python3\n",
    )
    executor = PiExecutor(
        "pi-default",
        PiRuntimeConfig(binary="pi"),
        skill_manager,
        {
            "extract_keywords": PiCapabilityConfig(
                skill="question_comprehension_info/generate_key_info"
            )
        },
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
        workflow_key="question_comprehension_info",
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
        "question_comprehension_info/generate_key_info",
        validate_script="#!/usr/bin/env python3\nimport sys\n",
    )

    executor = PiExecutor(
        "pi-default",
        PiRuntimeConfig(binary=str(fake_pi)),
        skill_manager,
        {
            "extract_keywords": PiCapabilityConfig(
                skill="question_comprehension_info/generate_key_info"
            )
        },
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
        workflow_key="question_comprehension_info",
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
        PiRuntimeConfig(binary="pi"),
        _make_skill_manager(tmp_path, "question_comprehension_info/generate_key_info"),
        {
            "extract_keywords": PiCapabilityConfig(
                skill="question_comprehension_info/generate_key_info"
            )
        },
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
        workflow_key="question_comprehension_info",
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
