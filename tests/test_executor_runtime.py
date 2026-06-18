from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest

from server.app.executors.models import ClaimedExecution, ExecutionContext, ExecutionResult
from server.app.executors.protocol import Executor
from server.app.executors.runtime import ExecutionRuntime


class FakeExecutor:
    """Fake executor blocked by a threading.Event for deterministic tests."""

    id: str
    kind: str = "fake"

    def __init__(self, executor_id: str, result: ExecutionResult | None = None) -> None:
        self.id = executor_id
        self._result = result
        self._block = threading.Event()
        self._cancelled: set[str] = set()
        self.execute_calls: list[ExecutionContext] = []
        self.cancel_calls: list[str] = []
        self.execute_started = threading.Event()

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        self.execute_calls.append(context)
        self.execute_started.set()
        if self._result is not None:
            return self._result
        self._block.wait()
        if context.execution_id in self._cancelled:
            self._cancelled.discard(context.execution_id)
            return ExecutionResult(
                status="cancelled",
                exit_code=-1,
                error_message="execution was cancelled",
                log_path=str(context.log_path),
            )
        return ExecutionResult(
            status="completed",
            exit_code=0,
            log_path=str(context.log_path),
        )

    def cancel(self, execution_id: str) -> None:
        self.cancel_calls.append(execution_id)
        self._cancelled.add(execution_id)
        self._block.set()

    def unblock(self) -> None:
        self._block.set()


class RaisingExecutor:
    """Executor that raises an exception from execute."""

    id: str
    kind: str = "raising"

    def __init__(self, executor_id: str, exc: Exception) -> None:
        self.id = executor_id
        self._exc = exc
        self.cancel_calls: list[str] = []

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        raise self._exc

    def cancel(self, execution_id: str) -> None:
        self.cancel_calls.append(execution_id)


class FakeLeaseRepository:
    """In-memory lease repository that records heartbeats and finishes."""

    def __init__(self, heartbeat_active: bool = True) -> None:
        self.heartbeat_active = heartbeat_active
        self.heartbeats: list[tuple[str, int]] = []
        self.finished: list[tuple[str, ExecutionResult]] = []

    def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        self.heartbeats.append((lease_id, ttl_seconds))
        return self.heartbeat_active

    def finish(self, lease_id: str, result: ExecutionResult) -> bool:
        self.finished.append((lease_id, result))
        return True


class FakeRegistry:
    """Registry that always returns the configured fake executor."""

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    def require(self, executor_id: str, capability: str) -> Executor:
        return self._executor


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    return tmp_path / "job"


def _make_claim(executor_id: str = "fake") -> ClaimedExecution:
    return ClaimedExecution(
        lease_id=f"lease-{uuid.uuid4().hex}",
        execution_id=f"exec-{uuid.uuid4().hex}",
        node_run_id=1,
        executor_id=executor_id,
        workspace_id="ws-1",
        job_id="job-1",
        workflow_key="test-workflow",
        node_key="test-node",
        capability="test-capability",
        log_path="/tmp/test.log",
    )


def _make_context(claim: ClaimedExecution, job_dir: Path) -> ExecutionContext:
    return ExecutionContext(
        execution_id=claim.execution_id,
        lease_id=claim.lease_id,
        node_run_id=claim.node_run_id,
        executor_id=claim.executor_id,
        workspace_id=claim.workspace_id,
        job_id=claim.job_id,
        workflow_key=claim.workflow_key,
        node_key=claim.node_key,
        capability=claim.capability,
        workspace={},
        job={},
        job_dir=job_dir,
        log_path=Path("/tmp/test.log"),
        inputs=(),
        expected_outputs=(),
    )


def _run_in_thread(
    runtime: ExecutionRuntime, claim: ClaimedExecution, context: ExecutionContext
) -> tuple[threading.Thread, dict[str, ExecutionResult]]:
    holder: dict[str, ExecutionResult] = {}

    def target() -> None:
        holder["result"] = runtime.run(claim, context)

    thread = threading.Thread(target=target)
    thread.start()
    return thread, holder


