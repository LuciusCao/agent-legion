from pathlib import Path

from server.app.db import Database
from server.app.pipeline.artifacts import clear_artifacts_from
from server.app.settings import Settings


def recover_interrupted_videos(db: Database, settings: Settings) -> int:
    running_videos = [video for video in db.list_videos() if video["status"] == "running"]
    for video in running_videos:
        phase = video["current_phase"]
        if not phase:
            continue
        video_dir = (
            Path(video["storage_dir"])
            if video["storage_dir"]
            else settings.videos_dir / video["id"]
        )
        if video_dir.exists():
            clear_artifacts_from(video_dir, phase, video["id"])
    return db.recover_running_videos()
