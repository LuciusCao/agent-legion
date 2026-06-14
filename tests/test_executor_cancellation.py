from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.cancellation import (
    CancellationToken,
    CancelledError,
    SubprocessTracker,
)
from server.app.executors.config import OpenClawCapabilityConfig, PiCapabilityConfig
from server.app.executors.local import LocalExecutor
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.openclaw import OpenClawExecutor
from server.app.executors.pi import PiExecutor
from server.app.executors.protocol import Executor
from server.app.executors.runtime import ExecutionRuntime
from server.app.pipeline.openclaw import OpenClawRunner
from server.app.pipelines.pi_runner import PiConfig


class TestCancellationToken:
    def test_initially_not_cancelled(self) -> None:
        token = CancellationToken()
        assert token.is_cancelled() is False
        assert token.wait(timeout=0.01) is False

    def test_cancel_sets_flag_and_wait_returns_true(self) -> None:
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled() is True
        assert token.wait(timeout=0) is True

    def test_raise_if_cancelled_raises_when_cancelled(self) -> None:
        token = CancellationToken()
        token.cancel()
        with pytest.raises(CancelledError):
            token.raise_if_cancelled()


class TestSubprocessTracker:
    def test_register_unregister(self) -> None:
        tracker = SubprocessTracker(grace_seconds=0.1)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        tracker.register("e1", proc)
        assert tracker.active() == ["e1"]
        tracker.unregister("e1")
        assert tracker.active() == []
        proc.kill()
        proc.wait()

    @pytest.mark.skipif(not hasattr(os, "killpg"), reason="process groups require POSIX")
    def test_cancel_terminates_then_kills_blocked_child(self) -> None:
        tracker = SubprocessTracker(grace_seconds=0.2)
        script = (
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(1000)"
        )
        proc = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
        tracker.register("e1", proc)
        start = time.monotonic()
        tracker.cancel("e1")
        elapsed = time.monotonic() - start
        assert proc.poll() is not None
        assert elapsed < 1.0
        assert tracker.active() == []

    def test_wait_for_returns_when_process_exits(self) -> None:
        tracker = SubprocessTracker(grace_seconds=0.1)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.1)"])
        tracker.register("e1", proc)
        assert tracker.wait_for("e1", timeout=2.0) is True
        tracker.unregister("e1")


# Handlers used by isolated-local tests.  They are referenced by import path so
# the multiprocessing child can resolve them after spawn.


def cooperative_handler(
    _job: dict[str, Any], job_dir: Path, runtime: dict[str, Any] | None
) -> None:
    runtime = runtime or {}
    token = runtime.get("cancellation")
    for _ in range(1000):
        if token is not None:
            token.raise_if_cancelled()
        time.sleep(0.005)
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def blocked_handler(_job: dict[str, Any], _job_dir: Path, _runtime: dict[str, Any] | None) -> None:
    while True:
        time.sleep(10)


def _local_context(
    tmp_path: Path,
    execution_id: str,
    capability: str,
    expected_outputs: tuple[str, ...] = (),
) -> ExecutionContext:
    job_dir = tmp_path / "job"
    return ExecutionContext(
        execution_id=execution_id,
        lease_id="lease-1",
        node_run_id=1,
        executor_id="local-default",
        workspace_id="ws-a",
        job_id="job-1",
        pipeline_key="test",
        node_key=capability,
        capability=capability,
        workspace={},
        job={"id": "job-1"},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=(),
        expected_outputs=expected_outputs,
        runtime={},
    )


