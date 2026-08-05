"""Full-gate evidence for SECURITY-PATH-001.

Creates real PostgreSQL records with malicious ``storage_dir`` values and exercises
public delete/read services. Asserts that:

- failure is closed (no unhandled crash);
- outside sentinel files/directories are untouched;
- database state is unchanged;
- no staged trash is left outside managed roots.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_deletion import JobDeletionService
from server.app.services.job_operation_error import JobOperationError
from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError
from tests.helpers import ensure_legacy_workspace_tables
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.full_gate


def _job_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    logs_dir = data_dir / "logs"
    videos_dir = data_dir / "videos"
    packages_dir = data_dir / "packages"
    for path in [data_dir, jobs_dir, logs_dir, videos_dir, packages_dir]:
        path.mkdir(parents=True, exist_ok=True)
    return Settings(
        root_dir=tmp_path,
        data_dir=data_dir,
        videos_dir=videos_dir,
        logs_dir=logs_dir,
        packages_dir=packages_dir,
        jobs_dir=jobs_dir,
        config={},
    )


def _create_job_with_malicious_storage(job_db: JobQueries, malicious_storage_dir: str) -> dict:
    workspace = job_db.create_workspace(
        "corrupt-ws", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["CORRUPT001"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        "question_comprehension_info",
        "question",
        "CORRUPT001",
        batch["id"],
        "Corrupt Job",
        ["fetch_question_context"],
        workspace_id=workspace["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set storage_dir=%s where id=%s",
            (malicious_storage_dir, job["id"]),
        )
    return job_db.get_job(job["id"])


def test_job_delete_rejects_outside_sibling_storage(tmp_path: Path) -> None:
    settings = _job_settings(tmp_path)
    job_db_path = TEST_DATABASE_URL
    job_db = JobQueries(job_db_path, settings.jobs_dir)
    ensure_legacy_workspace_tables(job_db)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("outside", encoding="utf-8")

    job = _create_job_with_malicious_storage(
        job_db,
        str(outside / "escaped_job"),
    )

    with pytest.raises(JobOperationError) as exc_info:
        service.delete(job["workspace_id"], job["id"])

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "delete_failed"
    assert sentinel.read_text(encoding="utf-8") == "outside"
    assert job_db.get_job(job["id"]) is not None
    assert not (outside / ".trash").exists()


def test_job_delete_rejects_symlink_escape_storage(tmp_path: Path) -> None:
    settings = _job_settings(tmp_path)
    job_db_path = TEST_DATABASE_URL
    job_db = JobQueries(job_db_path, settings.jobs_dir)
    ensure_legacy_workspace_tables(job_db)
    lease_repo = ExecutorLeaseRepository(job_db.path)
    service = JobDeletionService(job_db, lease_repo, settings)

    outside = tmp_path / "outside_target"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("outside", encoding="utf-8")

    link = settings.jobs_dir / "link_to_outside"
    link.symlink_to(outside)

    job = _create_job_with_malicious_storage(
        job_db,
        str(link / "escaped_job"),
    )

    with pytest.raises(JobOperationError) as exc_info:
        service.delete(job["workspace_id"], job["id"])

    assert exc_info.value.status == "failed"
    assert exc_info.value.reason_code == "delete_failed"
    assert sentinel.read_text(encoding="utf-8") == "outside"
    assert job_db.get_job(job["id"]) is not None
    assert not (outside / ".trash").exists()


def test_job_artifact_read_rejects_outside_storage(tmp_path: Path) -> None:
    settings = _job_settings(tmp_path)
    job_db_path = TEST_DATABASE_URL
    job_db = JobQueries(job_db_path, settings.jobs_dir)
    ensure_legacy_workspace_tables(job_db)
    service = JobArtifactService(job_db)

    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("outside", encoding="utf-8")

    job = _create_job_with_malicious_storage(
        job_db,
        str(outside / "escaped_job"),
    )

    with pytest.raises(ManagedPathError):
        service.read(job["id"], "sentinel.txt")

    assert sentinel.read_text(encoding="utf-8") == "outside"
    assert job_db.get_job(job["id"]) is not None


def test_job_artifact_read_rejects_symlink_escape_storage(tmp_path: Path) -> None:
    settings = _job_settings(tmp_path)
    job_db_path = TEST_DATABASE_URL
    job_db = JobQueries(job_db_path, settings.jobs_dir)
    ensure_legacy_workspace_tables(job_db)
    service = JobArtifactService(job_db)

    outside = tmp_path / "outside_target"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("outside", encoding="utf-8")

    link = settings.jobs_dir / "link_to_outside"
    link.symlink_to(outside)

    job = _create_job_with_malicious_storage(
        job_db,
        str(link / "escaped_job"),
    )

    with pytest.raises(ManagedPathError):
        service.read(job["id"], "sentinel.txt")

    assert sentinel.read_text(encoding="utf-8") == "outside"
    assert job_db.get_job(job["id"]) is not None
