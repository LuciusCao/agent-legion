from __future__ import annotations

import json
from dataclasses import replace

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