class TestLocalExecutorIsolation:
    def test_local_executor_rejects_non_importable_handler(self) -> None:
        with pytest.raises(ValueError, match="must be importable"):
            LocalExecutor(
                "local-default",
                {"unsafe": lambda _job, _job_dir, _runtime: None},
            )

    def test_local_executor_runs_handler_in_isolated_child(self, tmp_path: Path) -> None:
        executor = LocalExecutor(
            "local-default",
            {"cooperative": cooperative_handler},
            cancellation_grace_seconds=0.5,
        )
        ctx = _local_context(tmp_path, "exec-cooperative", "cooperative", ("out.json",))
        result = executor.execute(ctx)
        assert result.status == "completed"
        assert (ctx.job_dir / "out.json").is_file()

    def test_local_executor_cancels_blocked_child(self, tmp_path: Path) -> None:
        executor = LocalExecutor(
            "local-default",
            {"blocked": blocked_handler},
            cancellation_grace_seconds=0.3,
        )
        ctx = _local_context(tmp_path, "exec-blocked", "blocked", ("out.json",))
        result_holder: dict[str, ExecutionResult] = {}

        def run() -> None:
            result_holder["result"] = executor.execute(ctx)

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.1)
        executor.cancel(ctx.execution_id)
        thread.join(timeout=3.0)
        assert not thread.is_alive()
        assert result_holder["result"].status == "cancelled"

    def test_local_executor_pre_start_cancellation(self, tmp_path: Path) -> None:
        executor = LocalExecutor(
            "local-default",
            {"cooperative": cooperative_handler},
            cancellation_grace_seconds=0.3,
        )
        executor.cancel("exec-pre")
        ctx = _local_context(tmp_path, "exec-pre", "cooperative", ("out.json",))
        result = executor.execute(ctx)
        assert result.status == "cancelled"
        assert result.exit_code == -1
        assert "before starting" in result.error_message


def _pi_skill(skill_dir: Path) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "references" / "output-contract.md").write_text("contract", encoding="utf-8")
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts" / "validate_output.py").write_text(
        "import sys; sys.exit(0)", encoding="utf-8"
    )


class TestPiExecutorCancellation:
    @pytest.mark.skipif(not hasattr(os, "killpg"), reason="process groups require POSIX")
    def test_pi_executor_cancels_active_subprocess(self, tmp_path: Path) -> None:
        fake_pi = tmp_path / "fake_pi"
        fake_pi.write_text("#!/bin/bash\ntrap '' TERM\nsleep 1000\n")
        fake_pi.chmod(0o755)

        skill_dir = tmp_path / "skills" / "reading_analysis" / "extract_keywords"
        _pi_skill(skill_dir)

        executor = PiExecutor(
            "pi-default",
            PiConfig(binary=str(fake_pi), cancellation_grace_seconds=0.3),
            tmp_path / "skills",
            {"extract_keywords": PiCapabilityConfig(skill="reading_analysis/extract_keywords")},
        )
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        token = CancellationToken()
        ctx = ExecutionContext(
            execution_id="exec-pi",
            lease_id="lease-1",
            node_run_id=1,
            executor_id="pi-default",
            workspace_id="ws-a",
            job_id="job-1",
            pipeline_key="reading_analysis",
            node_key="extract_keywords",
            capability="extract_keywords",
            workspace={},
            job={"id": "job-1", "storage_dir": str(job_dir)},
            job_dir=job_dir,
            log_path=tmp_path / "run.log",
            inputs=(),
            expected_outputs=(),
            runtime={"cancellation": token},
        )
        result_holder: dict[str, ExecutionResult] = {}

        def run() -> None:
            result_holder["result"] = executor.execute(ctx)

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.1)
        token.cancel()
        executor.cancel("exec-pi")
        thread.join(timeout=3.0)
        assert not thread.is_alive()
        result = result_holder["result"]
        assert result.status == "failed"
        assert "execution was cancelled" in result.error_message
        assert executor._tracker.active() == []


