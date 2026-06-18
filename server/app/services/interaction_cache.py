import json

from server.app.db import Database
from server.app.pipeline.common import resolve_video_dir
from server.app.services.interaction_stats import (
    compute_interaction_review_status,
    compute_interaction_stats,
)
from server.app.settings import Settings


class InteractionCacheService:
    """Explicit write boundary for interaction stats derived from disk artifacts.

    ``refresh(video_id)`` computes the current interaction stats and review status
    from the video directory and persists them to the database cache. Callers must
    invoke this explicitly on producing-phase completion or maintenance actions;
    read paths must never call it.
    """

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def refresh(self, video_id: str) -> None:
        """Compute and persist interaction stats for *video_id*."""
        video = self.db.get_video(video_id)
        if video is None:
            return
        if video.get("content_type") != "knowledge":
            return
        video_dir = resolve_video_dir(video, self.settings.videos_dir)
        stats = compute_interaction_stats(video_dir)
        review_status = compute_interaction_review_status(video_dir)
        self.db.update_video(
            video_id,
            interaction_stats_json=json.dumps(stats, ensure_ascii=False) if stats else "",
            interaction_review_status=review_status or "",
        )
