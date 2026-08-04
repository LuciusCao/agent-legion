import json
from pathlib import Path

from server.app.workflows import video_knowledge


def write_video_input(job_dir: Path) -> None:
    (job_dir / "video_input.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entity_type": "video",
                "content_type": "knowledge",
                "legacy_video_id": "legacy-1",
                "external_id": "K001",
                "source_uuid": "",
                "source_url": "https://example.invalid/video.mp4",
                "title": "Title",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_assemble_video_metadata_writes_workspace_outputs(tmp_path: Path) -> None:
    write_video_input(tmp_path)
    (tmp_path / "source.mp4").write_bytes(b"fake")
    (tmp_path / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8"
    )
    (tmp_path / "subtitles_reviewed.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8"
    )
    (tmp_path / "chapters.json").write_text("[]", encoding="utf-8")
    (tmp_path / "interactions.json").write_text("[]", encoding="utf-8")
    (tmp_path / "checklist.json").write_text("{}", encoding="utf-8")
    (tmp_path / "review_result.json").write_text("{}", encoding="utf-8")

    video_knowledge.assemble_video_metadata({}, tmp_path, {"settings_config": {}})

    assert (tmp_path / "metadata.json").is_file()
    assert (tmp_path / "report.md").is_file()
    assert (tmp_path / "upload_params.json").is_file()


def test_package_video_job_writes_manifest(tmp_path: Path) -> None:
    write_video_input(tmp_path)
    (tmp_path / "metadata.json").write_text("{}", encoding="utf-8")
    (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
    (tmp_path / "upload_params.json").write_text("{}", encoding="utf-8")

    video_knowledge.package_video_job({}, tmp_path, {"settings_config": {}})

    manifest = json.loads((tmp_path / "package_manifest.json").read_text(encoding="utf-8"))
    assert manifest["workflow_key"] == "video_knowledge"
    assert "files" in manifest
