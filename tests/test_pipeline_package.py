import json
import zipfile

from server.app.pipeline.package import create_package


def test_package_completed_videos(tmp_path):
    video_dir = tmp_path / "videos" / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "metadata.json").write_text(json.dumps({"video_id": "a"}), encoding="utf-8")
    (video_dir / "chapters.json").write_text("[]", encoding="utf-8")
    (video_dir / "interactions.json").write_text(
        json.dumps({"version": "1.0", "interactions": []}), encoding="utf-8"
    )

    package_path = create_package(
        videos=[{"id": "a", "title": "A", "source_url": "https://example.com/a.mp4", "storage_dir": str(video_dir)}],
        packages_dir=tmp_path / "packages",
    )

    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())

    assert "manifest.json" in names
    assert "a/metadata.json" in names
    assert "a/interactions.json" in names


def test_package_includes_reviewed_subtitles_when_available(tmp_path):
    video_dir = tmp_path / "videos" / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "metadata.json").write_text(json.dumps({"video_id": "a"}), encoding="utf-8")
    (video_dir / "chapters.json").write_text("[]", encoding="utf-8")
    (video_dir / "interactions.json").write_text(
        json.dumps({"version": "1.0", "interactions": []}), encoding="utf-8"
    )
    (video_dir / "subtitles.srt").write_text("original", encoding="utf-8")
    (video_dir / "subtitles_reviewed.srt").write_text("reviewed", encoding="utf-8")

    package_path = create_package(
        videos=[{"id": "a", "title": "A", "source_url": "", "storage_dir": str(video_dir)}],
        packages_dir=tmp_path / "packages",
    )

    with zipfile.ZipFile(package_path) as zf:
        assert zf.read("a/subtitles.srt").decode("utf-8") == "reviewed"


def test_package_falls_back_to_original_subtitles_when_reviewed_missing(tmp_path):
    video_dir = tmp_path / "videos" / "b"
    video_dir.mkdir(parents=True)
    (video_dir / "metadata.json").write_text(json.dumps({"video_id": "b"}), encoding="utf-8")
    (video_dir / "chapters.json").write_text("[]", encoding="utf-8")
    (video_dir / "interactions.json").write_text(
        json.dumps({"version": "1.0", "interactions": []}), encoding="utf-8"
    )
    (video_dir / "subtitles.srt").write_text("original", encoding="utf-8")

    package_path = create_package(
        videos=[{"id": "b", "title": "B", "source_url": "", "storage_dir": str(video_dir)}],
        packages_dir=tmp_path / "packages",
    )

    with zipfile.ZipFile(package_path) as zf:
        assert zf.read("b/subtitles.srt").decode("utf-8") == "original"


def test_package_fallback_to_videos_base_dir_when_storage_dir_empty(tmp_path):
    video_dir = tmp_path / "videos" / "b"
    video_dir.mkdir(parents=True)
    (video_dir / "metadata.json").write_text(json.dumps({"video_id": "b"}), encoding="utf-8")

    package_path = create_package(
        videos=[{"id": "b", "title": "B", "source_url": "", "storage_dir": ""}],
        packages_dir=tmp_path / "packages",
        videos_base_dir=tmp_path / "videos",
    )

    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())

    assert "b/metadata.json" in names


def test_package_skips_video_when_no_storage_dir_and_no_fallback(tmp_path):
    package_path = create_package(
        videos=[{"id": "c", "title": "C", "source_url": "", "storage_dir": ""}],
        packages_dir=tmp_path / "packages",
    )

    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())

    assert "manifest.json" in names
    assert "c/" not in " ".join(names)


def test_packages_created_in_same_second_do_not_overwrite_each_other(tmp_path):
    first = create_package(
        videos=[{"id": "a", "title": "A", "source_url": "", "storage_dir": ""}],
        packages_dir=tmp_path / "packages",
    )
    second = create_package(
        videos=[{"id": "b", "title": "B", "source_url": "", "storage_dir": ""}],
        packages_dir=tmp_path / "packages",
    )

    assert first != second
    assert first.exists()
    assert second.exists()
