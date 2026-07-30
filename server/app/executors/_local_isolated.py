"""Isolated child-process execution for the local executor.

Split out of ``local.py`` so the executor module only carries capability
resolution and the adapter surface; mirrors the ``executors/_lease_*.py``
layout. Functions take the executor instance as their first argument.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from server.app.executors.cancellation import CancellationToken
from server.app.executors.models import ExecutionContext, ExecutionResult

if TYPE_CHECKING:
    from server.app.executors.local import LocalExecutor

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

    prefix = f"[local:{runtime.get('node_key', '')}]"
    logger.info("%s start capability=%s", prefix, runtime.get("capability", ""))

    error_message = ""
    try:
        handler = _resolve_handler(handler_key)
        job_db_path = runtime.pop("_job_db_path", None)
        jobs_dir = runtime.pop("_jobs_dir", None)
        if job_db_path and jobs_dir:
            from server.app.jobs import JobQueries

            runtime["job_db"] = JobQueries(str(job_db_path), Path(jobs_dir))
        job_dir = Path(job_dir_str)
        handler(job, job_dir, runtime)
        logger.info("%s completed", prefix)
        conn.send(("ok", None))
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        logger.error("%s failed: %s", prefix, error_message)
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


def execute_isolated(
    executor: LocalExecutor, context: ExecutionContext, handler_key: str
) -> ExecutionResult:
    """Run the handler child under cancellation AND a wall-clock deadline.

    The deadline is what keeps a hung handler from becoming an orphaned,
    CPU-spinning zombie: once it expires the child is terminated and the run
    reported failed ("timed out", matching the timeout failure class)."""
    context.job_dir.mkdir(parents=True, exist_ok=True)
    context.log_path.parent.mkdir(parents=True, exist_ok=True)

    parent_conn, child_conn = multiprocessing.Pipe()
    child_token = CancellationToken(multiprocessing.Event())
    executor._tokens[context.execution_id] = child_token

    runtime = executor._build_runtime(context, child_token)
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
        executor._watchers[context.execution_id] = watcher

    timeout = executor._capability_timeouts.get(
        context.capability, executor._default_timeout_seconds
    )
    deadline = time.monotonic() + timeout if timeout else None

    cancelled = False
    timed_out = False
    try:
        while process.is_alive():
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                executor._terminate_child(process)
                break
            if child_token.is_cancelled():
                cancelled = True
                executor._terminate_child(process)
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
                error_message=f"Local handler timed out after {timeout:g}s",
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

        return executor._check_outputs(context)
    finally:
        if process.is_alive():
            executor._terminate_child(process)
        if watcher is not None:
            child_token.cancel()
            watcher.join(timeout=0.5)
        executor._tokens.pop(context.execution_id, None)
        executor._watchers.pop(context.execution_id, None)
