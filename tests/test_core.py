import json
import os
import zipfile
from pathlib import Path

from server.app.db import Database
from server.app.pipeline.artifacts import clear_artifacts_from
from server.app.pipeline.common import get_video_id, parse_srt
from server.app.pipeline.openclaw import AgentPhase, OpenClawRunner
from server.app.pipeline.package import create_package
from server.app.pipeline.transcribe import (
    TranscriptionProvider,
    run_transcription_with_providers,
    validate_srt,
)
from server.app.settings import load_env_file


class BadProvider(TranscriptionProvider):
    name = "whisper"

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        output_path.write_text("", encoding="utf-8")


class GoodProvider(TranscriptionProvider):
    name = "sensevoice"

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        output_path.write_text(
            "1\n00:00:00,000 --> 00:00:10,000\n第一段讲解。\n\n"
            "2\n00:00:10,000 --> 00:00:20,000\n第二段讲解。\n",
            encoding="utf-8",
        )


def test_database_creates_video_and_phase_run(tmp_path):
    db = Database(tmp_path / "app.sqlite")

    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    run = db.start_phase(video["id"], "download", ["python3", "download.py"])
    db.finish_phase(run["id"], "completed", 0, "")

    videos = db.list_videos()
    runs = db.list_phase_runs(video["id"])

    assert videos[0]["id"] == "a"
    assert videos[0]["status"] == "completed"
    assert runs[0]["phase_key"] == "download"
    assert runs[0]["exit_code"] == 0


def test_parse_srt_and_video_id():
    assert get_video_id("https://cdn.example.com/videos/g02060101.mp4?x=1") == "g02060101"

    subtitles = parse_srt(
        "1\n00:00:00,000 --> 00:00:01,500\n你好\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\n继续\n"
    )

    assert subtitles == [
        {"index": 1, "start": 0.0, "end": 1.5, "text": "你好"},
        {"index": 2, "start": 1.5, "end": 3.0, "text": "继续"},
    ]


def test_load_env_file_preserves_quoted_secret_values(tmp_path, monkeypatch):
    monkeypatch.delenv("BASECMS_SECRET", raising=False)
    monkeypatch.setenv("BASECMS_TOKEN", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text(
        'BASECMS_TOKEN="from-file"\n'
        'BASECMS_SECRET="fake#secret$value"\n',
        encoding="utf-8",
    )

    load_env_file(env_file)

    assert os.environ["BASECMS_TOKEN"] == "already-set"
    assert os.environ["BASECMS_SECRET"] == "fake#secret$value"


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


def test_transcribe_auto_falls_back_to_sensevoice(tmp_path):
    video_path = tmp_path / "a.mp4"
    video_path.write_bytes(b"fake")

    result = run_transcription_with_providers(
        video_path=video_path,
        output_dir=tmp_path,
        title="A",
        duration=20,
        mode="auto",
        providers=[BadProvider(), GoodProvider()],
    )

    assert result.provider == "sensevoice"
    assert result.srt_entry_count == 2
    assert "fallback" in result.validation_summary
    assert validate_srt((tmp_path / "subtitles.srt").read_text(encoding="utf-8"), 20).ok


def test_openclaw_runner_executes_template_and_validates_json(tmp_path):
    command = [
        "python3",
        "-c",
        (
            "import json, pathlib, sys; "
            "out=pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); "
            "(out/'interactions.json').write_text(json.dumps({'version':'1.0','interactions':[]}), encoding='utf-8')"
        ),
        "{video_dir}",
    ]
    runner = OpenClawRunner(command_template=command, cwd=tmp_path, timeout_seconds=10)
    phase = AgentPhase(
        key="interaction_generate",
        reference_path=tmp_path / "reference.md",
        expected_outputs=["interactions.json"],
        json_outputs=["interactions.json"],
    )
    (tmp_path / "reference.md").write_text("Generate interactions.", encoding="utf-8")

    result = runner.run(
        phase=phase,
        video_id="a",
        video_dir=tmp_path / "video",
        prompt_dir=tmp_path / "prompts",
        log_path=tmp_path / "run.log",
    )

    assert result.status == "completed"
    assert (tmp_path / "video" / "interactions.json").exists()


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
