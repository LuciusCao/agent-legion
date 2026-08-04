from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.settings import Settings
from server.app.video_capabilities.projection import project_video_job_detail


def _write_video_input(job_dir: Path, legacy_video_id: str = "", source_url: str = "") -> None:
    (job_dir / "video_input.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entity_type": "video",
                "content_type": "knowledge",
                "legacy_video_id": legacy_video_id,
                "external_id": "K001",
                "source_uuid": "uuid-1",
                "source_url": source_url,
                "title": "Title",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_subtitles(job_dir: Path) -> None:
    (job_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n",
        encoding="utf-8",
    )


@pytest.fixture
def projection_setup(tmp_path: Path) -> tuple[Path, Settings]:
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={"secret_key": "secret"},
    )
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    _write_video_input(job_dir)
    _write_subtitles(job_dir)
    return job_dir, settings


def test_project_video_job_detail_uses_source_mp4_when_present(
    projection_setup: tuple[Path, Settings],
) -> None:
    job_dir, settings = projection_setup
    (job_dir / "source.mp4").write_bytes(b"video")

    result = project_video_job_detail(job_dir, settings=settings, local_video_url="/local/source")

    assert result.artifacts.video_url == "/local/source"


def test_project_video_job_detail_falls_back_to_canonical_output(
    projection_setup: tuple[Path, Settings],
) -> None:
    job_dir, settings = projection_setup
    _write_video_input(job_dir, legacy_video_id="knowledge_k001")
    canonical_dir = settings.videos_dir / "knowledge_k001"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    (canonical_dir / "knowledge_k001.mp4").write_bytes(b"canonical")

    result = project_video_job_detail(job_dir, settings=settings, local_video_url="/local/source")

    assert result.artifacts.video_url == "/local/source"


def test_project_video_job_detail_falls_back_to_source_url(
    projection_setup: tuple[Path, Settings],
) -> None:
    job_dir, settings = projection_setup
    _write_video_input(job_dir, source_url="https://example.com/video.mp4")

    result = project_video_job_detail(job_dir, settings=settings, local_video_url="/local/source")

    assert result.artifacts.video_url == "https://example.com/video.mp4"
