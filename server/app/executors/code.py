"""The code executor: run DB-published Python node code as DAG nodes.

All node code arrives as text on ``ExecutionContext.node_code`` — resolved at
dispatch from the workspace's published version, or from a frozen historical
version for quality replay (EXEC-CODE-002, #96: the legacy capability ``path``
binding to repo files is retired). The loader contract is a module-level
``run(job, job_dir, runtime)`` (or a ``def run(ctx)`` business function
decorated with the node SDK's ``@entrypoint``).

Because node code is DB-backed, it runs inside the velites OS sandbox
(EXEC-CODE-003, ``velites sandbox wrap``): read-only filesystem except
``job_dir``/tmp, network denied unless the node opts in (``sandbox_network``
via the resolved node config, P-0.5), and fail-closed — without a sandbox
backend the executor refuses to run code at all.

P-0.5: this is the only executor — the single implicit code pool
(CODE_EXECUTOR_ID), assembled directly by the app composition root; the
kind-registration machinery is gone.
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
from server.app.executors.artifact_mirror import (
    build_artifact_object_store,
    upload_produced_artifacts,
)
from server.app.executors.artifact_restore import restore_missing_inputs
from server.app.executors.models import (
    CODE_EXECUTOR_ID,
    ExecutionContext,
    ExecutionResult,
)
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.storage import build_s3_storage
from shared.material_cache import MATERIALS_CACHE_DIRNAME

logger = logging.getLogger(__name__)

# Platform fallback for contexts built without dispatch resolution (tests);
# dispatch always carries the resolved reserved-key values (P-0.5).
_DEFAULT_TIMEOUT_SECONDS = 600


def _failed(context: ExecutionContext, message: str) -> ExecutionResult:
    return ExecutionResult(
        status="failed", exit_code=1, error_message=message, log_path=str(context.log_path)
    )


class CodeExecutor:
    """Adapter that runs DB-published node code inside the velites sandbox."""

    kind = "code"
    id = CODE_EXECUTOR_ID

    def __init__(
        self,
        repo_root: Path,
        settings_config: Mapping[str, Any] | None = None,
        job_db: Any | None = None,
        cancellation_grace_seconds: float = 5,
        materials_cache_root: Path | None = None,
    ) -> None:
        self._repo_root = Path(repo_root).resolve()
        self.settings_config = dict(settings_config) if settings_config is not None else {}
        self.job_db = job_db
        self.cancellation_grace_seconds = cancellation_grace_seconds
        # Materialization cache (design §6.2): the composition root passes
        # ``settings.data_dir / materials_cache``; DB-less contexts (tests)
        # fall back under the repo's data dir. Statically allow-read in the
        # sandbox (MATERIAL-ACCESS-001).
        self._materials_cache_root = (
            Path(materials_cache_root)
            if materials_cache_root is not None
            else self._repo_root / "data" / MATERIALS_CACHE_DIRNAME
        )
        self._cancelled: set[str] = set()
        self._velites_probed = False
        self._velites_path: str | None = None
        self._storage_probed = False
        self._object_storage: Any | None = None
        self._artifact_objects: JobArtifactObjectStore | None = None

    def supports(self, capability: str) -> bool:
        # Single implicit code pool (P-0.5): the adapter runs any capability;
        # dispatch fails nodes without published node code earlier
        # (EXEC-CODE-002), so there is no capability allowlist left here.
        return True

    def _object_store(self) -> Any | None:
        """Instance object storage for materialization, probed lazily."""
        if not self._storage_probed:
            self._storage_probed = True
            self._object_storage = build_s3_storage()
        return self._object_storage

    def _artifact_object_store(self) -> JobArtifactObjectStore | None:
        """Artifact upload service (D12); None without storage or a DB handle."""
        if self._artifact_objects is None:
            # Settings/storage misconfiguration (e.g. a missing secret file
            # surfaced by load_s3_settings) must never fail the node
            # (EXEC-ARTIFACT-STORE-001): disable mirroring instead.
            # #204 broad-except audit: one-time lazy probe whose outcome is
            # cached for the executor's lifetime. The failure families are
            # deliberately not enumerated — env parsing, secret-file reads
            # and client construction each raise their own types, and any of
            # them means "artifact mirroring is unavailable on this host",
            # which is a degradation the node must survive. exc_info keeps
            # the configuration root cause visible for the operator.
            try:
                self._artifact_objects = build_artifact_object_store(
                    self._object_store(), getattr(self.job_db, "path", None)
                )
            except Exception:
                logger.warning("artifact store unavailable; mirroring disabled", exc_info=True)
        return self._artifact_objects

    def _upload_artifacts(self, context: ExecutionContext, produced: tuple[str, ...]) -> None:
        """Best-effort upload of produced artifacts (D12): a storage outage
        never fails the node — the local copy stays and the maintenance
        reconciler re-uploads later (EXEC-ARTIFACT-STORE-001)."""
        upload_produced_artifacts(
            self._artifact_object_store(),
            workspace_id=str(context.workspace_id),
            job_id=str(context.job_id),
            node_key=str(context.node_key),
            job_dir=context.job_dir,
            produced=produced,
        )

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        if context.execution_id in self._cancelled:
            self._cancelled.discard(context.execution_id)
            return ExecutionResult(
                status="cancelled",
                exit_code=-1,
                error_message="execution was cancelled before starting",
                log_path=str(context.log_path),
            )

        if context.node_code is None:
            # Dispatch resolves code text and fails the node earlier; this is
            # the defensive backstop (EXEC-CODE-002).
            return _failed(
                context,
                f"capability {context.capability!r} has no published node code (EXEC-CODE-002)",
            )
        # The timeout travels the node config chain (P-0.5): the resolved
        # node config wins, the platform default is the fallback for contexts
        # built without dispatch resolution (e.g. tests).
        timeout = context.node_config.get("timeout_seconds")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
            timeout = _DEFAULT_TIMEOUT_SECONDS
        if context.inputs:
            # The local job_dir is an evictable cache (EXEC-ARTIFACT-STORE-001):
            # a targeted rerun may find declared inputs reclaimed, so restore
            # them from object storage best-effort first. Failures never change
            # node semantics — the node errors on the missing input itself.
            # #204 broad-except audit: this wraps the restore loop's own
            # per-file containment for the one thing it deliberately lets
            # escape — the manifest lookup's DB read (restore_missing_inputs
            # already catches per-file storage errors itself). A transient DB
            # outage at restore time must degrade to "run with local files"
            # rather than fail a node whose inputs are all present locally;
            # the traceback is logged so the silent degradation is visible.
            try:
                restore_missing_inputs(
                    self._artifact_object_store(),
                    job_id=str(context.job_id),
                    job_dir=context.job_dir,
                    inputs=context.inputs,
                )
            except Exception:
                logger.warning(
                    "input restore failed for job %s; continuing with local files only",
                    context.job_id,
                    exc_info=True,
                )
        return execute_custom_sandboxed(self, context, timeout)

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
        self._upload_artifacts(context, produced)
        return ExecutionResult(
            status="completed",
            exit_code=0,
            log_path=str(context.log_path),
            produced_artifacts=produced,
        )
