from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_packages import JobPackageResult, JobPackageService
from server.app.settings import Settings


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


def _write_artifact(job: dict[str, Any]) -> None:
    storage_dir = Path(str(job["storage_dir"]))
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
    _write_artifact(completed_a)
    completed_b = _create_job(job_db, workspace_id, "Q101", status="completed")
    _write_artifact(completed_b)
    incomplete = _create_job(job_db, workspace_id, "Q102", status="queued")
    _write_artifact(incomplete)
    other_workspace_job = _create_job(job_db, "other-ws", "Q103", status="completed")
    _write_artifact(other_workspace_job)

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
    _write_artifact(incomplete)

    response: JobPackageResult = service.package(workspace_id, [incomplete["id"]])

    assert response["results"][0]["status"] == "failed"
    assert response["results"][0]["reason_code"] == "not_completed"
    assert response["succeeded_count"] == 0
    assert response["failed_count"] == 1
    assert response.get("package_filename") is None
    assert response.get("download_url") is None
