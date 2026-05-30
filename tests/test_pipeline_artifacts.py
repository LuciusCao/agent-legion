import json
from pathlib import Path

from server.app.pipeline.artifacts import clear_artifacts_from
from server.app.pipeline.reader import read_artifacts


def test_clear_artifacts_from_keeps_earlier_outputs(tmp_path):
    video_dir = tmp_path / "g1"
    video_dir.mkdir()
    for name in ["g1.mp4", "subtitles.srt", "chapters.json", "metadata.json"]:
        (video_dir / name).write_text("x", encoding="utf-8")

    clear_artifacts_from(video_dir, "transcribe", "g1")

    assert (video_dir / "g1.mp4").exists()
    assert not (video_dir / "subtitles.srt").exists()
    assert not (video_dir / "chapters.json").exists()
    assert not (video_dir / "metadata.json").exists()


def test_read_artifacts_includes_checklist(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8"
    )
    (video_dir / "interactions.json").write_text(json.dumps({"interactions": []}), encoding="utf-8")
    (video_dir / "chapters.json").write_text(json.dumps({"chapters": []}), encoding="utf-8")
    (video_dir / "checklist.json").write_text(
        json.dumps({"video_id": "v1", "checklist": {"content_usability": {"issues": []}}}),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"score": 100, "status": "published"}), encoding="utf-8"
    )

    result = read_artifacts(video_dir)
    assert result["checklist"] is not None
    assert result["checklist"]["checklist"]["content_usability"]["issues"] == []
    assert result["review"] is not None
    assert result["review"]["score"] == 100
