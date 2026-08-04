from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class VideoKnowledgeInput:
    schema_version: Literal[1]
    entity_type: Literal["video"]
    content_type: Literal["knowledge"]
    legacy_video_id: str
    external_id: str
    source_uuid: str
    source_url: str
    source_ref: str
    title: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> VideoKnowledgeInput:
        required = {"schema_version": 1, "entity_type": "video", "content_type": "knowledge"}
        for key, expected in required.items():
            if value.get(key) != expected:
                raise ValueError(f"{key} must be {expected}")
        external_id = str(value.get("external_id") or value.get("legacy_video_id") or "").strip()
        if not external_id:
            raise ValueError("external_id or legacy_video_id is required")
        return cls(
            schema_version=1,
            entity_type="video",
            content_type="knowledge",
            legacy_video_id=str(value.get("legacy_video_id") or ""),
            external_id=external_id,
            source_uuid=str(value.get("source_uuid") or ""),
            source_url=str(value.get("source_url") or ""),
            source_ref=str(value.get("source_ref") or ""),
            title=str(value.get("title") or ""),
        )
