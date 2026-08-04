import json
from pathlib import Path
from typing import Any, cast

from server.app.pipeline.common import parse_srt_file
from server.app.settings import Settings
from server.app.video_capabilities._video_paths import resolve_video_url
from server.app.video_capabilities.response_contracts import (
    VideoJobArtifactsResponse,
    VideoJobDetailResponse,
    VideoJobInputResponse,
    VideoSubtitleResponse,
)


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_video_input(job_dir: Path) -> VideoJobInputResponse:
    if (data := _read_json(job_dir / "video_input.json")) is None:
        raise FileNotFoundError(f"video_input.json not found in {job_dir}")
    return VideoJobInputResponse(**data)


def _load_subtitles(job_dir: Path) -> list[VideoSubtitleResponse]:
    return [
        VideoSubtitleResponse(**subtitle) for subtitle in parse_srt_file(job_dir / "subtitles.srt")
    ]


def project_video_job_detail(
    job_dir: Path,
    *,
    settings: Settings,
    local_video_url: str | None = None,
) -> VideoJobDetailResponse:
    video_input = _load_video_input(job_dir)
    subtitles = _load_subtitles(job_dir)
    chapters = _read_json(job_dir / "chapters.json") or []
    interactions_data = _read_json(job_dir / "interactions.json") or []
    if isinstance(interactions_data, dict):
        interactions_data = interactions_data.get("interactions") or []
    interactions = cast(list[dict[str, Any]], interactions_data)
    metadata = _read_json(job_dir / "metadata.json")
    review = _read_json(job_dir / "review_result.json")
    checklist = _read_json(job_dir / "checklist.json")
    upload_params = _read_json(job_dir / "upload_params.json")

    return VideoJobDetailResponse(
        input=video_input,
        artifacts=VideoJobArtifactsResponse(
            subtitles=subtitles,
            chapters=chapters,
            interactions=interactions,
            metadata=metadata,
            review=review,
            checklist=checklist,
            upload_params=upload_params,
            video_url=resolve_video_url(
                job_dir, video_input.model_dump(), settings, local_video_url
            )
            or None,
        ),
    )
