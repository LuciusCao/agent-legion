import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.app.storage_paths import (
    ManagedPathError,
    resolve_job_dir,
    resolve_video_dir,
)

PACKAGE_FILES = [
    "metadata.json",
    "chapters.json",
    "interactions.json",
    "review_result.json",
    "report.md",
    "upload_params.json",
]

WORKSPACE_PACKAGE_FILES = [
    "result.json",
    "comprehension_info.json",
    "question_context.json",
    "question.json",
    "upload_params.json",
    "metadata.json",
    "report.md",
]


def create_workspace_package(
    jobs: list[Any], packages_dir: Path, jobs_base_dir: Path
) -> tuple[Path, int]:
    packages_dir.mkdir(parents=True, exist_ok=True)
    package_path = (
        packages_dir / f"workspace-jobs-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}.zip"
    )
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "jobs": [
            {
                "id": job["id"],
                "source_id": job.get("source_id", ""),
                "workflow_key": job.get("workflow_key", ""),
                "status": job.get("status", ""),
            }
            for job in jobs
        ],
    }
    job_count = 0
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for job in jobs:
            try:
                job_dir = resolve_job_dir(job, jobs_base_dir)
            except ManagedPathError:
                continue
            if not job_dir.exists():
                continue
            for name in WORKSPACE_PACKAGE_FILES:
                path = job_dir / name
                if path.exists():
                    zf.write(path, f"{job['id']}/{name}")
            job_count += 1
    return package_path, job_count


def create_package(
    videos: list[Any], packages_dir: Path, videos_base_dir: Path | None = None
) -> tuple[Path, int]:
    packages_dir.mkdir(parents=True, exist_ok=True)
    package_path = packages_dir / f"video-hive-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}.zip"
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "videos": [
            {
                "id": video["id"],
                "title": video.get("title", video["id"]),
                "source_url": video.get("source_url", ""),
                "content_type": video.get("content_type", "knowledge"),
                "external_id": video.get("external_id", ""),
                "knowledge_code": video.get("knowledge_code", ""),
                "question_id": video.get("question_id", ""),
                "source_uuid": video.get("source_uuid", ""),
                "status": video.get("status", ""),
            }
            for video in videos
        ],
    }
    video_count = 0
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for video in videos:
            if videos_base_dir is None:
                continue
            try:
                video_dir = resolve_video_dir(video, videos_base_dir)
            except ManagedPathError:
                continue
            for name in PACKAGE_FILES:
                path = video_dir / name
                if path.exists():
                    zf.write(path, f"{video['id']}/{name}")
            reviewed_srt = video_dir / "subtitles_reviewed.srt"
            srt = video_dir / "subtitles.srt"
            if reviewed_srt.exists():
                zf.write(reviewed_srt, f"{video['id']}/subtitles.srt")
            elif srt.exists():
                zf.write(srt, f"{video['id']}/subtitles.srt")
            video_count += 1
    return package_path, video_count
