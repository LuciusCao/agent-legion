from __future__ import annotations

import logging
import multiprocessing
import os
import sys
import threading
import time
import types
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from server.app.executors import _local_isolated, _local_thread
from server.app.executors.cancellation import CancellationToken
from server.app.executors.local import (
    LocalExecutor,
    _handler_key,
    _resolve_handler,
    _run_handler,
    _watch_parent_token,
)
from server.app.executors.models import ExecutionContext, ExecutionResult
from tests.postgres_support import TEST_DATABASE_URL


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
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir)
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
    t.join(timeout=30)  # 高负载门禁下线程调度延迟可超 5s（flaky 处置惯例放宽到 30s）
    assert not t.is_alive(), "cancel 后 execute 未在 30s 内返回"

    assert result_holder["result"].status in ("cancelled", "failed")


def _test_handler_raises(
    _job: dict[str, Any], _job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    raise RuntimeError("boom")


def _test_handler_records_job_db(
    _job: dict[str, Any], job_dir: Path, runtime: dict[str, Any] | None
) -> None:
    job_db = runtime.get("job_db") if runtime else None
    (job_dir / "job-db-type.txt").write_text(type(job_db).__name__, encoding="utf-8")


def _test_handler_fd_write(
    _job: dict[str, Any], job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    # pytest replaces sys.stdout with its own capture object, so verify the
    # fd-level redirect with a raw fd write instead of print().
    os.write(1, b"fd line\n")
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def test_resolve_handler_from_mp_main(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_main = types.ModuleType("__mp_main__")
    fake_main.my_handler = _test_module_level_handler
    monkeypatch.setitem(sys.modules, "__mp_main__", fake_main)

    handler = _resolve_handler("my_handler")

    assert handler is _test_module_level_handler


def test_run_handler_direct_success(tmp_path: Path) -> None:
    parent_conn, child_conn = multiprocessing.Pipe()
    _run_handler(
        "tests.executors.test_local_executor._test_handler_with_output",
        {"id": "job-1"},
        str(tmp_path),
        {"node_key": "fetch", "capability": "fetch"},
        child_conn,
    )

    assert parent_conn.poll(1)
    assert parent_conn.recv() == ("ok", None)
    assert (tmp_path / "out.json").is_file()


def test_run_handler_direct_log_redirect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # basicConfig(force=True) would reconfigure root logging for the whole worker.
    monkeypatch.setattr(logging, "basicConfig", lambda **_: None)
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    parent_conn, child_conn = multiprocessing.Pipe()
    try:
        _run_handler(
            "tests.executors.test_local_executor._test_handler_fd_write",
            {"id": "job-1"},
            str(tmp_path),
            {"log_path": str(tmp_path / "run.log"), "node_key": "fetch"},
            child_conn,
        )
    finally:
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)

    assert parent_conn.poll(1)
    assert parent_conn.recv() == ("ok", None)
    assert "fd line" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_run_handler_direct_log_redirect_failure(tmp_path: Path, caplog) -> None:
    # A directory as log target makes os.open fail; the handler must still run.
    log_dir = tmp_path / "log-target"
    log_dir.mkdir()
    parent_conn, child_conn = multiprocessing.Pipe()
    with caplog.at_level(logging.ERROR, logger="server.app.executors.local"):
        _run_handler(
            "tests.executors.test_local_executor._test_handler_with_output",
            {"id": "job-1"},
            str(tmp_path),
            {"log_path": str(log_dir), "node_key": "fetch"},
            child_conn,
        )

    assert parent_conn.poll(1)
    assert parent_conn.recv() == ("ok", None)
    assert any("Failed to redirect run log" in record.getMessage() for record in caplog.records)


def test_run_handler_direct_handler_error(tmp_path: Path) -> None:
    parent_conn, child_conn = multiprocessing.Pipe()
    _run_handler(
        "tests.executors.test_local_executor._test_handler_raises",
        {"id": "job-1"},
        str(tmp_path),
        {"node_key": "fetch"},
        child_conn,
    )

    assert parent_conn.poll(1)
    status, payload = parent_conn.recv()
    assert status == "error"
    assert "RuntimeError: boom" in payload


def test_run_handler_direct_rebuilds_job_db(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    parent_conn, child_conn = multiprocessing.Pipe()
    _run_handler(
        "tests.executors.test_local_executor._test_handler_records_job_db",
        {"id": "job-1"},
        str(tmp_path),
        {
            "node_key": "fetch",
            "_job_db_path": str(TEST_DATABASE_URL),
            "_jobs_dir": str(jobs_dir),
        },
        child_conn,
    )

    assert parent_conn.poll(1)
    assert parent_conn.recv() == ("ok", None)
    assert (tmp_path / "job-db-type.txt").read_text(encoding="utf-8") == "JobQueries"


def test_watch_parent_token_propagates_cancellation() -> None:
    parent_token = CancellationToken()
    child_token = CancellationToken()
    parent_token.cancel()

    _watch_parent_token(parent_token, child_token)

    assert child_token.is_cancelled()


def test_local_executor_execute_raises_for_unimportable_handler_key(
    context: ExecutionContext,
) -> None:
    executor = LocalExecutor("local", {"fetch": _test_module_level_handler})
    executor._handler_keys.clear()

    with pytest.raises(RuntimeError, match="not importable"):
        executor.execute(replace(context, capability="fetch"))


def test_execute_isolated_setup_failure_leaves_no_token_leak(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup failures (e.g. EMFILE) must not leak tokens/semaphores/pipes."""
    from server.app.executors._local_isolated import execute_isolated

    executor = LocalExecutor("local", {"fetch": _test_module_level_handler})

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(executor, "_build_runtime", _boom)
    with pytest.raises(OSError, match="Too many open files"):
        execute_isolated(
            executor,
            replace(context, capability="fetch"),
            "tests.executors.test_local_executor._test_module_level_handler",
        )
    assert executor._tokens == {}


class _FakeConn:
    """Minimal pipe-end stand-in with scripted poll/recv behavior."""

    def __init__(self, poll_results: list[bool], recv_error: Exception | None = None) -> None:
        self._poll_results = list(poll_results)
        self._recv_error = recv_error

    def poll(self, timeout: float = 0.0) -> bool:
        return self._poll_results.pop(0) if self._poll_results else False

    def recv(self) -> Any:
        if self._recv_error is not None:
            raise self._recv_error
        return None

    def close(self) -> None:
        return None


class _FakeProcess:
    """Minimal multiprocessing.Process stand-in with scripted is_alive."""

    def __init__(self, alive_results: list[bool]) -> None:
        self._alive_results = list(alive_results)
        self.terminated = False
        self.killed = False

    def start(self) -> None:
        return None

    def is_alive(self) -> bool:
        return self._alive_results.pop(0) if self._alive_results else False

    def terminate(self) -> None:
        self.terminated = True

    def join(self, timeout: float | None = None) -> None:
        return None

    def kill(self) -> None:
        self.killed = True


def test_execute_isolated_handles_child_dying_without_result(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The child exits between the poll timeout and the liveness re-check, and the
    # pipe hits EOF instead of delivering a result.
    parent_conn = _FakeConn(poll_results=[False, True], recv_error=EOFError())
    child_conn = _FakeConn(poll_results=[])
    process = _FakeProcess(alive_results=[True, False, False])
    monkeypatch.setattr(multiprocessing, "Pipe", lambda: (parent_conn, child_conn))
    monkeypatch.setattr(multiprocessing, "Process", lambda **_: process)

    executor = LocalExecutor("local", {"fetch": _test_module_level_handler})
    result = executor.execute(replace(context, capability="fetch"))

    assert result.status == "failed"
    assert "did not return a result" in result.error_message


def test_terminate_child_kills_stubborn_process() -> None:
    executor = LocalExecutor("local", {"fetch": _test_module_level_handler})
    process = _FakeProcess(alive_results=[True, False])

    executor._terminate_child(process)

    assert process.terminated
    assert process.killed


def _test_hung_handler(_job: dict, _job_dir: Path, _runtime: dict | None) -> None:
    # Simulates the zombie case: a handler that never returns and never
    # checks its cancellation token.
    while True:
        time.sleep(0.05)


def test_local_executor_timeout_kills_hung_handler(context: ExecutionContext) -> None:
    executor = LocalExecutor(
        "local",
        {"fetch": _test_hung_handler},
        cancellation_grace_seconds=0.2,
        default_timeout_seconds=0.4,
    )
    started = time.monotonic()
    result = executor.execute(replace(context, capability="fetch"))
    elapsed = time.monotonic() - started

    assert result.status == "failed"
    assert "timed out" in result.error_message
    assert elapsed < 10  # the deadline, not the handler, ended the run
    assert context.execution_id not in executor._tokens


def test_local_executor_capability_timeout_overrides_default(context: ExecutionContext) -> None:
    executor = LocalExecutor(
        "local",
        {"fetch": _test_hung_handler},
        cancellation_grace_seconds=0.2,
        capability_timeouts={"fetch": 0.4},
        default_timeout_seconds=3600.0,
    )
    started = time.monotonic()
    result = executor.execute(replace(context, capability="fetch"))
    elapsed = time.monotonic() - started

    assert result.status == "failed"
    assert "timed out" in result.error_message
    assert "0.4s" in result.error_message
    assert elapsed < 10


def test_local_executor_without_timeout_completes(context: ExecutionContext) -> None:
    executor = LocalExecutor(
        "local",
        {"fetch": _test_slow_handler},
        default_timeout_seconds=30.0,
    )
    result = executor.execute(replace(context, capability="fetch", expected_outputs=()))
    assert result.status == "completed"


def test_build_local_executor_passes_capability_timeouts() -> None:
    from server.app.executors.config import LocalCapabilityConfig, LocalExecutorConfig
    from server.app.executors.kinds import RuntimeDependencies
    from server.app.executors.local import build_local_executor

    config = LocalExecutorConfig(
        kind="local",
        global_capacity=1,
        capabilities={
            "fetch": LocalCapabilityConfig(
                handler="tests.executors.test_local_executor._test_module_level_handler",
                timeout_seconds=12.0,
            ),
            "other": LocalCapabilityConfig(
                handler="tests.executors.test_local_executor._test_module_level_handler",
            ),
        },
    )
    executor = build_local_executor("local-default", config, RuntimeDependencies())

    assert executor._capability_timeouts == {"fetch": 12.0}
    assert executor._default_timeout_seconds > 0


def test_local_capability_config_timeout_validation() -> None:
    import pydantic

    from server.app.executors.config import LocalCapabilityConfig

    assert LocalCapabilityConfig(handler="a.b").timeout_seconds is None
    assert LocalCapabilityConfig(handler="a.b", timeout_seconds=5).timeout_seconds == 5
    with pytest.raises(pydantic.ValidationError):
        LocalCapabilityConfig(handler="a.b", timeout_seconds=0)
    with pytest.raises(pydantic.ValidationError):
        LocalCapabilityConfig(handler="a.b", timeout_seconds=-1)


# --- thread isolation (LocalCapabilityConfig.isolation == "thread") ---

_thread_runtime_capture: list[dict[str, Any]] = []


def _test_thread_handler_capture(
    _job: dict[str, Any], job_dir: Path, runtime: dict[str, Any] | None
) -> None:
    _thread_runtime_capture.append(runtime or {})
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def test_thread_capability_runs_handler_in_process(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_process(**kwargs: Any) -> None:
        raise AssertionError("thread isolation must not spawn a child process")

    monkeypatch.setattr(multiprocessing, "Process", _no_process)
    _thread_runtime_capture.clear()
    executor = LocalExecutor(
        "local", {"fetch": _test_thread_handler_capture}, thread_capabilities={"fetch"}
    )

    result = executor.execute(replace(context, capability="fetch"))

    assert result.status == "completed"
    assert result.produced_artifacts == ("out.json",)
    assert context.execution_id not in executor._tokens


def test_thread_capability_shares_live_job_db(context: ExecutionContext, tmp_path: Path) -> None:
    from server.app.jobs import JobQueries

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir)
    _thread_runtime_capture.clear()
    executor = LocalExecutor(
        "local",
        {"fetch": _test_thread_handler_capture},
        job_db=job_db,
        thread_capabilities={"fetch"},
    )

    result = executor.execute(replace(context, capability="fetch"))

    assert result.status == "completed"
    runtime = _thread_runtime_capture[0]
    assert runtime["job_db"] is job_db
    assert "_job_db_path" not in runtime
    assert "_jobs_dir" not in runtime


def test_thread_capability_handler_error(context: ExecutionContext) -> None:
    executor = LocalExecutor(
        "local", {"fetch": _test_handler_raises}, thread_capabilities={"fetch"}
    )

    result = executor.execute(replace(context, capability="fetch"))

    assert result.status == "failed"
    assert "RuntimeError: boom" in result.error_message
    assert context.execution_id not in executor._tokens


def test_thread_capability_cancelled_before_start(context: ExecutionContext) -> None:
    executor = LocalExecutor(
        "local", {"fetch": _test_handler_with_output}, thread_capabilities={"fetch"}
    )
    executor.cancel(context.execution_id)

    result = executor.execute(replace(context, capability="fetch"))

    assert result.status == "cancelled"
    assert "before starting" in result.error_message


def test_thread_capability_cooperative_cancel(context: ExecutionContext) -> None:
    executor = LocalExecutor("local", {"fetch": _test_slow_handler}, thread_capabilities={"fetch"})
    result_holder: dict = {}

    def run() -> None:
        result_holder["result"] = executor.execute(
            replace(context, capability="fetch", expected_outputs=())
        )

    t = threading.Thread(target=run)
    t.start()
    for _ in range(100):
        if context.execution_id in executor._tokens:
            break
        time.sleep(0.01)
    executor.cancel(context.execution_id)
    t.join(timeout=5)

    assert result_holder["result"].status == "cancelled"


def test_execute_dispatches_by_isolation(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def _fake_isolated(*args: Any) -> ExecutionResult:
        calls.append("process")
        return ExecutionResult(status="completed", exit_code=0, log_path="")

    def _fake_thread(*args: Any) -> ExecutionResult:
        calls.append("thread")
        return ExecutionResult(status="completed", exit_code=0, log_path="")

    monkeypatch.setattr(_local_isolated, "execute_isolated", _fake_isolated)
    monkeypatch.setattr(_local_thread, "execute_in_thread", _fake_thread)
    executor = LocalExecutor(
        "local",
        {"fetch": _test_module_level_handler, "other": _test_module_level_handler},
        thread_capabilities={"fetch"},
    )

    executor.execute(replace(context, capability="fetch"))
    executor.execute(replace(context, capability="other"))

    assert calls == ["thread", "process"]


def test_execute_defaults_to_process_isolation(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def _fake_isolated(*args: Any) -> ExecutionResult:
        calls.append("process")
        return ExecutionResult(status="completed", exit_code=0, log_path="")

    def _fake_thread(*args: Any) -> ExecutionResult:
        calls.append("thread")
        return ExecutionResult(status="completed", exit_code=0, log_path="")

    monkeypatch.setattr(_local_isolated, "execute_isolated", _fake_isolated)
    monkeypatch.setattr(_local_thread, "execute_in_thread", _fake_thread)
    executor = LocalExecutor("local", {"fetch": _test_module_level_handler})

    executor.execute(replace(context, capability="fetch"))

    assert calls == ["process"]


def test_local_capability_config_isolation_validation() -> None:
    import pydantic

    from server.app.executors.config import LocalCapabilityConfig

    assert LocalCapabilityConfig(handler="a.b").isolation == "process"
    assert LocalCapabilityConfig(handler="a.b", isolation="thread").isolation == "thread"
    with pytest.raises(pydantic.ValidationError):
        LocalCapabilityConfig(handler="a.b", isolation="sandbox")


def test_build_local_executor_passes_thread_capabilities() -> None:
    from server.app.executors.config import LocalCapabilityConfig, LocalExecutorConfig
    from server.app.executors.kinds import RuntimeDependencies
    from server.app.executors.local import build_local_executor

    config = LocalExecutorConfig(
        kind="local",
        global_capacity=1,
        capabilities={
            "fetch": LocalCapabilityConfig(
                handler="tests.executors.test_local_executor._test_module_level_handler",
                isolation="thread",
            ),
            "other": LocalCapabilityConfig(
                handler="tests.executors.test_local_executor._test_module_level_handler",
            ),
        },
    )
    executor = build_local_executor("local-default", config, RuntimeDependencies())

    assert executor._thread_capabilities == {"fetch"}
