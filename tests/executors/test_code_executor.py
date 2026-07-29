from __future__ import annotations

import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from server.app.executors.code import CodeExecutor
from server.app.executors.config import CodeCapabilityConfig
from server.app.executors.models import ExecutionContext


@pytest.fixture
def context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="code-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="question_comprehension_info",
        node_key="fetch_questions",
        capability="fetch_questions",
        workspace={"id": "ws-a"},
        job={
            "id": "job-1",
            "workspace_id": "ws-a",
            "workflow_key": "question_comprehension_info",
            "source_type": "question",
            "source_id": "q-1",
            "batch_id": "",
            "title": "Question 1",
            "storage_dir": str(tmp_path),
            "stem": "",
        },
        job_dir=tmp_path,
        log_path=tmp_path / "run.log",
        inputs=(),
        expected_outputs=("out.json",),
    )


def _write_node(repo_root: Path, name: str, body: str) -> str:
    path = repo_root / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return name


def _executor(
    repo_root: Path,
    path: str,
    *,
    timeout_seconds: int = 60,
) -> CodeExecutor:
    return CodeExecutor(
        "code-default",
        {"fetch_questions": CodeCapabilityConfig(path=path, timeout_seconds=timeout_seconds)},
        repo_root=repo_root,
    )


def test_constructor_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside the repository root"):
        _executor(tmp_path, "nodes/missing.py")


def test_constructor_rejects_non_file_path(tmp_path: Path) -> None:
    (tmp_path / "nodes").mkdir()
    with pytest.raises(ValueError, match="inside the repository root"):
        _executor(tmp_path, "nodes")


def test_config_rejects_absolute_and_escape_paths() -> None:
    with pytest.raises(ValueError, match="must not be absolute"):
        CodeCapabilityConfig(path="/etc/passwd")
    with pytest.raises(ValueError, match="must not contain '..'"):
        CodeCapabilityConfig(path="../outside.py")


def test_supports(tmp_path: Path) -> None:
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    assert executor.supports("fetch_questions")
    assert not executor.supports("other")


def test_execute_missing_capability(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(replace(context, capability="missing"))
    assert result.status == "failed"
    assert "not supported" in result.error_message


def test_execute_success_writes_expected_outputs(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(
        tmp_path,
        "node_ok.py",
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text("{}", encoding="utf-8")
        """,
    )
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "completed"
    assert result.produced_artifacts == ("out.json",)


def test_execute_fails_when_outputs_missing(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_noop.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "Missing outputs" in result.error_message


def test_execute_propagates_node_exception(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(
        tmp_path,
        "node_boom.py",
        """
        def run(job, job_dir, runtime):
            raise RuntimeError("boom")
        """,
    )
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "RuntimeError: boom" in result.error_message


def test_execute_fails_without_run_callable(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_no_run.py", "VALUE = 1\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "callable 'run'" in result.error_message


def test_execute_timeout_kills_child(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(
        tmp_path,
        "node_slow.py",
        """
        import time

        def run(job, job_dir, runtime):
            time.sleep(60)
        """,
    )
    executor = _executor(tmp_path, path, timeout_seconds=1)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "timed out after 1s" in result.error_message


def test_cancel_before_start(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    executor.cancel(context.execution_id)
    result = executor.execute(context)
    assert result.status == "cancelled"
