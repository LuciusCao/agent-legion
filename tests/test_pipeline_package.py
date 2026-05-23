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
