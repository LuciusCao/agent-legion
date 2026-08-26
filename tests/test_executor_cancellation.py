from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.cancellation import (
    CancellationToken,
    CancelledError,
    SubprocessTracker,
)
from server.app.executors.code import CodeExecutor
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.runtime import ExecutionRuntime
from tests.helpers.velites_sandbox import sandboxed


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

    @pytest.mark.slow
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

    @pytest.mark.slow
    def test_wait_for_returns_when_process_exits(self) -> None:
        tracker = SubprocessTracker(grace_seconds=0.1)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.1)"])
        tracker.register("e1", proc)
        assert tracker.wait_for("e1", timeout=2.0) is True
        tracker.unregister("e1")


# Code node bodies used by the isolated-code tests. They are written to node
# files under tmp_path (which doubles as the repo root) so the multiprocessing
# child can load them by path after spawn.

_COOPERATIVE_NODE = """
def run(job, job_dir, runtime):
    token = (runtime or {}).get("cancellation")
    if token is not None:
        token.raise_if_cancelled()
    (job_dir / "out.json").write_text("{}", encoding="utf-8")
"""

_BLOCKED_NODE = """
import time


def run(job, job_dir, runtime):
    while True:
        time.sleep(10)
"""


def _code_executor(
    repo_root: Path,
    capability: str,
    *,
    cancellation_grace_seconds: float = 0.5,
) -> CodeExecutor:
    # Post-#96 node code arrives as DB-published text on the context and runs
    # sandboxed; there is no repo file to bind.
    return CodeExecutor(
        repo_root=repo_root,
        cancellation_grace_seconds=cancellation_grace_seconds,
    )


def _code_context(
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
        executor_id="code-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="test",
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


class TestCodeExecutorIsolation:
    @pytest.mark.slow
    def test_code_executor_runs_node_sandboxed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sandboxed(monkeypatch)
        executor = _code_executor(tmp_path, "cooperative")
        ctx = _code_context(tmp_path, "exec-cooperative", "cooperative", ("out.json",))
        ctx = replace(ctx, node_code=textwrap.dedent(_COOPERATIVE_NODE))
        result = executor.execute(ctx)
        assert result.status == "completed"
        assert (ctx.job_dir / "out.json").is_file()

    @pytest.mark.slow
    def test_code_executor_cancels_blocked_child(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from server.app.executors.cancellation import CancellationToken

        sandboxed(monkeypatch)
        executor = _code_executor(tmp_path, "blocked", cancellation_grace_seconds=0.3)
        # In-flight cancellation flows through the runtime token (the parent's
        # ExecutionRuntime cancel path); executor.cancel marks pre-start only.
        token = CancellationToken(threading.Event())
        ctx = _code_context(tmp_path, "exec-blocked", "blocked", ("out.json",))
        ctx = replace(
            ctx,
            node_code=textwrap.dedent(_BLOCKED_NODE),
            runtime={"cancellation": token},
        )
        result_holder: dict[str, ExecutionResult] = {}

        def run() -> None:
            result_holder["result"] = executor.execute(ctx)

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.5)
        token.cancel()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert result_holder["result"].status == "cancelled"

    @pytest.mark.slow
    def test_code_executor_pre_start_cancellation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_sandbox_needed = monkeypatch  # pre-start cancel never spawns
        executor = _code_executor(tmp_path, "cooperative", cancellation_grace_seconds=0.3)
        executor.cancel("exec-pre")
        ctx = _code_context(tmp_path, "exec-pre", "cooperative", ("out.json",))
        ctx = replace(ctx, node_code=textwrap.dedent(_COOPERATIVE_NODE))
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
        assert token.wait(timeout=10), "cancellation token was not set in time"
        return ExecutionResult(
            status="cancelled",
            exit_code=-1,
            log_path=str(context.log_path),
        )

    def cancel(self, execution_id: str) -> None:
        self.cancel_calls.append(execution_id)


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
    runtime = ExecutionRuntime(
        leases=leases,
        executor=executor,
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
        workflow_key="test",
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
        workflow_key=claim.workflow_key,
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
