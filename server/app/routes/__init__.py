from fastapi import APIRouter

from ..agents import AgentStatusManager
from ..db import Database
from ..events import VideoEventManager
from ..settings import Settings
from .agents import create_agents_router
from .artifacts import create_artifacts_router
from .common import create_common_router
from .packages import create_packages_router
from .videos import create_videos_router


def create_router(
    db: Database,
    settings: Settings,
    agent_manager: AgentStatusManager,
    video_event_manager: VideoEventManager,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    router.include_router(create_common_router(db, settings))
    router.include_router(create_agents_router(agent_manager))
    router.include_router(create_videos_router(db, settings, agent_manager, video_event_manager))
    router.include_router(create_artifacts_router(db, settings))
    router.include_router(create_packages_router(db, settings))

    return router