def test_runtime_successful_completion(job_dir: Path) -> None:
    executor = FakeExecutor("fake", result=ExecutionResult(status="completed", exit_code=0))
    leases = FakeLeaseRepository()
    registry = FakeRegistry(executor)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
        heartbeat_interval_seconds=0.01,
        lease_ttl_seconds=30,
    )
    claim = _make_claim()
    context = _make_context(claim, job_dir)

    result = runtime.run(claim, context)

    assert result.status == "completed"
    assert result.exit_code == 0
    assert len(leases.finished) == 1
    assert leases.finished[0][0] == claim.lease_id
    assert leases.finished[0][1] == result
    assert len(executor.execute_calls) == 1
    assert executor.execute_calls[0].execution_id == claim.execution_id


def test_runtime_adapter_exception_normalized_to_failed(job_dir: Path) -> None:
    executor = RaisingExecutor("fake", RuntimeError("adapter boom"))
    leases = FakeLeaseRepository()
    registry = FakeRegistry(executor)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
        heartbeat_interval_seconds=0.01,
        lease_ttl_seconds=30,
    )
    claim = _make_claim()
    context = _make_context(claim, job_dir)

    result = runtime.run(claim, context)

    assert result.status == "failed"
    assert result.exit_code == 1
    assert "adapter boom" in result.error_message
    assert len(leases.finished) == 1
    assert leases.finished[0][0] == claim.lease_id
    assert leases.finished[0][1] == result


def test_runtime_periodic_heartbeat(job_dir: Path) -> None:
    executor = FakeExecutor("fake")
    leases = FakeLeaseRepository()
    registry = FakeRegistry(executor)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
        heartbeat_interval_seconds=0.01,
        lease_ttl_seconds=30,
    )
    claim = _make_claim()
    context = _make_context(claim, job_dir)

    thread, holder = _run_in_thread(runtime, claim, context)
    try:
        assert executor.execute_started.wait(timeout=1.0), "executor did not start in time"
        # Wait long enough for at least one heartbeat to fire.
        time.sleep(0.05)
        assert len(leases.heartbeats) >= 1
        assert all(hb[0] == claim.lease_id and hb[1] == 30 for hb in leases.heartbeats)
        executor.unblock()
    finally:
        thread.join(timeout=1.0)
        if thread.is_alive():
            executor.unblock()
            thread.join(timeout=1.0)

    result = holder["result"]
    assert result.status == "completed"
    assert result.exit_code == 0
    assert len(leases.finished) == 1


def test_runtime_lost_lease_cancels_and_fails(job_dir: Path) -> None:
    executor = FakeExecutor("fake")
    leases = FakeLeaseRepository(heartbeat_active=False)
    registry = FakeRegistry(executor)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
        heartbeat_interval_seconds=0.01,
        lease_ttl_seconds=30,
    )
    claim = _make_claim()
    context = _make_context(claim, job_dir)

    thread, holder = _run_in_thread(runtime, claim, context)
    try:
        assert executor.execute_started.wait(timeout=1.0), "executor did not start in time"
        # Wait for the heartbeat thread to detect the lost lease and cancel.
        thread.join(timeout=1.0)
    finally:
        if thread.is_alive():
            executor.unblock()
            thread.join(timeout=1.0)

    result = holder["result"]
    assert result.status == "failed"
    assert result.exit_code == 1
    assert "lease" in result.error_message.lower()
    assert claim.execution_id in executor.cancel_calls
    assert len(leases.finished) == 1
    assert leases.finished[0][0] == claim.lease_id


def test_runtime_cancellation_result_finishes_lease(job_dir: Path) -> None:
    executor = FakeExecutor("fake", result=ExecutionResult(status="cancelled", exit_code=-1))
    leases = FakeLeaseRepository()
    registry = FakeRegistry(executor)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
        heartbeat_interval_seconds=0.01,
        lease_ttl_seconds=30,
    )
    claim = _make_claim()
    context = _make_context(claim, job_dir)

    result = runtime.run(claim, context)

    assert result.status == "cancelled"
    assert result.exit_code == -1
    assert len(leases.finished) == 1
    assert leases.finished[0][0] == claim.lease_id
    assert leases.finished[0][1] == result
