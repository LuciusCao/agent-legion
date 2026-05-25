import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PACKAGE_FILES = [
    "metadata.json",
    "chapters.json",
    "interactions.json",
    "review_result.json",
    "report.md",
    "upload_params.json",
]


def create_package(videos: list[dict], packages_dir: Path, videos_base_dir: Path | None = None) -> Path:
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
                "status": video.get("status", ""),
            }
            for video in videos
        ],
    }
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for video in videos:
            storage_dir = video.get("storage_dir", "")
            if storage_dir:
                video_dir = Path(storage_dir)
            elif videos_base_dir is not None:
                video_dir = videos_base_dir / video["id"]
            else:
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
    return package_path
