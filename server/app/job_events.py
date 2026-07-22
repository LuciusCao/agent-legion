from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

from server.app.event_bus import EventBus, workspace_channel
from server.app.job_dashboard_events import broadcast_workspace_stats_batch
from server.app.job_event_buffer import JobEventBuffer

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)


class _JobQueries(Protocol):
    def list_patch_summaries(
        self, workspace_id: str, job_ids: list[str]
    ) -> list[dict[str, Any]]: ...

    def count_jobs_by_status(self, workspace_id: str) -> dict[str, int]: ...


class WorkspaceJobEventAggregator:
    def __init__(
        self,
        buffer: JobEventBuffer,
        job_queries: _JobQueries,
        bus: EventBus,
    ) -> None:
        self.buffer = buffer
        self.job_queries = job_queries
        self.bus = bus

    def flush_once(self) -> None:
        compacted = self.buffer.drain_compacted()
        workspace_ids = (
            set(compacted.updated_job_ids_by_workspace)
            | set(compacted.created_job_ids_by_workspace)
            | set(compacted.deleted_job_ids_by_workspace)
            | compacted.resync_workspace_ids
        )
        workspace_stats: list[dict[str, Any]] = []
        for workspace_id in sorted(workspace_ids):
            stats = self.job_queries.count_jobs_by_status(workspace_id)
            workspace_stats.append({"id": workspace_id, "job_stats": stats})
            if workspace_id in compacted.resync_workspace_ids:
                self.bus.publish(
                    workspace_channel(workspace_id),
                    build_resync_required_payload(
                        workspace_id,
                        compacted.latest_revision,
                        "event_buffer_overflow",
                    ),
                )
                continue
            changed_ids = sorted(
                compacted.created_job_ids_by_workspace.get(workspace_id, set())
                | compacted.updated_job_ids_by_workspace.get(workspace_id, set())
            )
            deleted_ids = sorted(compacted.deleted_job_ids_by_workspace.get(workspace_id, set()))
            if changed_ids or deleted_ids:
                jobs = self.job_queries.list_patch_summaries(workspace_id, changed_ids)
                self.bus.publish(
                    workspace_channel(workspace_id),
                    build_job_patch_batch_payload(
                        workspace_id=workspace_id,
                        revision=compacted.latest_revision,
                        stats=stats,
                        jobs=jobs,
                        deleted_job_ids=deleted_ids,
                    ),
                )
        broadcast_workspace_stats_batch(
            self.bus,
            compacted.latest_revision,
            workspace_stats,
        )

    async def run(
        self, interval_seconds: float = 0.5, failure_backoff_seconds: float = 1.0
    ) -> None:
        while True:
            try:
                await asyncio.to_thread(self.flush_once)
            except Exception:
                # A transient failure (e.g. DB hiccup) must not kill the event
                # pipeline: log, back off, and keep flushing.
                logger.exception("workspace event flush failed; retrying after backoff")
                await asyncio.sleep(failure_backoff_seconds)
                continue
            await asyncio.sleep(interval_seconds)


def build_workspace_event_aggregator(
    job_db: Any,
    settings: Any,
    bus: EventBus,
) -> tuple[JobEventBuffer, WorkspaceJobEventAggregator]:
    from server.app.services.job_patch_queries import JobPatchQueryService

    query_service = JobPatchQueryService(job_db, settings)
    buffer = JobEventBuffer(db_path=job_db.path)
    aggregator = WorkspaceJobEventAggregator(
        buffer,
        query_service,
        bus,
    )
    return buffer, aggregator


def build_job_patch_batch_payload(
    workspace_id: str,
    revision: int,
    stats: dict[str, int],
    jobs: list[dict[str, Any]],
    deleted_job_ids: list[str],
) -> str:
    return json.dumps(
        {
            "type": "job_patch_batch",
            "workspace_id": workspace_id,
            "revision": revision,
            "stats": stats,
            "jobs": jobs,
            "deleted_job_ids": deleted_job_ids,
        }
    )


def build_resync_required_payload(
    workspace_id: str,
    latest_revision: int,
    reason: str,
) -> str:
    return json.dumps(
        {
            "type": "resync_required",
            "workspace_id": workspace_id,
            "latest_revision": latest_revision,
            "reason": reason,
        }
    )


def record_job_update(
    job_db: JobQueries | None,
    job_event_buffer: Any | None,
    job_id: str,
    workspace_id: str | None = None,
) -> None:
    try:
        if job_event_buffer is None or job_db is None:
            return
        if workspace_id is None:
            job = job_db.get_job(job_id)
            if job is None:
                return
            workspace_id = str(job.get("workspace_id", ""))
        if workspace_id:
            job_event_buffer.record_job_updated(workspace_id, job_id)
    except Exception:
        logger.exception("Failed to record job update for %s", job_id)


def broadcast_job_update(
    job_db: JobQueries | None,
    job_event_manager: Any | None,
    job_id: str,
) -> None:
    try:
        if job_event_manager is None or job_db is None:
            return
        job = job_db.get_job(job_id)
        if job is None:
            return
        workspace_id = str(job.get("workspace_id", ""))
        if not workspace_id:
            return
        stats = job_db.count_jobs_by_status(workspace_id)
        job_event_manager.broadcast_job_updated(workspace_id, job_id, stats)
    except Exception:
        logger.exception("Failed to broadcast job update for %s", job_id)
