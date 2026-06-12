from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from server.app.executors.models import ExecutionContext, ExecutionResult

logger = logging.getLogger(__name__)

LocalHandler = Callable[[dict[str, Any], Path, dict[str, Any] | None], None]


class LocalExecutor:
    """Adapter that runs repository-owned local handlers inside the workspace runtime."""

    kind = "local"

    def __init__(
        self,
        id: str,
        handlers: Mapping[str, LocalHandler],
        settings_config: Mapping[str, Any] | None = None,
        job_db: Any | None = None,
    ) -> None:
        self.id = id
        self.handlers = dict(handlers)
        self.settings_config = dict(settings_config) if settings_config is not None else {}
        self.job_db = job_db
        self._cancelled: set[str] = set()

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

        runtime: dict[str, Any] = {
            "job_dir": context.job_dir,
            "log_path": context.log_path,
            "inputs": context.inputs,
            "expected_outputs": context.expected_outputs,
            "capability": context.capability,
            "node_key": context.node_key,
            "pipeline_key": context.pipeline_key,
            "execution_id": context.execution_id,
            "workspace_id": context.workspace_id,
            "workspace": context.workspace,
            "job": context.job,
            "job_db": self.job_db,
            "settings_config": self.settings_config,
        }

        context.job_dir.mkdir(parents=True, exist_ok=True)
        context.log_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            handler(dict(context.job), context.job_dir, runtime)
        except Exception as exc:
            error_message = str(exc)
            logger.exception("Local handler failed for execution %s", context.execution_id)
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=error_message,
                log_path=str(context.log_path),
            )

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

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)
