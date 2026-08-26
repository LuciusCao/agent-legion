"""Sandboxed custom code execution for the code executor (EXEC-CODE-003).

Split from ``code.py`` to keep it within its size budget; mirrors the
``_local_thread.py`` layout (functions take the executor instance and reach
its internals, same as the builtin isolated child helpers).

Fail-closed: without the velites wrapper (and thus without
sandbox-exec/bwrap) custom code never runs unsandboxed. The child is an
exec'd command line — the sandbox can only confine exec'd processes, not
multiprocessing forks — so job/runtime ride a payload pickle on **stdin**
(parent-produced, so unpickling it is safe) and the result comes back as
strictly validated **JSON** from a file inside the sandbox-writable
``job_dir`` (never pickle: the child tree could have replaced that file).
"""

from __future__ import annotations

import contextlib
import os
import pickle
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from server.app.executors._code_runtime import (
    build_runtime,
    clear_auth_failure_marker,
    consume_auth_failure_marker,
)
from server.app.executors.cancellation import CancellationToken
from server.app.executors.models import ExecutionContext, ExecutionResult
from shared.code_sandbox import (
    build_sandbox_argv,
    child_env,
    read_result_error,
    read_roots,
)
from shared.material_cache import MaterializeError

if TYPE_CHECKING:
    from server.app.executors.code import CodeExecutor

# Real repository root hosting the ``workspace_libs`` package (the sandboxed
# child imports ``workspace_libs.*`` from here); differs from the executor's
# configured ``repo_root`` only in tests, where capabilities point into tmp
# dirs.
_SERVER_REPO_ROOT = Path(__file__).resolve().parents[3]

# Repo subdirectories the child must READ: import roots (server helpers,
# yaml-independent config modules, workspace_libs) plus the demo workflow's
# git-reviewed seed sources (workflow_nodes/). Node code never reads
# examples/ — the demo intake node consumes its knowledge markdown as a
# material through the allow-read materialization cache (design §9,
# MATERIAL-ACCESS-001). Deliberately excludes the repo root itself,
# `.env`, `deploy/` and `data/` (secrets and runtime data). The `config`
# entry relies on CONFIG-YAML-001: tracked config yaml must never carry
# secret values (they live in the vault / instance settings), so granting the
# sandbox read access to `config/` exposes no credentials.
_REPO_READ_SUBDIRS = ("server", "workflow_nodes", "config", "workspace_libs")

_RESULT_BASENAME = ".custom_node_result.json"


def _velites_binary(executor: CodeExecutor) -> str | None:
    """PATH probe for the velites sandbox wrapper, cached per executor."""
    if not executor._velites_probed:
        executor._velites_probed = True
        executor._velites_path = shutil.which("velites")
    return executor._velites_path


def _import_roots(executor: CodeExecutor) -> list[Path]:
    """Read roots for the Host-side sandbox: repo subdirs plus the cache."""
    roots: list[Path] = []
    for repo_root in {executor._repo_root, _SERVER_REPO_ROOT}:
        for subdir in _REPO_READ_SUBDIRS:
            candidate = repo_root / subdir
            if candidate.is_dir():
                roots.append(candidate)
    return roots


def _read_roots(executor: CodeExecutor) -> list[str]:
    """Host adapter over the shared read-roots allowlist.

    Keeps the historical executor-based signature (tests and diagnostics
    probe the sandbox surface through it); the materials cache root
    (MATERIAL-ACCESS-001) is appended by the shared implementation.
    """
    return read_roots(_import_roots(executor), executor._materials_cache_root)


def _read_result(result_path: Path, log_path: Path) -> ExecutionResult | None:
    """Wrap the shared strict result check into an ExecutionResult."""
    error = read_result_error(result_path)
    if error is None:
        return None
    return ExecutionResult(
        status="failed",
        exit_code=1,
        error_message=error,
        log_path=str(log_path),
    )


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
    result_path = context.job_dir / _RESULT_BASENAME
    # A leftover result from a previous attempt must never fake a success.
    result_path.unlink(missing_ok=True)
    # Same for a stale auth-failure marker (the parent consumes it post-run).
    clear_auth_failure_marker(context)
    # The exec'd child builds its own cancellation token; the parent's
    # multiprocessing token cannot cross an exec boundary and is dropped.
    # DB-derived inputs (job_batch, skill_versions) are prefetched inside
    # build_runtime: children never get a database handle (EXEC-CODE-003).
    # A material input is materialized there too — a failure is a clean
    # node-facing error, not a crashed dispatch.
    try:
        runtime = build_runtime(executor, context, CancellationToken(threading.Event()))
    except MaterializeError as exc:
        return ExecutionResult(
            status="failed",
            exit_code=1,
            error_message=str(exc),
            log_path=str(context.log_path),
        )
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

    command = build_sandbox_argv(
        velites,
        context.job_dir,
        _import_roots(executor),
        result_path,
        sandbox_network=context.node_config.get("sandbox_network"),
        materials_cache_root=executor._materials_cache_root,
    )
    # Network opt-in travels the node config chain (P-0.5): the resolved
    # node config wins; anything else denies (the sandbox default).

    log_fd = os.open(str(context.log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            cwd=str(context.job_dir),
            env=child_env(_SERVER_REPO_ROOT),
            # velites does not forward signals: terminate the whole group so
            # sandbox-exec grandchildren are not orphaned.
            start_new_session=True,
        )
    finally:
        os.close(log_fd)

    # Feed the payload from a daemon thread so a slow-starting child cannot
    # stall the deadline/cancellation loop on a full pipe buffer.
    write_error: list[BaseException] = []

    def _feed_stdin() -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(payload_bytes)
            process.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            write_error.append(exc)

    feeder = threading.Thread(target=_feed_stdin, daemon=True)
    feeder.start()

    parent_token_obj = (
        context.runtime.get("cancellation") if isinstance(context.runtime, Mapping) else None
    )
    parent_token = parent_token_obj if isinstance(parent_token_obj, CancellationToken) else None
    deadline = time.monotonic() + timeout_seconds
    try:
        while process.poll() is None:
            if write_error:
                executor._terminate_child(process)
                return ExecutionResult(
                    status="failed",
                    exit_code=1,
                    error_message=(
                        "custom code child exited before reading its payload "
                        f"({type(write_error[0]).__name__})"
                    ),
                    log_path=str(context.log_path),
                )
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

        failure = _read_result(result_path, context.log_path)
        if failure is not None:
            return failure
        return executor._check_outputs(context)
    finally:
        if process.poll() is None:
            executor._terminate_child(process)
        consume_auth_failure_marker(executor, context)
        feeder.join(timeout=1)
        with contextlib.suppress(OSError):
            result_path.unlink(missing_ok=True)
