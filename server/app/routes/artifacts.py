from typing import Any

from fastapi import APIRouter, HTTPException

from ..db import Database
from ..pipeline.common import resolve_video_dir
from ..pipeline.reader import read_artifacts
from ..settings import Settings


def create_artifacts_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/videos", tags=["artifacts"])

    @router.get("/{video_id}/artifacts")
    def artifacts(video_id: str) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        return read_artifacts(resolve_video_dir(video, settings.videos_dir))

    return router
