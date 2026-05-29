from dataclasses import dataclass
from typing import Any

from server.app.pipeline.phases import AGENT_PHASES
from server.app.settings import Settings

DEFAULT_PHASE_CONCURRENCY = {
    "download": 10,
    "transcribe": 2,
    "assemble": 10,
    "waiting_for_url": 10,
}


@dataclass(frozen=True)
class WorkerCapacity:
    free_runner: Any | None
    running_local_counts: dict[str, int]


@dataclass(frozen=True)
class WorkItem:
    kind: str
    video: Any
    phase: str


def phase_requires_openclaw(phase: str) -> bool:
    return phase in AGENT_PHASES


def get_phase_concurrency_limit(settings: Settings, phase: str) -> int:
    worker_config = settings.config.get("worker", {})
    phase_config = worker_config.get("phase_concurrency", {})
    configured = phase_config.get(phase, DEFAULT_PHASE_CONCURRENCY.get(phase, 1))
    try:
        return max(1, int(configured))
    except (TypeError, ValueError):
        return DEFAULT_PHASE_CONCURRENCY.get(phase, 1)


def pick_next_work(
    videos: list[Any],
    running_video_ids: set[str],
    capacity: WorkerCapacity,
    settings: Settings,
) -> WorkItem | None:
    for video in videos:
        if video["status"] not in {"queued", "missing_url"}:
            continue
        if video["id"] in running_video_ids:
            continue
        phase = video["current_phase"]
        if phase_requires_openclaw(phase):
            if capacity.free_runner is not None:
                return WorkItem(kind="agent", video=video, phase=phase)
            continue
        limit = get_phase_concurrency_limit(settings, phase)
        if capacity.running_local_counts.get(phase, 0) < limit:
            return WorkItem(kind="local", video=video, phase=phase)
    return None
