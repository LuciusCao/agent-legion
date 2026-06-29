from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.pipeline.common import parse_srt_file
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
    path = job_dir / "video_input.json"
    if not path.exists():
        raise FileNotFoundError(f"video_input.json not found in {job_dir}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return VideoJobInputResponse(**data)


def _load_subtitles(job_dir: Path) -> list[VideoSubtitleResponse]:
    return [
        VideoSubtitleResponse(**subtitle) for subtitle in parse_srt_file(job_dir / "subtitles.srt")
    ]


def project_video_job_detail(
    job_dir: Path, *, local_video_url: str | None = None
) -> VideoJobDetailResponse:
    """Project a video job directory into a frontend detail response.

    This function is intentionally db-free: it only reads artifact files from
    ``job_dir`` and never queries the orchestration tables.
    """
    video_input = _load_video_input(job_dir)
    subtitles = _load_subtitles(job_dir)
    chapters = _read_json(job_dir / "chapters.json") or []
    interactions = _read_json(job_dir / "interactions.json") or []
    metadata = _read_json(job_dir / "metadata.json")
    review = _read_json(job_dir / "review_result.json")
    checklist = _read_json(job_dir / "checklist.json")
    upload_params = _read_json(job_dir / "upload_params.json")

    video_url = local_video_url if (job_dir / "source.mp4").is_file() else video_input.source_url

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
            video_url=video_url or None,
        ),
    )
