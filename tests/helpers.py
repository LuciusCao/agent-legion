import json
from pathlib import Path

from server.app.pipeline.transcribe import TranscriptionProvider
from server.app.settings import Settings


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


class TestProvider(TranscriptionProvider):
    name = "sensevoice"

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        output_path.write_text("1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n", encoding="utf-8")


class ChapterRunner:
    def __init__(self):
        self.calls = 0

    def run(self, phase, video_id, video_dir, prompt_dir, log_path):
        self.calls += 1
        (video_dir / "chapters_raw.json").write_text(
            json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "开始"}]),
            encoding="utf-8",
        )
        (video_dir / "chapters.json").write_text(
            json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "开始"}]),
            encoding="utf-8",
        )
        return type(
            "Result",
            (),
            {
                "status": "completed",
                "error_message": "",
                "command": ["openclaw", "chapter_generate", video_id],
            },
        )()


class InputItem:
    def __init__(
        self,
        url: str = "",
        title: str = "",
        content_type: str = "knowledge",
        external_id: str = "",
    ):
        self.url = url
        self.title = title
        self.content_type = content_type
        self.external_id = external_id


def setup_spa_app(
    tmp_path, monkeypatch, *, root_dir_name="project", data_dir_name="data", config=None
):
    """Create a minimal filesystem layout and patch load_settings so create_app mounts the SPA."""
    from server.app import main

    root_dir = tmp_path / root_dir_name
    data_dir = tmp_path / data_dir_name
    for sub in ("videos", "logs", "packages", "jobs"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)

    cfg = config if config is not None else {}
    default_data_dir = data_dir

    def fake_load_settings(data_dir=None):
        resolved = data_dir if data_dir is not None else default_data_dir
        return Settings(
            root_dir=root_dir,
            data_dir=resolved,
            videos_dir=resolved / "videos",
            logs_dir=resolved / "logs",
            packages_dir=resolved / "packages",
            jobs_dir=resolved / "jobs",
            config=cfg,
        )

    monkeypatch.setattr(main, "load_settings", fake_load_settings)
    return root_dir, data_dir
