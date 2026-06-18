from __future__ import annotations

import contextlib
import importlib
import logging
import multiprocessing
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from server.app.executors.cancellation import CancellationToken
from server.app.executors.models import ExecutionContext, ExecutionResult

logger = logging.getLogger(__name__)

LocalHandler = Callable[[dict[str, Any], Path, dict[str, Any] | None], None]


def _handler_key(handler: LocalHandler) -> str | None:
    """Return an importable path for *handler* when it can be resolved in a child.

    Lambdas and other non-serializable callables return ``None`` so executor
    construction can reject handlers that would bypass process isolation.
    """
    qualname = getattr(handler, "__qualname__", "")
    module = getattr(handler, "__module__", "")
    if not module or not qualname or "<lambda>" in qualname or "<locals>" in qualname:
        return None
    return f"{module}.{qualname}"


def _resolve_handler(handler_key: str) -> LocalHandler:
    """Resolve a handler by its importable path.

    Repository handlers are registered as ``module.function`` under
    ``server.app.workflows``; tests and other callers may use a fully-qualified
    module path.
    """
    if "." not in handler_key:
        module_path = "__mp_main__"
        func_name = handler_key
    else:
        module_path, func_name = handler_key.rsplit(".", 1)
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        module = importlib.import_module(f"server.app.workflows.{module_path}")
    handler = getattr(module, func_name)
    if not callable(handler):
        raise ValueError(f"Handler {handler_key!r} is not callable")
    return cast(LocalHandler, handler)


def _run_handler(
    handler_key: str,
    job: dict[str, Any],
    job_dir_str: str,
    runtime: dict[str, Any],
    conn: Any,
) -> None:
    """Target run in an isolated multiprocessing child."""
    error_message = ""
    try:
        handler = _resolve_handler(handler_key)
        job_db_path = runtime.pop("_job_db_path", None)
        jobs_dir = runtime.pop("_jobs_dir", None)
        if job_db_path and jobs_dir:
            from server.app.jobs import JobQueries

            runtime["job_db"] = JobQueries(Path(job_db_path), Path(jobs_dir))
        job_dir = Path(job_dir_str)
        handler(job, job_dir, runtime)
        conn.send(("ok", None))
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        logger.exception("Isolated handler %s failed", handler_key)
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


class LocalExecutor:
    """Adapter that runs repository-owned local handlers inside the workspace runtime."""

    kind = "local"

    def __init__(
        self,
        id: str,
        handlers: Mapping[str, LocalHandler],
        settings_config: Mapping[str, Any] | None = None,
        job_db: Any | None = None,
        cancellation_grace_seconds: float = 5,
    ) -> None:
        self.id = id
        self.handlers = dict(handlers)
        self._handler_keys: dict[str, str] = {}
        unsafe_capabilities: list[str] = []
        for capability, handler in self.handlers.items():
            key = _handler_key(handler)
            if key is None:
                unsafe_capabilities.append(capability)
                continue
            try:
                resolved = _resolve_handler(key)
            except (AttributeError, ImportError, ValueError):
                unsafe_capabilities.append(capability)
                continue
            if resolved is not handler:
                unsafe_capabilities.append(capability)
                continue
            self._handler_keys[capability] = key
        if unsafe_capabilities:
            raise ValueError(
                "Local executor handlers must be importable module-level functions: "
                + ", ".join(sorted(unsafe_capabilities))
            )
        self.settings_config = dict(settings_config) if settings_config is not None else {}
        self.job_db = job_db
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self._cancelled: set[str] = set()
        self._tokens: dict[str, CancellationToken] = {}
        self._watchers: dict[str, threading.Thread] = {}

    def supports(self, capability: str) -> bool:
        return capability in self.handlers

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        if context.execution_id in self._cancelled:
            self._cancelled.discard(context.execution_id)
            return ExecutionResult(
                status="cancelled",
                exit_code=-1,
                error_message="execution was cancelled before starting",
                log_path=str(context.log_path),
            )

        handler = self.handlers.get(context.capability)
        if handler is None:
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=f"capability {context.capability!r} is not supported",
                log_path=str(context.log_path),
            )

        key = self._handler_keys.get(context.capability)
        if key is None:  # guarded by constructor validation
            raise RuntimeError(f"Local handler for {context.capability!r} is not importable")
        return self._execute_isolated(context, key)

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
            "cancellation": token,
        }
        if self.job_db is not None:
            runtime["_job_db_path"] = str(getattr(self.job_db, "path", ""))
            runtime["_jobs_dir"] = str(getattr(self.job_db, "jobs_dir", ""))
        return runtime

    def _execute_isolated(self, context: ExecutionContext, handler_key: str) -> ExecutionResult:
        context.job_dir.mkdir(parents=True, exist_ok=True)
        context.log_path.parent.mkdir(parents=True, exist_ok=True)

        parent_conn, child_conn = multiprocessing.Pipe()
        child_token = CancellationToken(multiprocessing.Event())
        self._tokens[context.execution_id] = child_token

        runtime = self._build_runtime(context, child_token)
        process = multiprocessing.Process(
            target=_run_handler,
            args=(
                handler_key,
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

        cancelled = False
        try:
            while process.is_alive():
                if child_token.is_cancelled():
                    cancelled = True
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
                    error_message="isolated handler did not return a result",
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
            error_message = f"Missing outputs after local run: {', '.join(missing)}"
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
