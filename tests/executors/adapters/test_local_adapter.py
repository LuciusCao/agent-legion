from __future__ import annotations

import json
import multiprocessing
from dataclasses import replace

import pytest

from server.app.executors.local import LocalExecutor
from server.app.executors.models import ExecutionContext
from tests.executors.adapters.helpers import (
    logging_local_handler,
    noop_local_handler,
    raising_local_handler,
    record_runtime_handler,
    write_output_handler,
)


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


def test_local_executor_writes_logs_to_log_path(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The isolated child redirects fd 1/2 onto the log file with dup2. Under
    # Linux's default fork start method the child also inherits pytest's
    # captured sys.stdout, so print()/logging keep writing to the capture
    # buffer instead of fd 1 and the log file stays empty. In production (and
    # under spawn, the macOS default) the child's stdout is bound to the real
    # fd, so pin the spawn start method here to test that behavior.
    spawn = multiprocessing.get_context("spawn")
    monkeypatch.setattr(multiprocessing, "Process", spawn.Process)
    monkeypatch.setattr(multiprocessing, "Pipe", spawn.Pipe)
    monkeypatch.setattr(multiprocessing, "Event", spawn.Event)
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
