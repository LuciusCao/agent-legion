"""Quality-loop service assembly (issue #190).

The four quality services are wired 1:1 with the router tree and have no
lifecycle of their own, so the composition root hands them to the router
bundle as one prebuilt group instead of constructing them inline in
``main.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.quality_labels import QualityLabelService
from server.app.services.quality_replays import QualityReplayService
from server.app.services.quality_sampling import QualitySamplingService
from server.app.services.quality_stats import QualityStatsService


@dataclass
class QualityLoopDeps:
    """Sampling / labels / stats / replays constructed with shared stores."""

    quality_sampling: QualitySamplingService
    quality_labels: QualityLabelService
    quality_stats: QualityStatsService
    quality_replays: QualityReplayService


def build_quality_loop(
    job_db: JobQueries,
    artifact_store: ArtifactStore,
    object_store: JobArtifactObjectStore | None = None,
) -> QualityLoopDeps:
    return QualityLoopDeps(
        quality_sampling=QualitySamplingService(job_db),
        quality_labels=QualityLabelService(job_db, artifact_store, object_store=object_store),
        quality_stats=QualityStatsService(job_db),
        quality_replays=QualityReplayService(job_db, artifact_store, object_store=object_store),
    )
