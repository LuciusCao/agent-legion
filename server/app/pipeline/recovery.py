from server.app.db import Database
from server.app.pipeline.artifacts import clear_artifacts_from
from server.app.pipeline.common import resolve_video_dir
from server.app.settings import Settings


def recover_interrupted_videos(db: Database, settings: Settings) -> int:
    running_videos = [video for video in db.list_videos() if video["status"] == "running"]
    for video in running_videos:
        phase = video["current_phase"]
        if not phase:
            continue
        video_dir = resolve_video_dir(video, settings.videos_dir)
        if video_dir.exists():
            clear_artifacts_from(video_dir, phase, video["id"])
    return db.recover_running_videos()
