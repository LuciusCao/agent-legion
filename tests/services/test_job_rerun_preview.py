"""batch_rerun_preview：大 selection 的集合式查询回归（N+1 防护）。"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

import server.app.executors.leases as leases_module
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services._job_rerun_preview import batch_rerun_preview
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_rerun import JobRerunService
from tests.helpers import publish_builtin_revision

_NODE_KEYS = [
    "intake_knowledge_points",
    "write_script",
    "review_script",
    "publish_content",
]

_JOB_COUNT = 300
# 读连接上界：bulk jobs + bulk nodes + bulk leases（显式 ids 无 filter 扫描）。
_MAX_READ_CONNECTIONS = 8


def _seed_failed_jobs(job_db, count: int) -> tuple[str, list[str]]:
    workspace = job_db.create_workspace(
        "preview-perf", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": [f"Q{i}" for i in range(count)]},
        workspace_id=workspace["id"],
    )
    ids: list[str] = []
    for i in range(count):
        job = job_db.create_job(
            workflow_key="education_video_problems_generation",
            source_type="question",
            source_id=f"Q{i}",
            run_id=batch["id"],
            title=f"Q{i}",
            node_keys=_NODE_KEYS,
            workspace_id=workspace["id"],
        )
        job_db.update_job_status(job["id"], "failed", "boom")
        ids.append(str(job["id"]))
    return str(workspace["id"]), ids


@pytest.fixture
def preview_service(job_db, settings):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db.path, data_dir=settings.data_dir),
        settings,
        JobArtifactMutationService(settings.jobs_dir),
    )


def _count_read_connections(job_db, monkeypatch) -> dict[str, int]:
    """Count DB read connections opened through JobQueries and the lease repo."""
    counter = {"n": 0}

    original_connect = type(job_db)._connect_read

    @contextmanager
    def counting_connect(self):
        counter["n"] += 1
        with original_connect(self) as conn:
            yield conn

    monkeypatch.setattr(type(job_db), "_connect_read", counting_connect)

    original_read = leases_module.read_connection

    @contextmanager
    def counting_read(dsn):
        counter["n"] += 1
        with original_read(dsn) as conn:
            yield conn

    monkeypatch.setattr(leases_module, "read_connection", counting_read)
    return counter


def test_preview_constant_queries_for_large_selection(preview_service, job_db, monkeypatch) -> None:
    workspace_id, ids = _seed_failed_jobs(job_db, _JOB_COUNT)
    counter = _count_read_connections(job_db, monkeypatch)

    result = batch_rerun_preview(preview_service, workspace_id, ids, "intake_knowledge_points")

    assert result == {"total_count": _JOB_COUNT, "eligible_count": _JOB_COUNT}
    assert counter["n"] <= _MAX_READ_CONNECTIONS


def test_preview_from_failed_node_constant_queries(preview_service, job_db, monkeypatch) -> None:
    workspace_id, ids = _seed_failed_jobs(job_db, _JOB_COUNT)
    for job_id in ids:
        job_db.update_job_node(job_id, "write_script", status="failed")
    counter = _count_read_connections(job_db, monkeypatch)

    result = batch_rerun_preview(preview_service, workspace_id, ids, from_failed_node=True)

    assert result == {"total_count": _JOB_COUNT, "eligible_count": _JOB_COUNT}
    assert counter["n"] <= _MAX_READ_CONNECTIONS
