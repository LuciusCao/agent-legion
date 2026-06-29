from pathlib import Path

import pytest

from server.app.video_capabilities.artifact_contract import (
    VIDEO_KNOWLEDGE_ARTIFACTS,
    artifact_owners,
)
from server.app.video_capabilities.contracts import (
    ArtifactRef,
    VideoKnowledgeInput,
    VideoNodeContext,
)


def test_video_input_from_mapping_accepts_knowledge_video() -> None:
    item = VideoKnowledgeInput.from_mapping(
        {
            "schema_version": 1,
            "entity_type": "video",
            "content_type": "knowledge",
            "legacy_video_id": "legacy-1",
            "external_id": "K001",
            "source_uuid": "source-1",
            "source_url": "https://example.invalid/video.mp4",
            "title": "Title",
        }
    )

    assert item.external_id == "K001"
    assert item.content_type == "knowledge"


def test_video_input_from_mapping_falls_back_to_legacy_video_id() -> None:
    item = VideoKnowledgeInput.from_mapping(
        {
            "schema_version": 1,
            "entity_type": "video",
            "content_type": "knowledge",
            "legacy_video_id": "legacy-1",
            "source_uuid": "source-1",
            "source_url": "https://example.invalid/video.mp4",
            "title": "Title",
        }
    )

    assert item.external_id == "legacy-1"
    assert item.legacy_video_id == "legacy-1"


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"schema_version": 2}, "schema_version"),
        ({"schema_version": 1, "entity_type": "question"}, "entity_type"),
        ({"schema_version": 1, "entity_type": "video", "content_type": "question"}, "content_type"),
        (
            {
                "schema_version": 1,
                "entity_type": "video",
                "content_type": "knowledge",
                "external_id": "",
            },
            "external_id",
        ),
    ],
)
def test_video_input_rejects_unsupported_payloads(payload: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        VideoKnowledgeInput.from_mapping(payload)


def test_video_context_is_storage_explicit_and_db_free(tmp_path: Path) -> None:
    input_payload = VideoKnowledgeInput.from_mapping(
        {
            "schema_version": 1,
            "entity_type": "video",
            "content_type": "knowledge",
            "legacy_video_id": "legacy-1",
            "external_id": "K001",
            "source_uuid": "",
            "source_url": "https://example.invalid/video.mp4",
            "title": "Title",
        }
    )
    ref = ArtifactRef(
        name="video_input.json", path=tmp_path / "video_input.json", media_type="application/json"
    )
    context = VideoNodeContext(
        entity_id="K001",
        storage_dir=tmp_path,
        input=input_payload,
        inputs={"video_input.json": ref},
        config={},
        resources={},
    )

    assert context.storage_dir == tmp_path
    assert context.inputs["video_input.json"].name == "video_input.json"


def test_video_artifacts_have_unique_owners() -> None:
    owners = artifact_owners()
    assert owners["subtitles.srt"] == "transcribe"
    assert owners["metadata.json"] == "assemble"
    assert owners["package_manifest.json"] == "package"
    assert len(owners) == sum(len(value) for value in VIDEO_KNOWLEDGE_ARTIFACTS.values())
