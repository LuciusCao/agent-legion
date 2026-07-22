from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from server.app.agent_bundle import AgentBundleError, extract_agent_result
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult, ExecutionStatus
from server.app.services.artifact_store import ArtifactStore
from server.app.storage_paths import resolve_job_dir


@dataclass(frozen=True)
class AgentOutcome:
    status: ExecutionStatus
    exit_code: int
    error_message: str = ""
    command: tuple[str, ...] = ()
    output_artifacts: dict[str, str] = field(default_factory=dict)


def _unpack_result(archive_path: Path, job_dir: Path, expected: tuple[str, ...]) -> None:
    """Extract into a staging dir, then promote only declared expected outputs.

    Worker archives are untrusted: nothing outside ``expected`` lands in the
    job dir, so a Worker cannot clobber other nodes' inputs/outputs or plant a
    ``runs/.../events.jsonl`` to spoof Pi model-error detection (which this
    handler deliberately does not perform on Worker-supplied files)."""
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


class AgentCompletionHandler:
    def __init__(
        self,
        leases: ExecutorLeaseRepository,
        artifact_store: ArtifactStore,
        jobs_dir: Path,
        bundle_dir: Path,
    ) -> None:
        self.leases = leases
        self.artifact_store = artifact_store
        self.jobs_dir = jobs_dir
        self.bundle_dir = bundle_dir

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
                _unpack_result(self.bundle_dir / archive_name, job_dir, expected)
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
        return self.leases.finish(
            lease_id,
            ExecutionResult(
                status=status,
                exit_code=exit_code,
                error_message=error,
                command=outcome.command,
                # Worker-side run dirs (events.jsonl, session) are not promoted
                # to the host: they are Worker-controlled and must not feed
                # server-side success/failure decisions.
                run_dir="",
                session_dir="",
                skill_version=str(manifest.get("skill_version", "")),
                produced_artifacts=produced,
                runner=worker_id,
            ),
        )
