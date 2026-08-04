"""Sandboxed custom code execution for the code executor (EXEC-CODE-003).

Split from ``code.py`` to keep it within its size budget; mirrors the
``_local_thread.py`` layout (functions take the executor instance and reach
its internals, same as the builtin isolated child helpers).

Fail-closed: without the velites wrapper (and thus without
sandbox-exec/bwrap) custom code never runs unsandboxed. The child is an
exec'd command line — the sandbox can only confine exec'd processes, not
multiprocessing forks — so job/runtime ride a payload pickle on stdin and the
result comes back via a pickle inside ``job_dir``.
"""

from __future__ import annotations

import contextlib
import os
import pickle
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from server.app.executors.cancellation import CancellationToken
from server.app.executors.models import ExecutionContext, ExecutionResult

if TYPE_CHECKING:
    from server.app.executors.code import CodeExecutor

# Real repository root hosting the ``server`` package (the sandboxed child
# imports ``server.app.*`` from here); differs from the executor's configured
# ``repo_root`` only in tests, where capabilities point into tmp dirs.
_SERVER_REPO_ROOT = Path(__file__).resolve().parents[3]


def _velites_binary(executor: CodeExecutor) -> str | None:
    """PATH probe for the velites sandbox wrapper, cached per executor."""
    if not executor._velites_probed:
        executor._velites_probed = True
        executor._velites_path = shutil.which("velites")
    return executor._velites_path


def execute_custom_sandboxed(
    executor: CodeExecutor, context: ExecutionContext, timeout_seconds: int
) -> ExecutionResult:
    """Run custom node code inside the velites OS sandbox (EXEC-CODE-003)."""
    velites = _velites_binary(executor)
    if velites is None:
        return ExecutionResult(
            status="failed",
            exit_code=1,
            error_message=(
                "custom node code requires the velites OS sandbox "
                "(sandbox-exec/bwrap via `velites sandbox wrap`) but no "
                "velites binary is on PATH; refusing to run unsandboxed"
            ),
            log_path=str(context.log_path),
        )

    context.job_dir.mkdir(parents=True, exist_ok=True)
    context.log_path.parent.mkdir(parents=True, exist_ok=True)
    result_path = context.job_dir / ".custom_node_result.pkl"
    # The exec'd child builds its own cancellation token; the parent's
    # multiprocessing token cannot cross an exec boundary and is dropped.
    runtime = executor._build_runtime(context, CancellationToken(threading.Event()))
    runtime.pop("cancellation", None)
    # The payload rides stdin: node_config may hold resolved secrets, and
    # those must never touch the (job-dir) filesystem (VAULT-SECRET-001).
    payload_bytes = pickle.dumps(
        {
            "code": context.node_code,
            "job": dict(context.job),
            "job_dir": str(context.job_dir),
            "runtime": runtime,
        }
    )

    read_roots = [str(executor._repo_root)]
    if executor._repo_root != _SERVER_REPO_ROOT:
        read_roots.append(str(_SERVER_REPO_ROOT))
    command = [velites, "sandbox", "wrap", "--cwd", str(context.job_dir)]
    for root in read_roots:
        # The child imports server.app.* helpers; repos stay read-only.
        command += ["--allow-read", root]
    if executor._capabilities[context.capability].sandbox_network:
        command.append("--allow-network")
    command += [
        "--",
        sys.executable,
        "-m",
        "server.app.executors._code_child",
        str(result_path),
    ]

    log_fd = os.open(str(context.log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    env = dict(os.environ)
    python_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{_SERVER_REPO_ROOT}{os.pathsep}{python_path}" if python_path else str(_SERVER_REPO_ROOT)
    )
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            cwd=str(context.job_dir),
            env=env,
        )
    finally:
        os.close(log_fd)
    assert process.stdin is not None
    process.stdin.write(payload_bytes)
    process.stdin.close()

    parent_token_obj = (
        context.runtime.get("cancellation") if isinstance(context.runtime, Mapping) else None
    )
    parent_token = parent_token_obj if isinstance(parent_token_obj, CancellationToken) else None
    deadline = time.monotonic() + timeout_seconds
    try:
        while process.poll() is None:
            if parent_token is not None and parent_token.wait(timeout=0.1):
                executor._terminate_child(process)
                return ExecutionResult(
                    status="cancelled",
                    exit_code=-1,
                    error_message="execution was cancelled",
                    log_path=str(context.log_path),
                )
            if time.monotonic() > deadline:
                executor._terminate_child(process)
                return ExecutionResult(
                    status="failed",
                    exit_code=1,
                    error_message=(
                        f"custom code node timed out after {timeout_seconds}s (sandboxed)"
                    ),
                    log_path=str(context.log_path),
                )
            if parent_token is None:
                time.sleep(0.05)

        try:
            with open(result_path, "rb") as handle:
                status, payload = pickle.load(handle)  # noqa: S301 - child-produced
        except (OSError, pickle.UnpicklingError, EOFError, ValueError):
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message="sandboxed custom code node did not return a result",
                log_path=str(context.log_path),
            )
        if status == "error":
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=payload,
                log_path=str(context.log_path),
            )
        return executor._check_outputs(context)
    finally:
        if process.poll() is None:
            executor._terminate_child(process)
        with contextlib.suppress(OSError):
            result_path.unlink(missing_ok=True)
