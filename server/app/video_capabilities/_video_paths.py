from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from server.app.settings import Settings


def load_video_input(job_dir: Path) -> dict[str, Any]:
    """Load video_input.json if present, otherwise return an empty mapping."""
    path = job_dir / "video_input.json"
    if not path.is_file():
        return {}
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def resolve_canonical_video_path(legacy_video_id: str, settings: Settings) -> Path | None:
    """Return the canonical output video path if it exists."""
    if not legacy_video_id:
        return None
    candidate = settings.videos_dir / legacy_video_id / f"{legacy_video_id}.mp4"
    if candidate.is_file():
        return candidate
    return None


def resolve_video_url(
    job_dir: Path,
    video_input: dict,
    settings: Settings,
    local_video_url: str | None = None,
) -> str | None:
    """Choose the best available video URL for a video job."""
    if (job_dir / "source.mp4").is_file():
        return local_video_url
    legacy_video_id = video_input.get("legacy_video_id") or ""
    if resolve_canonical_video_path(legacy_video_id, settings) is not None:
        return local_video_url
    return video_input.get("source_url") or None


def build_video_source_response(
    job_dir: Path, settings: Settings
) -> FileResponse | RedirectResponse:
    """Return a response that serves the local source, canonical output, or source URL."""
    source_path = job_dir / "source.mp4"
    if source_path.is_file():
        return FileResponse(source_path, media_type="video/mp4", filename="source.mp4")

    video_input = load_video_input(job_dir)
    legacy_video_id = video_input.get("legacy_video_id") or ""
    source_url = video_input.get("source_url") or ""

    canonical_path = resolve_canonical_video_path(legacy_video_id, settings)
    if canonical_path is not None:
        return FileResponse(
            canonical_path, media_type="video/mp4", filename=f"{legacy_video_id}.mp4"
        )

    if source_url:
        return RedirectResponse(url=source_url, status_code=302)

    raise HTTPException(status_code=404, detail="Video source not found")
