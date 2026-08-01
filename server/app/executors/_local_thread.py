"""In-thread execution for local capabilities declared ``isolation: thread``.

Mirrors the ``_local_isolated.py`` layout (functions take the executor
instance as their first argument) but runs the handler directly in the
executor pool thread, skipping the multiprocessing spawn overhead. There is
no process isolation and no wall-clock kill (``timeout_seconds`` does not
apply), and stdout is not redirected into the node log file — handlers must
use ``logging``. Cancellation is cooperative only: ``cancel()`` sets the
token exposed as ``runtime["cancellation"]`` and the handler must observe it.
Only trusted, fast, pure-code handlers may opt in.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.app.executors.cancellation import CancellationToken
from server.app.executors.models import ExecutionContext, ExecutionResult

if TYPE_CHECKING:
    from server.app.executors._local_isolated import LocalHandler
    from server.app.executors.local import LocalExecutor

logger = logging.getLogger(__name__)


def execute_in_thread(
    executor: LocalExecutor, context: ExecutionContext, handler: LocalHandler
) -> ExecutionResult:
    """Run *handler* in the current (executor pool) thread."""
    context.job_dir.mkdir(parents=True, exist_ok=True)
    context.log_path.parent.mkdir(parents=True, exist_ok=True)

    token = CancellationToken()
    executor._tokens[context.execution_id] = token
    try:
        runtime = executor._build_runtime(context, token)
        # Same process: hand the handler the live JobQueries instead of the
        # paths an isolated child would use to rebuild one.
        runtime.pop("_job_db_path", None)
        runtime.pop("_jobs_dir", None)
        if executor.job_db is not None:
            runtime["job_db"] = executor.job_db
        try:
            handler(dict(context.job), context.job_dir, runtime)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            logger.exception("Thread local handler for %s failed", context.capability)
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=error_message,
                log_path=str(context.log_path),
            )
        if token.is_cancelled():
            return ExecutionResult(
                status="cancelled",
                exit_code=-1,
                error_message="execution was cancelled",
                log_path=str(context.log_path),
            )
        return executor._check_outputs(context)
    finally:
        executor._tokens.pop(context.execution_id, None)
