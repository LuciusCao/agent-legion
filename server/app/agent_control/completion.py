from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.app.agent_broker.remote_artifacts import apply_worker_artifact_refs
from server.app.agent_broker.result_unpack import (
    code_result_log_target,
    safe_relative_dir,
    unpack_agent_result,
)
from server.app.db.connection import DatabaseDsn
from server.app.executors.artifact_mirror import upload_produced_artifacts
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult, ExecutionStatus
from server.app.services.artifact_store import ArtifactStore
from server.app.services.connection_tokens import ConnectionTokenService
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.skills.manager import SkillManager
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.output_validation import validate_worker_outputs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentOutcome:
    status: ExecutionStatus
    exit_code: int
    error_message: str = ""
    command: tuple[str, ...] = ()
    # name -> legacy CAS ref ("sha256:<hash>") or, since #160 D12, the
    # object-storage ref {"storage_key", "size_bytes", "content_hash"} of a
    # direct Worker upload (validated by parse_result_metadata).
    output_artifacts: dict[str, Any] = field(default_factory=dict)
    # Worker run dir relative to the job dir (e.g. "runs/<node>/worker"); its
    # events.jsonl is promoted for log display and token usage only — never
    # for success/failure decisions.
    run_dir: str = ""
    # Batch 2 (design §5.3): a code node asked for this connection's cached
    # token to be invalidated (upstream auth failure); the commit path
    # performs the privileged invalidation. Empty = no request.
    auth_failure_connection: str = ""


def report_auth_failure_safe(database_dsn: DatabaseDsn, connection_key: str) -> None:
    """Worker-reported auth failure (batch 2): invalidate the cached token.

    Reporting must never mask a committed result, so failures are logged and
    swallowed here."""
    try:
        ConnectionTokenService(database_dsn).report_auth_failure(connection_key)
    except Exception:
        logger.exception("connection %s: failed to report auth failure", connection_key)


class AgentCompletionHandler:
    def __init__(
        self,
        leases: ExecutorLeaseRepository,
        artifact_store: ArtifactStore,
        jobs_dir: Path,
        bundle_dir: Path,
        skill_manager: SkillManager | None = None,
        object_store: JobArtifactObjectStore | None = None,
        max_archive_bytes: int | None = None,
    ) -> None:
        self.leases = leases
        self.artifact_store = artifact_store
        self.jobs_dir = jobs_dir
        self.bundle_dir = bundle_dir
        self.skill_manager = skill_manager
        self.object_store = object_store
        # Instance size ceiling (agent_workers.max_archive_bytes), applied to
        # Worker-direct S3 uploads the same way the legacy archive channel
        # enforces it; None = no ceiling.
        self.max_archive_bytes = max_archive_bytes

    def finish(
        self,
        *,
        lease_id: str,
        worker_id: str,
        job_id: str,
        node_key: str,
        manifest: dict,
        outcome: AgentOutcome,
        archive_name: str,
    ) -> bool:
        job_db = self.leases.job_db
        job = job_db.get_job(job_id) if job_db is not None else None
        if job is None:
            result = ExecutionResult(
                status="failed", exit_code=1, error_message=f"job {job_id!r} is missing"
            )
            return self.leases.finish(lease_id, result)
        job_dir = resolve_job_dir(job, self.jobs_dir)
        expected = tuple(str(name) for name in manifest.get("expected_outputs", ()))
        # Batch 2 (decision 10): a kind='code' archive's node.log member is
        # promoted to the run's canonical log path. For a cancelled run the
        # archive's partial outputs are still uploaded and registered as
        # artifact refs below (parity with the agent path); they are just
        # never promoted into the job dir — only the partial log is.
        log_target = code_result_log_target(manifest, self.leases.data_dir or self.jobs_dir.parent)
        cancelled = outcome.status == "cancelled"
        if archive_name and (not cancelled or log_target is not None):
            try:
                unpack_agent_result(
                    self.bundle_dir / archive_name,
                    job_dir,
                    () if cancelled else expected,
                    "" if cancelled else outcome.run_dir,
                    log_target,
                )
            except Exception as exc:
                return self.leases.finish(
                    lease_id,
                    ExecutionResult(
                        status="failed",
                        exit_code=1,
                        error_message=f"failed to unpack Agent result: {exc}",
                        runner=worker_id,
                    ),
                )
        # #160 D12: dict-form refs mean the Worker uploaded straight to S3
        # (per-execution staging keys); verify ALL refs, then promote +
        # download + register (no half-applied state). Any failure flips the
        # whole result to failed.
        remote_names, remote_failure = apply_worker_artifact_refs(
            self.object_store,
            runner=worker_id,
            workspace_id=str(job["workspace_id"]),
            job_id=job_id,
            node_key=node_key,
            job_dir=job_dir,
            expected=expected,
            output_artifacts=outcome.output_artifacts,
            download=not cancelled,
            execution_id=str(manifest.get("execution_id") or ""),
            max_size_bytes=self.max_archive_bytes,
        )
        if remote_failure is not None:
            return self.leases.finish(lease_id, remote_failure)
        for name, ref in outcome.output_artifacts.items():
            if name not in remote_names:
                self.artifact_store.add_ref(job_id, node_key, name, str(ref).split(":", 1)[-1])
        produced = tuple(name for name in expected if (job_dir / name).is_file())
        status = outcome.status
        exit_code = outcome.exit_code
        error = outcome.error_message
        if status == "completed" and expected and not outcome.output_artifacts:
            status, exit_code, error = "failed", 1, "Agent Worker did not report output artifacts"
        missing = [name for name in expected if name not in produced]
        if status == "completed" and missing:
            status, exit_code, error = "failed", 1, f"Missing outputs: {', '.join(missing)}"
        # Worker results are untrusted: validate Host-side like the Pi runner.
        if status == "completed" and self.skill_manager is not None:
            validation_error = validate_worker_outputs(self.skill_manager, manifest, job_dir)
            if validation_error:
                status, exit_code, error = "failed", 1, validation_error
        # D12: mirror produced artifacts into object storage (best-effort —
        # a storage outage never flips the node; the reconciler retries).
        if status == "completed" and produced:
            upload_produced_artifacts(
                self.object_store,
                workspace_id=str(job["workspace_id"]),
                job_id=job_id,
                node_key=node_key,
                job_dir=job_dir,
                produced=produced,
                skip=remote_names,
            )
        return self.leases.finish(
            lease_id,
            ExecutionResult(
                status=status,
                exit_code=exit_code,
                error_message=error,
                command=outcome.command,
                # The promoted events.jsonl feeds log display and token usage
                # only; success/failure decisions above never read it.
                run_dir=self._stored_run_dir(job_dir, outcome.run_dir),
                session_dir="",
                skill_version=str(manifest.get("skill_version", "")),
                produced_artifacts=produced,
                runner=worker_id,
            ),
        )

    def _stored_run_dir(self, job_dir: Path, run_dir: str) -> str:
        """Data-dir-relative path of the promoted Worker run dir, or "".

        Empty when the Worker did not declare one or nothing was promoted, so
        older Workers and cancelled runs behave exactly as before."""
        run_dir_relative = safe_relative_dir(run_dir)
        if run_dir_relative is None or not (job_dir / run_dir_relative).is_dir():
            return ""
        base = self.leases.data_dir or self.jobs_dir.parent
        try:
            return (job_dir / run_dir_relative).resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            return ""
