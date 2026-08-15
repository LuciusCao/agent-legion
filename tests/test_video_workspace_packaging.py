from __future__ import annotations

import json
import zipfile
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_packages import JobPackageService
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.registry import load_registered_workflow

# Generic packaging path (plan §1.4 #27): base names merged with the
# workflow's declared node outputs. Every artifact the fixture writes is a
# declared video_knowledge output (or base name), so all of them ship.
EXPECTED_PACKAGE_FILES = [
    "chapters.json",
    "chapters_raw.json",
    "interactions.json",
    "metadata.json",
    "package_manifest.json",
    "report.md",
    "source.mp4",
    "subtitles.srt",
    "subtitles_reviewed.srt",
    "transcription.json",
    "upload_params.json",
    "video_input.json",
]


def _create_video_knowledge_job(
    job_db: JobQueries, settings: Settings, workspace_id: str, external_id: str
) -> dict[str, Any]:
    job_db.create_workspace(workspace_id, default_workflow_key="video_knowledge")
    batch = job_db.create_batch(
        "video_knowledge",
        "batch_by_urls",
        {"video_urls": [f"https://example.com/{external_id}.mp4"]},
        workspace_id,
    )
    definition = load_registered_workflow("video_knowledge")
    job = job_db.create_job(
        workflow_key="video_knowledge",
        source_type="video",
        source_id=external_id,
        batch_id=batch["id"],
        title=f"Video {external_id}",
        node_keys=list(definition.nodes),
        workspace_id=workspace_id,
    )
    job_db.update_job_status(job["id"], "completed")
    return job


def _write_video_artifacts(job: dict[str, Any], settings: Settings) -> None:
    job_dir = resolve_job_dir(job, settings.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    # Declared outputs / base names: packaged under the generic path.
    (job_dir / "chapters.json").write_text(json.dumps({"chapters": []}), encoding="utf-8")
    (job_dir / "interactions.json").write_text(json.dumps({"interactions": []}), encoding="utf-8")
    (job_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n",
        encoding="utf-8",
    )
    (job_dir / "transcription.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
    (job_dir / "metadata.json").write_text(
        json.dumps({"title": job["title"]}),
        encoding="utf-8",
    )
    (job_dir / "upload_params.json").write_text(
        json.dumps({"external_id": job["source_id"]}),
        encoding="utf-8",
    )
    (job_dir / "package_manifest.json").write_text(
        json.dumps({"job_id": job["id"], "files": []}),
        encoding="utf-8",
    )
    (job_dir / "source.mp4").write_text("fake video bytes", encoding="utf-8")
    (job_dir / "video_input.json").write_text(json.dumps({}), encoding="utf-8")
    (job_dir / "report.md").write_text("# Video report", encoding="utf-8")
    (job_dir / "chapters_raw.json").write_text(json.dumps({}), encoding="utf-8")
    (job_dir / "subtitles_reviewed.srt").write_text("reviewed\n", encoding="utf-8")
    # Not declared anywhere: must NOT be packaged.
    (job_dir / "notes.txt").write_text("scratch", encoding="utf-8")


def test_video_knowledge_workspace_package_includes_deliverables(
    job_db: JobQueries, settings: Settings
) -> None:
    service = JobPackageService(job_db, settings)
    workspace_id = "video-pkg-ws"
    job = _create_video_knowledge_job(job_db, settings, workspace_id, "VID001")
    _write_video_artifacts(job, settings)

    response = service.package(workspace_id, [job["id"]])

    assert response["succeeded_count"] == 1
    assert response["failed_count"] == 0
    assert response["package_filename"]

    package_path = (
        settings.packages_dir / f"workspace-{workspace_id}" / response["package_filename"]
    )
    assert package_path.exists()
    assert package_path.stat().st_size > 0

    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())
        prefix = f"{job['id']}/"

        assert "manifest.json" in names
        for name in EXPECTED_PACKAGE_FILES:
            assert f"{prefix}{name}" in names
        job_entries = {n for n in names if n.startswith(prefix)}
        assert job_entries == {f"{prefix}{name}" for name in EXPECTED_PACKAGE_FILES}
        assert f"{prefix}notes.txt" not in names


def test_video_knowledge_workspace_package_purges_source_video(
    job_db: JobQueries, settings: Settings
) -> None:
    service = JobPackageService(job_db, settings)
    workspace_id = "video-pkg-ws"
    job = _create_video_knowledge_job(job_db, settings, workspace_id, "VID001")
    _write_video_artifacts(job, settings)
    job_dir = resolve_job_dir(job, settings.jobs_dir)

    response = service.package(workspace_id, [job["id"]])

    assert response["succeeded_count"] == 1
    assert not (job_dir / "source.mp4").exists()
    # Packaged deliverables and rerun inputs stay on disk.
    assert (job_dir / "chapters.json").is_file()
    assert (job_dir / "video_input.json").is_file()
