from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.pipeline.transcribe import TranscriptionProvider
from server.app.pipeline_worker_thread import PipelineWorkerThread
from server.app.pipelines.definition import PipelineDefinition
from server.app.pipelines.registry import load_registered_pipeline
from server.app.settings import Settings


def make_pipeline_worker(
    tmp_path: Path,
    queries: JobQueries,
    *,
    pipeline_key: str = "reading_analysis",
    pi_binary: str | None = "echo",
    pi_timeout: int = 1,
    with_executors: bool = True,
) -> tuple[PipelineWorkerThread, PipelineDefinition]:
    """Build a configured PipelineWorkerThread for *pipeline_key*."""
    definition = load_registered_pipeline(Path("."), pipeline_key)
    pipelines_config: dict[str, object] = {"enabled": True}
    if pi_binary is not None:
        pipelines_config["pi"] = {"binary": pi_binary, "timeout_seconds": pi_timeout}

    settings = Settings(
        root_dir=Path("."),
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={"pipelines": pipelines_config},
    )

    worker = PipelineWorkerThread(queries, settings)
    worker._definitions = [definition]
    if with_executors:
        worker._local_executor = ThreadPoolExecutor(max_workers=definition.concurrency.local)
        worker._agent_executor = ThreadPoolExecutor(max_workers=definition.concurrency.agent)
    worker._skill_root = tmp_path / "skills"
    worker._skill_root.mkdir(parents=True)

    return worker, definition


class BadProvider(TranscriptionProvider):
    name = "whisper"

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        raise RuntimeError("forced failure")


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
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self, phase: Any, video_id: str, video_dir: Path, prompt_dir: Path, log_path: Path
    ) -> Any:
        self.calls += 1
        phase_key = getattr(phase, "key", str(phase))
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
                "command": ["openclaw", phase_key, video_id],
            },
        )()


class InputItem:
    def __init__(
        self,
        url: str = "",
        title: str = "",
        content_type: str = "knowledge",
        external_id: str = "",
        source_uuid: str = "",
    ):
        self.url = url
        self.title = title
        self.content_type = content_type
        self.external_id = external_id
        self.source_uuid = source_uuid


def setup_spa_app(tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path]:
    """Create a temporary project root/data dir and patch create_app to use them."""
    root_dir = tmp_path / "root"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    from server.app import main as app_main

    def fake_load_settings(
        data_dir: Path | None = None, config_path: Path | None = None
    ) -> Settings:
        resolved = data_dir or tmp_path / "data"
        return Settings(
            root_dir=root_dir,
            data_dir=resolved,
            videos_dir=resolved / "videos",
            logs_dir=resolved / "logs",
            packages_dir=resolved / "packages",
            jobs_dir=resolved / "jobs",
            config={},
        )

    monkeypatch.setattr(app_main, "load_settings", fake_load_settings)
    return root_dir, data_dir


def wait_for_predicate(
    predicate: Callable[[], bool], timeout: float = 5.0, interval: float = 0.01
) -> None:
    """Poll *predicate* until it returns True or *timeout* expires.

    Uses a short sleep between polls; this is acceptable when waiting on
    external thread state, while avoiding arbitrary long sleeps in tests.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("Predicate was not satisfied in time")
        time.sleep(interval)
