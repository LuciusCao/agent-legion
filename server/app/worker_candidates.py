from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from server.app.db import Database
from server.app.records import VideoRecord
from server.app.settings import Settings

DEFAULT_POLL_BATCH_SIZE = 100
_WORKER_STATUSES = ["queued", "missing_url", "running"]


def get_poll_batch_size(settings: Settings) -> int:
    worker_config = settings.config.get("worker", {})
    configured = worker_config.get("poll_batch_size", DEFAULT_POLL_BATCH_SIZE)
    try:
        return max(1, int(configured))
    except (TypeError, ValueError):
        return DEFAULT_POLL_BATCH_SIZE


def iter_candidate_pages(db: Database, settings: Settings) -> Iterator[list[VideoRecord]]:
    batch_size = get_poll_batch_size(settings)
    cursor: tuple[str, str] | None = None
    while True:
        videos = _fetch_page(db, batch_size, cursor)
        if not videos:
            return
        yield videos
        if len(videos) < batch_size:
            return
        cursor = _video_cursor(videos[-1])


@dataclass(frozen=True)
class CandidatePage:
    videos: list[VideoRecord]
    wrapped: bool = False
    has_more: bool = False


class WorkerCandidatePager:
    def __init__(self) -> None:
        self._cursor: tuple[str, str] | None = None

    def fetch(self, db: Database, settings: Settings) -> CandidatePage:
        batch_size = get_poll_batch_size(settings)
        videos = _fetch_page(db, batch_size, self._cursor)
        if not videos and self._cursor is not None:
            self._cursor = None
            return CandidatePage([], wrapped=True)
        return CandidatePage(videos, has_more=len(videos) == batch_size)

    def advance(
        self, settings: Settings, videos: list[VideoRecord], selected: VideoRecord | None
    ) -> None:
        cursor_video = selected if selected is not None else videos[-1] if videos else None
        if cursor_video is not None and (
            selected is not None or len(videos) == get_poll_batch_size(settings)
        ):
            self._cursor = _video_cursor(cursor_video)
        else:
            self._cursor = None


def _fetch_page(db: Database, batch_size: int, cursor: tuple[str, str] | None) -> list[VideoRecord]:
    return db.list_videos(
        status_filter=_WORKER_STATUSES,
        limit=batch_size,
        after_created_at=cursor[0] if cursor else None,
        after_id=cursor[1] if cursor else None,
    )


def _video_cursor(video: VideoRecord) -> tuple[str, str]:
    return video["created_at"], video["id"]
