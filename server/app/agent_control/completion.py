from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.agent_broker.agent_bundle import extract_agent_result
from server.app.agent_broker.result_timing import mark as mark_result_stage
from server.app.agent_broker.result_unpack import code_result_log_target, plan_agent_result_moves
from server.app.agent_broker.result_unpack_pool import unpack_in_pool
from server.app.agent_control import completion_staged
from server.app.db.dialect import ConnectSource
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult, ExecutionStatus
from server.app.services.artifact_store import ArtifactStore
from server.app.services.connection_tokens import ConnectionTokenService
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.skills.manager import SkillManager
from server.app.storage_paths import resolve_job_dir

if TYPE_CHECKING:
    from server.app.agent_broker.result_timing import ResultStageTimer

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


def report_auth_failure_safe(database_dsn: ConnectSource, connection_key: str) -> None:
    """Worker-reported auth failure (batch 2): invalidate the cached token.

    Reporting must never mask a committed result, so failures are logged and
    swallowed here."""
    try:
        ConnectionTokenService(database_dsn).report_auth_failure(connection_key)
    except Exception:
        # #204 broad-except audit: fire-and-forget invalidation after the
        # result already committed (agent_result_commit calls this below its
        # own success path). A failure to delete the cached token must never
        # mask or fail the committed report — the stale token stays cached,
        # and the next refresh cycle (or a retrying Worker's next 401
        # report) re-runs the invalidation, so the outcome space (vault/DB
        # surface) self-heals. logger.exception keeps the traceback; the
        # docstring pins the "reporting must never mask" contract.
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
        spot_check_percent: int | None = None,
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
        # #356 plan B: the trust-reported artifacts' spot-check percent
        # (agent_workers.artifact_spot_check_percent); None = module default.
        self.spot_check_percent = spot_check_percent

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
        stage_timer: ResultStageTimer | None = None,
    ) -> bool:
        job_db = self.leases.job_db
        job = job_db.get_job(job_id) if job_db is not None else None
        if job is None:
            result = ExecutionResult(
                status="failed", exit_code=1, error_message=f"job {job_id!r} is missing"
            )
            return self.leases.finish(lease_id, result, stage_timer=stage_timer)
        job_dir = resolve_job_dir(job, self.jobs_dir)
        expected = tuple(str(name) for name in manifest.get("expected_outputs", ()))
        # Batch 2 (decision 10): a kind='code' archive's node.log member is
        # promoted to the run's canonical log path. For a cancelled run the
        # archive's partial outputs are still uploaded and registered as
        # artifact refs below (parity with the agent path); they are just
        # never promoted into the job dir — only the partial log is.
        log_target = code_result_log_target(manifest, self.leases.data_dir or self.jobs_dir.parent)
        cancelled = outcome.status == "cancelled"
        # #759 review P1-1: the archive is extracted into a staging dir and
        # NOTHING lands in job_dir here — the file promotion rides the
        # lease-finish generation gate (ExecutionResult.staged_file_moves),
        # so a stale (post-reset) completion can never overwrite the new
        # generation's local inputs. view_dir is the read view every
        # pre-finish consumer (validation, shard read, mirror upload) uses.
        staging_cm: tempfile.TemporaryDirectory[str] | None = None
        staged_moves: list[tuple[Path, Path]] = []
        view_dir = job_dir
        if archive_name and (not cancelled or log_target is not None):
            staging_cm = tempfile.TemporaryDirectory(prefix=".result-staging-", dir=job_dir)
            try:
                # #552：解包是纯 CPU 段（tar/gzip + member 校验），下沉进程池
                # ——HTTP 平面线程只停在 future.result() 的 GIL 释放等待上，
                # 完成波不再挤单核；坏包炸子进程不炸主进程。
                unpack_in_pool(
                    extract_agent_result,
                    self.bundle_dir / archive_name,
                    Path(staging_cm.name),
                )
                staged_moves, _staged_produced = plan_agent_result_moves(
                    Path(staging_cm.name),
                    job_dir,
                    () if cancelled else expected,
                    "" if cancelled else outcome.run_dir,
                    log_target,
                )
            except Exception as exc:
                staging_cm.cleanup()
                # #204 broad-except audit: per-result containment that
                # CONVERTS, not masks — the Worker's untrusted archive
                # surface (gzip/tar corruption, unsafe member paths raising
                # AgentBundleError, disk errors) has no enumerable business
                # family, and every flavor must fail THIS node's lease with
                # a failed result instead of crashing the completion path
                # (the lease would otherwise time out and requeue into the
                # same poison archive). The converted message rides
                # ExecutionResult.error_message; the Worker-side traceback
                # stays in the Worker's own log.
                mark_result_stage(stage_timer, "unpack")
                return self.leases.finish(
                    lease_id,
                    ExecutionResult(
                        status="failed",
                        exit_code=1,
                        error_message=f"failed to unpack Agent result: {exc}",
                        runner=worker_id,
                    ),
                    stage_timer=stage_timer,
                )
            view_dir = Path(staging_cm.name)
        mark_result_stage(stage_timer, "unpack")
        try:
            return completion_staged.finish_staged(
                self,
                lease_id=lease_id,
                worker_id=worker_id,
                job_id=job_id,
                node_key=node_key,
                job=job,
                manifest=manifest,
                outcome=outcome,
                job_dir=job_dir,
                view_dir=view_dir,
                expected=expected,
                staged_moves=staged_moves,
                cancelled=cancelled,
                stage_timer=stage_timer,
            )
        finally:
            if staging_cm is not None:
                staging_cm.cleanup()
