from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VideoJobInputResponse(BaseModel):
    schema_version: int
    entity_type: str
    content_type: str
    legacy_video_id: str = ""
    external_id: str = ""
    source_uuid: str = ""
    source_url: str = ""
    title: str = ""


class VideoSubtitleResponse(BaseModel):
    index: int
    start: float
    end: float
    text: str


class VideoJobArtifactsResponse(BaseModel):
    subtitles: list[VideoSubtitleResponse] = Field(default_factory=list)
    chapters: list[dict[str, Any]] = Field(default_factory=list)
    interactions: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    checklist: dict[str, Any] | None = None
    upload_params: dict[str, Any] | None = None
    video_url: str | None = None


class VideoJobDetailResponse(BaseModel):
    input: VideoJobInputResponse
    artifacts: VideoJobArtifactsResponse
