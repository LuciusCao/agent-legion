"""Host-side output validation for Agent Worker results.

Worker-reported results are untrusted: AgentCompletionHandler must re-run the
node skill's ``scripts/validate_output.py`` after unpacking, exactly like the
local Pi runner does, so review rejections (and any contract violation) fail
the node instead of silently flowing downstream.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.agent_control.completion import AgentCompletionHandler, AgentOutcome
from tests.helpers.skill_manager import _make_skill_manager

_VALIDATE_OK = "import sys\nsys.exit(0)\n"
_VALIDATE_REJECT = (
    "import sys\n"
    "print('Review rejected key_info items: ki_00000000', file=sys.stderr)\n"
    "sys.exit(1)\n"
)


class _StubJobDb:
    def __init__(self, job: dict[str, Any]) -> None:
        self._job = job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._job if job_id == self._job["id"] else None


class _StubLeases:
    def __init__(self, job: dict[str, Any]) -> None:
        self.job_db = _StubJobDb(job)
        self.data_dir = None
        self.results: list[Any] = []

    def finish(self, lease_id: str, result: Any) -> bool:
        self.results.append(result)
        return True


class _StubArtifactStore:
    def __init__(self) -> None:
        self.refs: list[tuple[str, str, str, str]] = []

    def add_ref(self, job_id: str, node_key: str, name: str, digest: str) -> None:
        self.refs.append((job_id, node_key, name, digest))


def _make_handler(
    tmp_path: Path,
    *,
    validate_script: str | None = None,
    with_skill_manager: bool = True,
) -> tuple[AgentCompletionHandler, _StubLeases, dict[str, Any], Path]:
    jobs_dir = tmp_path / "jobs"
    job = {"id": "job-1", "workspace_id": "ws-1", "storage_dir": "jobs/ws/job-1"}
    job_dir = jobs_dir / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "out.json").write_text("{}", encoding="utf-8")
    leases = _StubLeases(job)
    skill_manager = (
        _make_skill_manager(tmp_path, "wf/review_node", validate_script)
        if with_skill_manager
        else None
    )
    handler = AgentCompletionHandler(
        leases,  # type: ignore[arg-type]
        _StubArtifactStore(),  # type: ignore[arg-type]
        jobs_dir,
        tmp_path / "bundles",
        skill_manager=skill_manager,
    )
    return handler, leases, job, job_dir


def _finish(handler: AgentCompletionHandler, job: dict[str, Any]) -> None:
    handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id=job["id"],
        node_key="review_node",
        manifest={
            "expected_outputs": ["out.json"],
            "skill": "wf/review_node",
        },
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={"out.json": "sha256:abc"},
        ),
        archive_name="",
    )


def test_worker_result_fails_when_validator_rejects(tmp_path: Path) -> None:
    handler, leases, job, _ = _make_handler(tmp_path, validate_script=_VALIDATE_REJECT)
    _finish(handler, job)
    assert len(leases.results) == 1
    result = leases.results[0]
    assert result.status == "failed"
    assert result.exit_code == 1
    assert result.error_message.startswith("Output validation failed: Review rejected")


def test_worker_result_completes_when_validator_passes(tmp_path: Path) -> None:
    handler, leases, job, _ = _make_handler(tmp_path, validate_script=_VALIDATE_OK)
    _finish(handler, job)
    assert len(leases.results) == 1
    result = leases.results[0]
    assert result.status == "completed"
    assert result.exit_code == 0


def test_worker_result_skips_validation_without_skill_manager(tmp_path: Path) -> None:
    handler, leases, job, _ = _make_handler(
        tmp_path, validate_script=_VALIDATE_REJECT, with_skill_manager=False
    )
    _finish(handler, job)
    assert leases.results[0].status == "completed"
