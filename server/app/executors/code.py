"""Code executor kind: run repo-tracked Python files as DAG nodes.

A capability may bind a repository-relative ``path`` pointing at a tracked
Python file that exposes a module-level ``run(job, job_dir, runtime)`` with the
same contract as local handlers. Execution uses the same isolated
multiprocessing child pattern as the local executor, plus a per-capability
timeout. Declared paths must stay inside the repository root (EXEC-CODE-001)
so node code is always git-reviewed and CI-gated. A capability without a
``path`` is custom-code only: it has no builtin file and dispatches purely
from DB-backed custom code.

Custom node code (EXEC-CODE-002) arrives as text on ``ExecutionContext.
node_code`` — resolved at dispatch from the frozen job version or the
published DB version — and is loaded from the string instead of the file; the
``run`` contract is identical. Because custom code is user-supplied, it runs
inside the velites OS sandbox (EXEC-CODE-003, ``velites sandbox wrap``):
read-only filesystem except ``job_dir``/tmp, network denied unless the
capability opts in (``sandbox_network``), and fail-closed — without a
sandbox backend the executor refuses to run custom code at all.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from server.app.executors._code_runtime import (
    build_runtime,
    clear_auth_failure_marker,
    consume_auth_failure_marker,
)
from server.app.executors._code_sandbox import execute_custom_sandboxed
from server.app.executors.cancellation import CancellationToken
from server.app.executors.config import CodeCapabilityConfig, CodeExecutorConfig
from server.app.executors.kinds import ExecutorKind, RuntimeDependencies, register_kind
from server.app.executors.models import ExecutionContext, ExecutionResult

logger = logging.getLogger(__name__)


def _load_run_callable(file_path: str, repo_root: str):
    """Import a code file by path and return its module-level ``run`` callable."""
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    path = Path(file_path)
    module_name = f"_code_node_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load code node module from {file_path!r}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = getattr(module, "run", None)
    if not callable(run):
        raise ValueError(f"Code node {file_path!r} does not expose a callable 'run'")
    return run


def _load_run_from_source(source: str):
    """Build a module from custom code text and return its ``run`` callable."""
    spec = importlib.util.spec_from_loader("_code_node_custom", loader=None)
    if spec is None:
        raise ValueError("Cannot build module spec for custom node code")
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, "<custom_node>", "exec"), module.__dict__)
    run = getattr(module, "run", None)
    if not callable(run):
        raise ValueError("Custom node code does not expose a callable 'run'")
    return run


def _failed(context: ExecutionContext, message: str) -> ExecutionResult:
    return ExecutionResult(
        status="failed", exit_code=1, error_message=message, log_path=str(context.log_path)
    )


def _run_code_node(
    file_path: str,
    repo_root: str,
    job: dict[str, Any],
    job_dir_str: str,
    runtime: dict[str, Any],
    conn: Any,
    code_source: str | None = None,
) -> None:
    """Target run in an isolated multiprocessing child."""
    log_path = runtime.get("log_path")
    if log_path:
        try:
            log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            os.dup2(log_fd, 1)
            os.dup2(log_fd, 2)
            os.close(log_fd)
            getattr(sys.stdout, "reconfigure", lambda **_: None)(line_buffering=True)
            getattr(sys.stderr, "reconfigure", lambda **_: None)(line_buffering=True)
            logging.basicConfig(
                level=logging.INFO, format="%(message)s", stream=sys.stdout, force=True
            )
        except Exception:
            logger.exception("Failed to redirect run log to %s", log_path)

    prefix = f"[code:{runtime.get('node_key', '')}]"
    origin = "custom" if code_source is not None else f"path={file_path}"
    logger.info("%s start capability=%s %s", prefix, runtime.get("capability", ""), origin)

    try:
        run = (
            _load_run_from_source(code_source)
            if code_source is not None
            else _load_run_callable(file_path, repo_root)
        )
        job_dir = Path(job_dir_str)
        run(job, job_dir, runtime)
        logger.info("%s completed", prefix)
        conn.send(("ok", None))
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        logger.error("%s failed: %s", prefix, error_message)
        logger.exception("Isolated code node %s failed", file_path)
        with contextlib.suppress(Exception):
            conn.send(("error", error_message))
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _watch_parent_token(parent_token: CancellationToken, child_token: CancellationToken) -> None:
    """Propagate cancellation from the runtime token to the child token."""
    while not child_token.is_cancelled():
        if parent_token.wait(timeout=0.1):
            child_token.cancel()
            break


class CodeExecutor:
    """Adapter that runs repo-tracked code files inside the workspace runtime."""

    kind = "code"

    def __init__(
        self,
        id: str,
        capabilities: Mapping[str, CodeCapabilityConfig],
        repo_root: Path,
        settings_config: Mapping[str, Any] | None = None,
        job_db: Any | None = None,
        cancellation_grace_seconds: float = 5,
    ) -> None:
        self.id = id
        self._repo_root = Path(repo_root).resolve()
        self._paths: dict[str, Path] = {}
        invalid: list[str] = []
        for capability, cap_config in capabilities.items():
            if cap_config.path is None:
                # Custom-code-only capability (EXEC-CODE-002): no repo file to
                # resolve; dispatch requires a published custom code version.
                continue
            resolved = (self._repo_root / cap_config.path).resolve()
            if not resolved.is_relative_to(self._repo_root) or not resolved.is_file():
                invalid.append(f"{capability} ({cap_config.path})")
                continue
            self._paths[capability] = resolved
        if invalid:
            raise ValueError(
                "Code executor paths must resolve to tracked files inside the "
                "repository root: " + ", ".join(sorted(invalid))
            )
        self._capabilities = dict(capabilities)
        self.settings_config = dict(settings_config) if settings_config is not None else {}
        self.job_db = job_db
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self._cancelled: set[str] = set()
        self._tokens: dict[str, CancellationToken] = {}
        self._watchers: dict[str, threading.Thread] = {}
        self._velites_probed = False
        self._velites_path: str | None = None

    def supports(self, capability: str) -> bool:
        return capability in self._capabilities

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        if context.execution_id in self._cancelled:
            self._cancelled.discard(context.execution_id)
            return ExecutionResult(
                status="cancelled",
                exit_code=-1,
                error_message="execution was cancelled before starting",
                log_path=str(context.log_path),
            )

        cap_config = self._capabilities.get(context.capability)
        if cap_config is None:
            return _failed(context, f"capability {context.capability!r} is not supported")

        timeout = cap_config.timeout_seconds
        if context.node_code is not None:
            return execute_custom_sandboxed(self, context, timeout)
        path = self._paths.get(context.capability)
        if path is None:
            return _failed(
                context,
                f"capability {context.capability!r} has no builtin code path "
                "and no custom node code (EXEC-CODE-002)",
            )
        return self._execute_isolated(context, path, timeout)

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)
        token = self._tokens.get(execution_id)
        if token is not None:
            token.cancel()

    def _execute_isolated(
        self, context: ExecutionContext, path: Path, timeout_seconds: int
    ) -> ExecutionResult:
        context.job_dir.mkdir(parents=True, exist_ok=True)
        context.log_path.parent.mkdir(parents=True, exist_ok=True)
        clear_auth_failure_marker(context)

        parent_conn, child_conn = multiprocessing.Pipe()
        child_token = CancellationToken(multiprocessing.Event())
        self._tokens[context.execution_id] = child_token

        runtime = build_runtime(self, context, child_token)
        process = multiprocessing.Process(
            target=_run_code_node,
            args=(
                str(path),
                str(self._repo_root),
                dict(context.job),
                str(context.job_dir),
                runtime,
                child_conn,
                context.node_code,
            ),
        )
        process.start()
        child_conn.close()

        parent_token = (
            context.runtime.get("cancellation") if isinstance(context.runtime, Mapping) else None
        )
        watcher: threading.Thread | None = None
        if parent_token is not None:
            watcher = threading.Thread(
                target=_watch_parent_token,
                args=(parent_token, child_token),
                daemon=True,
            )
            watcher.start()
            self._watchers[context.execution_id] = watcher

        deadline = time.monotonic() + timeout_seconds
        cancelled = False
        timed_out = False
        try:
            while process.is_alive():
                if child_token.is_cancelled():
                    cancelled = True
                    self._terminate_child(process)
                    break
                if time.monotonic() > deadline:
                    timed_out = True
                    self._terminate_child(process)
                    break
                if parent_conn.poll(0.05):
                    break
                if not process.is_alive():
                    break

            if cancelled:
                return ExecutionResult(
                    status="cancelled",
                    exit_code=-1,
                    error_message="execution was cancelled",
                    log_path=str(context.log_path),
                )

            if timed_out:
                return ExecutionResult(
                    status="failed",
                    exit_code=1,
                    error_message=(
                        f"code node timed out after {timeout_seconds}s "
                        f"({path.relative_to(self._repo_root)})"
                    ),
                    log_path=str(context.log_path),
                )

            result: tuple[str, str] | None = None
            try:
                if parent_conn.poll(0.5):
                    result = parent_conn.recv()
            except EOFError:
                result = None

            if result is None:
                return ExecutionResult(
                    status="failed",
                    exit_code=1,
                    error_message="isolated code node did not return a result",
                    log_path=str(context.log_path),
                )

            status, payload = result
            if status == "error":
                return ExecutionResult(
                    status="failed",
                    exit_code=1,
                    error_message=payload,
                    log_path=str(context.log_path),
                )

            return self._check_outputs(context)
        finally:
            if process.is_alive():
                self._terminate_child(process)
            consume_auth_failure_marker(self, context)
            if watcher is not None:
                child_token.cancel()
                watcher.join(timeout=0.5)
            self._tokens.pop(context.execution_id, None)
            self._watchers.pop(context.execution_id, None)

    def _terminate_child(
        self, process: multiprocessing.process.BaseProcess | subprocess.Popen[bytes]
    ) -> None:
        if isinstance(process, subprocess.Popen):
            # The sandboxed child is exec'd with start_new_session=True and
            # velites does not forward signals: signal the whole process
            # group so sandbox-exec grandchildren are not orphaned.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=self.cancellation_grace_seconds)
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=2)
            return
        process.terminate()
        process.join(timeout=self.cancellation_grace_seconds)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)

    def _check_outputs(self, context: ExecutionContext) -> ExecutionResult:
        missing = [
            name for name in context.expected_outputs if not (context.job_dir / name).is_file()
        ]
        if missing:
            error_message = f"Missing outputs after code run: {', '.join(missing)}"
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=error_message,
                log_path=str(context.log_path),
            )

        produced = tuple(
            name for name in context.expected_outputs if (context.job_dir / name).is_file()
        )
        return ExecutionResult(
            status="completed",
            exit_code=0,
            log_path=str(context.log_path),
            produced_artifacts=produced,
        )


def build_code_executor(
    executor_id: str, config: CodeExecutorConfig, deps: RuntimeDependencies
) -> CodeExecutor:
    return CodeExecutor(
        id=executor_id,
        capabilities=config.capabilities,
        repo_root=deps.repo_root,
        settings_config=deps.settings_config,
        job_db=deps.job_db,
        cancellation_grace_seconds=deps.cancellation_grace_seconds,
    )


register_kind(
    ExecutorKind(name="code", config_model=CodeExecutorConfig, factory=build_code_executor)
)
