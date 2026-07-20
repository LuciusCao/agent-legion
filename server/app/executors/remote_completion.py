"""Lease completion for submit-only remote executions.

``RemoteExecutor.execute`` returns as soon as the execution is enqueued, so
nothing in the workflow worker waits for the result. When the execution
reaches a terminal state — a worker result report, a cancel, or a
requeue-limit failure — the broker invokes the registered completion
callbacks with the stored outcome. This handler is that callback: it
rebuilds the run context from the persisted broker row, unpacks the result
archive, translates the outcome into an ``ExecutionResult``, and finishes
the lease. All of it is idempotent: duplicate reports are deduplicated by
the broker state machine, and an already-finished lease makes this a no-op.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult
from server.app.executors.remote_broker import (
    RemoteExecutionBroker,
    RemoteExecutionPayload,
    RemoteOutcome,
)
from server.app.executors.remote_bundle import extract_result_archive
from server.app.skills.manager import SkillManager
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.pi_protocol import detect_model_error

logger = logging.getLogger(__name__)


class RemoteCompletionHandler:
    """Drive lease finish from broker completion events (result/cancel/requeue-limit)."""

    def __init__(
        self,
        broker: RemoteExecutionBroker,
        leases: ExecutorLeaseRepository,
        jobs_dir: Path,
        skill_manager: SkillManager | None = None,
        scan_error: Callable[[Path], str | None] | None = None,
    ) -> None:
        self._broker = broker
        self._leases = leases
        self._jobs_dir = jobs_dir
        # Reserved for future skill-side bookkeeping; the completion path does
        # not need skill resolution today, so the composition layer omits it.
        self._skill_manager = skill_manager
        self._scan_error = scan_error or detect_model_error

    def handle_completion(self, execution_id: str, outcome: RemoteOutcome) -> None:
        payload = self._broker.payload_for(execution_id)
        if payload is None:
            logger.warning("remote completion %s has no stored payload; ignoring", execution_id)
            return
        result = self._to_result(execution_id, payload, outcome)
        if not self._leases.finish(payload.lease_id, result):
            # Duplicate report or a cancel race lost: the lease is already
            # finished, so this completion changes nothing.
            logger.info(
                "lease %s already finished; ignoring duplicate completion for %s",
                payload.lease_id,
                execution_id,
            )
            return
        self._cleanup_bundles(payload, outcome)

    def _to_result(
        self, execution_id: str, payload: RemoteExecutionPayload, outcome: RemoteOutcome
    ) -> ExecutionResult:
        job_db = self._leases.job_db
        job = job_db.get_job(payload.job_id) if job_db is not None else None
        if job is None:
            logger.warning(
                "job %s for remote execution %s not found; finishing as failed",
                payload.job_id,
                execution_id,
            )
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=f"job record {payload.job_id!r} missing for remote completion",
            )
        job_dir = resolve_job_dir(job, self._jobs_dir)
        manifest = payload.manifest
        run_token = str(manifest.get("run_token", ""))
        skill_version = str(manifest.get("skill_version", ""))
        run_dir = job_dir / "runs" / payload.node_key / run_token
        session_dir = run_dir / "session"
        if outcome.status != "cancelled" and outcome.result_archive_name:
            try:
                extract_result_archive(
                    self._broker.bundle_dir / outcome.result_archive_name, job_dir
                )
            except Exception as exc:
                logger.exception("failed to unpack remote result for %s", execution_id)
                return ExecutionResult(
                    status="failed",
                    exit_code=1,
                    error_message=f"failed to unpack remote result: {exc}",
                    command=outcome.command,
                    skill_version=skill_version,
                    runner=outcome.worker_id,
                )
        expected_outputs = tuple(str(name) for name in manifest.get("expected_outputs", ()))
        produced = tuple(name for name in expected_outputs if (job_dir / name).is_file())
        status = outcome.status
        error_message = outcome.error_message
        exit_code = outcome.exit_code
        if status == "completed" and exit_code == 0:
            model_error = self._scan_error(run_dir / "events.jsonl")
            if model_error:
                status = "failed"
                exit_code = 1
                error_message = f"Pi model call failed: {model_error}"
        if status == "completed":
            missing = [name for name in expected_outputs if name not in produced]
            if missing:
                status = "failed"
                exit_code = 1
                error_message = f"Missing outputs after Pi run: {', '.join(missing)}"
        return ExecutionResult(
            status=status,
            exit_code=exit_code,
            error_message=error_message,
            command=outcome.command,
            # An empty log_path falls back to the path stored at claim time.
            run_dir=str(run_dir) if run_dir.is_dir() else "",
            session_dir=str(session_dir) if session_dir.is_dir() else "",
            skill_version=skill_version,
            produced_artifacts=produced,
            runner=outcome.worker_id,
        )

    def _cleanup_bundles(self, payload: RemoteExecutionPayload, outcome: RemoteOutcome) -> None:
        """Delete broker-owned bundle/archive files once the lease is finished.

        Skill snapshot cleanup stays with the payload builder (it ran at
        submit time); these files live in the broker's bundle dir.
        """
        (self._broker.bundle_dir / payload.bundle_name).unlink(missing_ok=True)
        if outcome.result_archive_name:
            (self._broker.bundle_dir / outcome.result_archive_name).unlink(missing_ok=True)
