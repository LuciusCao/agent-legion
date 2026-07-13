from server.app.db import Database
from server.app.pipeline.openclaw import OpenClawRunner
from server.app.services.video_execution import (
    _default_registry,  # noqa: F401
    build_default_providers,  # noqa: F401
    process_video_once,
)
from server.app.settings import Settings
from server.app.worker_candidates import iter_candidate_pages

from .worker_scheduler import (
    WorkerCapacity,  # noqa: F401
    WorkItem,  # noqa: F401
    get_phase_concurrency_limit,  # noqa: F401
    phase_requires_openclaw,
    pick_next_work,  # noqa: F401
)


def process_next(
    db: Database,
    settings: Settings,
    openclaw_runner: OpenClawRunner | None = None,
) -> bool:
    for videos in iter_candidate_pages(db, settings):
        for video in videos:
            if (
                openclaw_runner is None
                and video["status"] == "queued"
                and phase_requires_openclaw(video["current_phase"])
            ):
                continue
            if video["status"] in {"queued", "missing_url"} and process_video_once(
                db, settings, video["id"], openclaw_runner=openclaw_runner
            ):
                return True
    return False
