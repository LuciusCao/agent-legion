"""Code executor kind: run DB-published Python node code as DAG nodes.

All node code arrives as text on ``ExecutionContext.node_code`` — resolved at
dispatch from the frozen job version, the workspace's published version, or
the global factory seed (EXEC-CODE-002, #96: the legacy capability ``path``
binding to repo files is retired). The loader contract is a module-level
``run(job, job_dir, runtime)`` (or a ``def run(ctx)`` business function
decorated with the node SDK's ``@entrypoint``).

Because node code is DB-backed, it runs inside the velites OS sandbox
(EXEC-CODE-003, ``velites sandbox wrap``): read-only filesystem except
``job_dir``/tmp, network denied unless the capability opts in
(``sandbox_network``), and fail-closed — without a sandbox backend the
executor refuses to run code at all.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from server.app.executors._code_sandbox import execute_custom_sandboxed
from server.app.executors.config import CodeCapabilityConfig, CodeExecutorConfig
from server.app.executors.kinds import ExecutorKind, RuntimeDependencies, register_kind
from server.app.executors.models import ExecutionContext, ExecutionResult

logger = logging.getLogger(__name__)


def _failed(context: ExecutionContext, message: str) -> ExecutionResult:
    return ExecutionResult(
        status="failed", exit_code=1, error_message=message, log_path=str(context.log_path)
    )


class CodeExecutor:
    """Adapter that runs DB-published node code inside the velites sandbox."""

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
        self._capabilities = dict(capabilities)
        self.settings_config = dict(settings_config) if settings_config is not None else {}
        self.job_db = job_db
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self._cancelled: set[str] = set()
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

        if context.node_code is None:
            # Dispatch resolves code text and fails the node earlier; this is
            # the defensive backstop (EXEC-CODE-002).
            return _failed(
                context,
                f"capability {context.capability!r} has no published node code (EXEC-CODE-002)",
            )
        return execute_custom_sandboxed(self, context, cap_config.timeout_seconds)

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)

    def _terminate_child(self, process: subprocess.Popen[bytes]) -> None:
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
