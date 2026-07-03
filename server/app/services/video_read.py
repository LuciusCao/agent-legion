from collections.abc import Mapping
from typing import Any, cast

from server.app.db import Database
from server.app.pipeline.common import resolve_video_dir
from server.app.services.interaction_stats import _backfill_interaction_stats, _enrich_video
from server.app.settings import Settings


def project_video_storage_dir(video: Mapping[str, Any], settings: Settings) -> dict[str, Any]:
    """Return a response copy with storage_dir resolved under the active data root."""
    projected = dict(video)
    projected["storage_dir"] = str(resolve_video_dir(projected, settings.videos_dir))
    return projected


class VideoReadService:
    """Read-only video enrichment service.

    Derives response-only fields such as ``interaction_stats`` from disk artifacts
    or cached DB columns, but never persists derived state.
    """

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def get_video_detail(self, video_id: str) -> dict[str, Any] | None:
        """Return the enriched video record without writing to the database."""
        video = cast(dict[str, Any] | None, self.db.get_video(video_id))
        if video is None:
            return None
        self._enrich(video)
        return video

    def list_videos(self) -> list[dict[str, Any]]:
        """Return enriched video records without writing to the database."""
        videos = cast(list[dict[str, Any]], self.db.list_videos())
        for video in videos:
            self._enrich(video)
        return videos

    def _enrich(self, video: dict[str, Any]) -> None:
        """Populate derived response fields in place."""
        _enrich_video(video)
        video_dir = resolve_video_dir(video, self.settings.videos_dir)
        video["storage_dir"] = str(video_dir)
        if video.get("content_type") == "knowledge" and "interaction_stats" not in video:
            _backfill_interaction_stats(video, video_dir)
