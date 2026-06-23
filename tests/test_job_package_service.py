from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from server.app.jobs import JobQueries
from server.app.services.job_packages import (
    JobPackageResult,
    JobPackageService,
    WorkspacePackageLockedError,
)
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir


def _create_settings(tmp_path: Path) -> Settings:
    # Match the paths produced by the job_db fixture.
    data_dir = tmp_path
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


def _create_job(
    job_db: JobQueries, workspace_id: str, source_id: str, status: str = "queued"
) -> dict[str, Any]:
    job_db.create_workspace(workspace_id)
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": [source_id]}, workspace_id
    )
    job = job_db.create_job(
        "question_content",
        "question",
        source_id,
        batch["id"],
        f"Job {source_id}",
        ["extract_question"],
        workspace_id=workspace_id,
    )
    if status != "queued":
        job_db.update_job_status(job["id"], status)
    return job


def _write_artifact(job: dict[str, Any], settings: Settings) -> None:
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "question_context.json").write_text(
        '{"question_id":"' + job["source_id"] + '"}', encoding="utf-8"
    )


def test_package_returns_ordered_results_with_reason_codes(
    job_db: JobQueries, tmp_path: Path
) -> None:
    settings = _create_settings(tmp_path)
    service = JobPackageService(job_db, settings)

    workspace_id = "pkg-ws"
    completed_a = _create_job(job_db, workspace_id, "Q100", status="completed")
    _write_artifact(completed_a, settings)
    completed_b = _create_job(job_db, workspace_id, "Q101", status="completed")
    _write_artifact(completed_b, settings)
    incomplete = _create_job(job_db, workspace_id, "Q102", status="queued")
    _write_artifact(incomplete, settings)
    other_workspace_job = _create_job(job_db, "other-ws", "Q103", status="completed")
    _write_artifact(other_workspace_job, settings)

    job_ids = [
        completed_a["id"],
        other_workspace_job["id"],
        "missing-job",
        incomplete["id"],
        completed_b["id"],
    ]
    response: JobPackageResult = service.package(workspace_id, job_ids)

    results = response["results"]
    assert [r["job_id"] for r in results] == job_ids

    assert results[0]["status"] == "succeeded"
    assert results[0].get("reason_code") is None

    assert results[1]["status"] == "failed"
    assert results[1]["reason_code"] == "wrong_workspace"

    assert results[2]["status"] == "failed"
    assert results[2]["reason_code"] == "not_found"

    assert results[3]["status"] == "failed"
    assert results[3]["reason_code"] == "not_completed"

    assert results[4]["status"] == "succeeded"
    assert results[4].get("reason_code") is None

    assert response["succeeded_count"] == 2
    assert response["failed_count"] == 3
    assert response["package_filename"]
    assert response["download_url"]

    package_path = (
        settings.packages_dir / f"workspace-{workspace_id}" / response["package_filename"]
    )
    assert package_path.exists()
    assert package_path.stat().st_size > 0


def test_package_requires_at_least_one_eligible_job(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    service = JobPackageService(job_db, settings)

    workspace_id = "empty-pkg-ws"
    incomplete = _create_job(job_db, workspace_id, "Q200", status="queued")
    _write_artifact(incomplete, settings)

    response: JobPackageResult = service.package(workspace_id, [incomplete["id"]])

    assert response["results"][0]["status"] == "failed"
    assert response["results"][0]["reason_code"] == "not_completed"
    assert response["succeeded_count"] == 0
    assert response["failed_count"] == 1
    assert response.get("package_filename") is None
    assert response.get("download_url") is None


def test_package_creates_workspace_package_record_and_marks_jobs_packed(
    job_db: JobQueries, tmp_path: Path
) -> None:
    settings = _create_settings(tmp_path)
    service = JobPackageService(job_db, settings)

    workspace_id = "pkg-ws-record"
    completed_a = _create_job(job_db, workspace_id, "Q300", status="completed")
    _write_artifact(completed_a, settings)
    completed_b = _create_job(job_db, workspace_id, "Q301", status="completed")
    _write_artifact(completed_b, settings)

    response = service.package(workspace_id, [completed_a["id"], completed_b["id"]])

    assert response["succeeded_count"] == 2
    assert response["package_filename"]

    packages = job_db.list_workspace_packages(workspace_id)
    assert len(packages) == 1
    pkg = packages[0]
    assert pkg["job_count"] == 2
    assert pkg["size_bytes"] > 0
    assert pkg["name"] == "批次 (2个任务)"
    assert pkg["path"].startswith("packages/workspace-pkg-ws-record/")
    assert pkg["locked"] == 0

    assert job_db.get_job(completed_a["id"])["packed"] == 1
    assert job_db.get_job(completed_b["id"])["packed"] == 1


def test_workspace_package_lifecycle_respects_locked(job_db: JobQueries, tmp_path: Path) -> None:
    settings = _create_settings(tmp_path)
    service = JobPackageService(job_db, settings)

    workspace_id = "pkg-ws-lifecycle"
    completed = _create_job(job_db, workspace_id, "Q400", status="completed")
    _write_artifact(completed, settings)

    service.package(workspace_id, [completed["id"]])
    pkg = job_db.list_workspace_packages(workspace_id)[0]

    service.lock_workspace_package(workspace_id, pkg["id"], True)
    assert job_db.get_workspace_package(workspace_id, pkg["id"])["locked"] == 1

    with pytest.raises(WorkspacePackageLockedError):
        service.delete_workspace_package(workspace_id, pkg["id"])

    service.lock_workspace_package(workspace_id, pkg["id"], False)
    service.delete_workspace_package(workspace_id, pkg["id"])
    assert job_db.list_workspace_packages(workspace_id) == []