class TestOpenClawExecutorCancellation:
    def test_openclaw_executor_cancels_active_subprocess(self, tmp_path: Path) -> None:
        command = [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(1000)",
        ]
        runner = OpenClawRunner(
            command_template=command,
            cwd=tmp_path,
            timeout_seconds=600,
            cancellation_grace_seconds=0.3,
        )
        executor = OpenClawExecutor(
            "oc-default",
            runner,
            {"interaction_generate": OpenClawCapabilityConfig(skill="interaction")},
        )
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        token = CancellationToken()
        ctx = ExecutionContext(
            execution_id="exec-oc",
            lease_id="lease-1",
            node_run_id=1,
            executor_id="oc-default",
            workspace_id="ws-a",
            job_id="job-1",
            pipeline_key="reading_analysis",
            node_key="interaction_generate",
            capability="interaction_generate",
            workspace={},
            job={"id": "job-1", "storage_dir": str(job_dir)},
            job_dir=job_dir,
            log_path=tmp_path / "run.log",
            inputs=(),
            expected_outputs=("interactions.json",),
            runtime={"cancellation": token},
        )
        result_holder: dict[str, ExecutionResult] = {}

        def run() -> None:
            result_holder["result"] = executor.execute(ctx)

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.1)
        token.cancel()
        executor.cancel("exec-oc")
        thread.join(timeout=3.0)
        assert not thread.is_alive()
        result = result_holder["result"]
        assert result.status == "failed"
        assert "execution was cancelled" in result.error_message
        assert runner._tracker.active() == []


class FakeCancellingExecutor:
    id = "fake"
    kind = "fake"

    def __init__(self) -> None:
        self.cancel_calls: list[str] = []
        self.tokens: list[CancellationToken] = []

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        token = (context.runtime or {}).get("cancellation")
        self.tokens.append(token)
        token.wait(timeout=10)
        return ExecutionResult(
            status="cancelled",
            exit_code=-1,
            log_path=str(context.log_path),
        )

    def cancel(self, execution_id: str) -> None:
        self.cancel_calls.append(execution_id)


class FakeRegistry:
    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    def require(self, executor_id: str, capability: str) -> Executor:
        return self._executor


class FakeLeaseRepository:
    def __init__(self, heartbeat_active: bool = True) -> None:
        self.heartbeat_active = heartbeat_active
        self.finished: list[tuple[str, ExecutionResult]] = []

    def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        return self.heartbeat_active

    def finish(self, lease_id: str, result: ExecutionResult) -> bool:
        self.finished.append((lease_id, result))
        return True


def test_runtime_passes_cancellation_token(tmp_path: Path) -> None:
    executor = FakeCancellingExecutor()
    leases = FakeLeaseRepository()
    registry = FakeRegistry(executor)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=5,
        cancellation_grace_seconds=0.2,
    )
    claim = _make_claim()
    context = _make_context(claim, tmp_path / "job")

    result_holder: dict[str, ExecutionResult] = {}

    def run() -> None:
        result_holder["result"] = runtime.run(claim, context)

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.1)
    assert executor.tokens
    assert isinstance(executor.tokens[0], CancellationToken)
    runtime.cancel(claim.execution_id)
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result_holder["result"].status == "cancelled"
    assert len(leases.finished) == 1


def _make_claim() -> Any:
    from server.app.executors.models import ClaimedExecution

    return ClaimedExecution(
        lease_id="lease-1",
        execution_id="exec-1",
        node_run_id=1,
        executor_id="fake",
        workspace_id="ws-1",
        job_id="job-1",
        pipeline_key="test",
        node_key="test-node",
        capability="test-capability",
        log_path="/tmp/test.log",
    )


def _make_context(claim: Any, job_dir: Path) -> ExecutionContext:
    return ExecutionContext(
        execution_id=claim.execution_id,
        lease_id=claim.lease_id,
        node_run_id=claim.node_run_id,
        executor_id=claim.executor_id,
        workspace_id=claim.workspace_id,
        job_id=claim.job_id,
        pipeline_key=claim.pipeline_key,
        node_key=claim.node_key,
        capability=claim.capability,
        workspace={},
        job={},
        job_dir=job_dir,
        log_path=Path("/tmp/test.log"),
        inputs=(),
        expected_outputs=(),
        runtime={},
    )
