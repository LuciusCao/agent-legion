"""Code executor kind: run repo-tracked Python files as DAG nodes.

Each capability binds to a repository-relative ``path`` pointing at a tracked
Python file that exposes a module-level ``run(job, job_dir, runtime)`` with the
same contract as local handlers. Execution uses the same isolated
multiprocessing child pattern as the local executor, plus a per-capability
timeout. Paths must stay inside the repository root (EXEC-CODE-001) so node
code is always git-reviewed and CI-gated.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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


def _run_code_node(
    file_path: str,
    repo_root: str,
    job: dict[str, Any],
    job_dir_str: str,
    runtime: dict[str, Any],
    conn: Any,
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
    logger.info("%s start capability=%s path=%s", prefix, runtime.get("capability", ""), file_path)

    try:
        run = _load_run_callable(file_path, repo_root)
        job_db_path = runtime.pop("_job_db_path", None)
        jobs_dir = runtime.pop("_jobs_dir", None)
        if job_db_path and jobs_dir:
            from server.app.jobs import JobQueries

            runtime["job_db"] = JobQueries(str(job_db_path), Path(jobs_dir))
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

    def supports(self, capability: str) -> bool:
        return capability in self._paths

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        if context.execution_id in self._cancelled:
            self._cancelled.discard(context.execution_id)
            return ExecutionResult(
                status="cancelled",
                exit_code=-1,
                error_message="execution was cancelled before starting",
                log_path=str(context.log_path),
            )

        path = self._paths.get(context.capability)
        if path is None:
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=f"capability {context.capability!r} is not supported",
                log_path=str(context.log_path),
            )

        timeout = self._capabilities[context.capability].timeout_seconds
        return self._execute_isolated(context, path, timeout)

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)
        token = self._tokens.get(execution_id)
        if token is not None:
            token.cancel()

    def _build_runtime(self, context: ExecutionContext, token: CancellationToken) -> dict[str, Any]:
        runtime: dict[str, Any] = {
            "job_dir": context.job_dir,
            "log_path": context.log_path,
            "inputs": context.inputs,
            "expected_outputs": context.expected_outputs,
            "capability": context.capability,
            "node_key": context.node_key,
            "workflow_key": context.workflow_key,
            "execution_id": context.execution_id,
            "workspace_id": context.workspace_id,
            "workspace": dict(context.workspace),
            "job": dict(context.job),
            "settings_config": self.settings_config,
            "node_config": dict(context.node_config),
            "cancellation": token,
        }
        if self.job_db is not None:
            runtime["_job_db_path"] = str(getattr(self.job_db, "path", ""))
            runtime["_jobs_dir"] = str(getattr(self.job_db, "jobs_dir", ""))
        return runtime

    def _execute_isolated(
        self, context: ExecutionContext, path: Path, timeout_seconds: int
    ) -> ExecutionResult:
        context.job_dir.mkdir(parents=True, exist_ok=True)
        context.log_path.parent.mkdir(parents=True, exist_ok=True)

        parent_conn, child_conn = multiprocessing.Pipe()
        child_token = CancellationToken(multiprocessing.Event())
        self._tokens[context.execution_id] = child_token

        runtime = self._build_runtime(context, child_token)
        process = multiprocessing.Process(
            target=_run_code_node,
            args=(
                str(path),
                str(self._repo_root),
                dict(context.job),
                str(context.job_dir),
                runtime,
                child_conn,
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
            if watcher is not None:
                child_token.cancel()
                watcher.join(timeout=0.5)
            self._tokens.pop(context.execution_id, None)
            self._watchers.pop(context.execution_id, None)

    def _terminate_child(self, process: multiprocessing.process.BaseProcess) -> None:
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
