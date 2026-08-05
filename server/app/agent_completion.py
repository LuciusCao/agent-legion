from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from server.app.agent_broker.agent_bundle import AgentBundleError, extract_agent_result
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult, ExecutionStatus
from server.app.services.artifact_store import ArtifactStore
from server.app.skills.manager import SkillManager
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.output_validation import validate_worker_outputs


@dataclass(frozen=True)
class AgentOutcome:
    status: ExecutionStatus
    exit_code: int
    error_message: str = ""
    command: tuple[str, ...] = ()
    output_artifacts: dict[str, str] = field(default_factory=dict)
    # Worker run dir relative to the job dir (e.g. "runs/<node>/worker"); its
    # events.jsonl is promoted for log display and token usage only — never
    # for success/failure decisions.
    run_dir: str = ""


def _safe_relative_dir(value: str) -> PurePosixPath | None:
    if not value:
        return None
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    return relative


def _unpack_result(
    archive_path: Path, job_dir: Path, expected: tuple[str, ...], run_dir: str = ""
) -> None:
    """Extract into a staging dir, then promote declared expected outputs plus
    the Worker run dir's ``events.jsonl``.

    Worker archives are untrusted: nothing outside ``expected`` and that single
    log file lands in the job dir, so a Worker cannot clobber other nodes'
    inputs/outputs or plant files to spoof server-side decisions (log display
    and token parsing are read-only consumers)."""
    with tempfile.TemporaryDirectory(prefix=".result-staging-", dir=job_dir) as staging:
        staging_dir = Path(staging)
        extract_agent_result(archive_path, staging_dir)
        for name in expected:
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise AgentBundleError(f"unsafe expected output name: {name!r}")
            source = staging_dir / relative
            if source.is_file():
                target = job_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
        run_dir_relative = _safe_relative_dir(run_dir)
        if run_dir_relative is not None:
            events_source = staging_dir / run_dir_relative / "events.jsonl"
            if events_source.is_file():
                events_target = job_dir / run_dir_relative / "events.jsonl"
                events_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(events_source), str(events_target))


class AgentCompletionHandler:
    def __init__(
        self,
        leases: ExecutorLeaseRepository,
        artifact_store: ArtifactStore,
        jobs_dir: Path,
        bundle_dir: Path,
        skill_manager: SkillManager | None = None,
    ) -> None:
        self.leases = leases
        self.artifact_store = artifact_store
        self.jobs_dir = jobs_dir
        self.bundle_dir = bundle_dir
        self.skill_manager = skill_manager

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
        if outcome.status != "cancelled" and archive_name:
            try:
                _unpack_result(self.bundle_dir / archive_name, job_dir, expected, outcome.run_dir)
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
        for name, ref in outcome.output_artifacts.items():
            digest = ref.split(":", 1)[-1]
            self.artifact_store.add_ref(job_id, node_key, name, digest)
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
        run_dir_relative = _safe_relative_dir(run_dir)
        if run_dir_relative is None or not (job_dir / run_dir_relative).is_dir():
            return ""
        base = self.leases.data_dir or self.jobs_dir.parent
        try:
            return (job_dir / run_dir_relative).resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            return ""
