from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.local import (
    LocalExecutor,
    _handler_key,
    _resolve_handler,
)
from server.app.executors.models import ExecutionContext


@pytest.fixture
def context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="test",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="question_comprehension_info",
        node_key="review_keywords",
        capability="review_keywords",
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
        inputs=("in.json",),
        expected_outputs=("out.json",),
    )


def _test_module_level_handler(
    _job: dict[str, Any], _job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    return None


def _test_handler_with_output(
    _job: dict[str, Any], job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def _test_handler_logging(
    _job: dict[str, Any], job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    print("log line")
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def _test_slow_handler(_job: dict, _job_dir: Path, runtime: dict | None) -> None:

    token = runtime.get("cancellation") if runtime else None
    for _ in range(50):
        if token is not None and token.is_cancelled():
            return
        time.sleep(0.05)


def test_handler_key_for_module_function():
    key = _handler_key(_test_module_level_handler)
    assert key is not None
    assert "_test_module_level_handler" in key


def test_handler_key_for_lambda_returns_none():
    key = _handler_key(lambda _j, _d, _r: None)
    assert key is None


def test_handler_key_for_local_function_returns_none():
    def local_handler(_j: dict, _d: Path, _r: dict | None) -> None:
        return None

    key = _handler_key(local_handler)
    assert key is None


def test_resolve_handler_with_full_module_path():
    handler = _resolve_handler("tests.executors.test_local_executor._test_module_level_handler")
    assert handler.__name__ == "_test_module_level_handler"
    assert handler.__module__ == "tests.executors.test_local_executor"


def test_resolve_handler_with_short_name():
    handler = _resolve_handler("tests.executors.test_local_executor._test_module_level_handler")
    assert handler.__name__ == "_test_module_level_handler"


def test_resolve_handler_missing_module():
    with pytest.raises(ImportError):
        _resolve_handler("no_such_module.no_such_handler")


def test_resolve_handler_missing_module_falls_back_to_workflows():
    # Key with a dot but module not found at top-level triggers fallback to server.app.workflows.
    with pytest.raises(ImportError):
        _resolve_handler("nonexistent_module.some_handler")


def test_resolve_handler_missing_function():
    with pytest.raises(AttributeError):
        _resolve_handler("tests.executors.test_local_executor._no_such_handler")


# Module-level non-callable attribute used for testing resolver validation.
_test_not_callable = 42


def test_resolve_handler_not_callable():
    with pytest.raises(ValueError, match="not callable"):
        _resolve_handler("tests.executors.test_local_executor._test_not_callable")


def test_local_executor_rejects_unsafe_handlers():
    with pytest.raises(ValueError, match="importable module-level functions"):
        LocalExecutor("local", {"fetch": lambda _j, _d, _r: None})


def test_local_executor_rejects_unresolvable_handlers():
    def local_handler(_j: dict, _d: Path, _r: dict | None) -> None:
        return None

    with pytest.raises(ValueError, match="importable module-level functions"):
        LocalExecutor("local", {"fetch": local_handler})


def _test_alias_handler(_job: dict, _job_dir: Path, _r: dict | None) -> None:
    return None


def test_local_executor_rejects_handler_with_mismatched_resolution() -> None:
    # Make _test_alias_handler look like _test_module_level_handler to the key resolver,
    # so _resolve_handler returns a different callable than the one we passed in.
    aliased = _test_alias_handler
    aliased.__module__ = "tests.executors.test_local_executor"
    aliased.__qualname__ = "_test_module_level_handler"

    with pytest.raises(ValueError, match="importable module-level functions"):
        LocalExecutor("local", {"fetch": aliased})


def test_local_executor_execute_missing_capability(context: ExecutionContext) -> None:
    executor = LocalExecutor("local", {"fetch": _test_module_level_handler})
    result = executor.execute(replace(context, capability="missing"))
    assert result.status == "failed"
    assert "not supported" in result.error_message


def test_local_executor_execute_with_job_db_in_runtime(
    context: ExecutionContext, tmp_path: Path
) -> None:
    from server.app.jobs import JobQueries

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    job_db = JobQueries(tmp_path / "jobs.sqlite", jobs_dir)
    executor = LocalExecutor("local", {"fetch": _test_handler_with_output}, job_db=job_db)
    result = executor.execute(replace(context, capability="fetch", expected_outputs=("out.json",)))
    assert result.status == "completed"


def test_local_executor_cancel_during_run(context: ExecutionContext) -> None:
    executor = LocalExecutor("local", {"fetch": _test_slow_handler})
    # Start execution in background and cancel it quickly.
    import threading

    result_holder: dict = {}

    def run() -> None:
        result_holder["result"] = executor.execute(
            replace(context, capability="fetch", expected_outputs=("out.json",))
        )

    t = threading.Thread(target=run)
    t.start()
    executor.cancel(context.execution_id)
    t.join(timeout=5)

    assert result_holder["result"].status in ("cancelled", "failed")
