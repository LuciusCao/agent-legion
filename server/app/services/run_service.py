"""RunService: create runs from items (materials-and-runs design §4, slice 3).

An item is the terminal intake state — a ready material upload or an external
reference — so creation skips the legacy RESOLVERS/source_kind validation
entirely while reusing the intake skeleton: node config freeze, code version
pins, dedup against existing jobs, deterministic run id digest, one job per
item. The legacy ``/job-batches`` path keeps flowing through
``JobIntakeService``; both creation paths stay fully usable until the intake
retirement slice.

#467 A1–A5 (chunked submit): the per-item DB round trips of the old
``_resolve_items`` (one SELECT per material/bundle/ref item) are replaced by
chunked set-based existence probes, the dedup scan loads only this request's
keys, job insertion commits in bounded chunks, and the create response no
longer materializes job rows (run id + created_count; the detail endpoint and
#358's counter tables carry the rest).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg

from server.app.db.rowmap import iso_optional, parse_object
from server.app.events import JobEventManager
from server.app.jobs import JobQueries
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.agent_service import published_agent_definitions
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.job_intake_workspace import get_workspace
from server.app.services.node_code_resolution import freeze_node_code_versions
from server.app.services.node_config import resolve_workflow_node_configs
from server.app.services.run_item_resolution import resolve_run_items
from server.app.services.run_item_types import validate_run_item_types
from server.app.services.run_partial_failure import (
    PartialRunCreationError,
    partial_failure_message,
)
from server.app.settings import Settings
from server.app.workflows.definition import workflow_definition_from_dict

logger = logging.getLogger(__name__)

# Runs created from items carry this marker in source_kind; legacy rows keep
# their intake source_kind for display (design §5.2).
ITEMS_SOURCE_KIND = "items"

# Event-record chunk (#467 A5): the buffer itself batches revisions; this
# only bounds the transient id list per record call.
_EVENT_RECORD_CHUNK = 500


def _run_record(row: dict[str, Any]) -> dict[str, Any]:
    """Public run record; queue_payload (async intake working state) stays internal."""
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "workflow_key": str(row["workspace_id"]),
        "source_kind": str(row["source_kind"]),
        "status": str(row["status"]),
        "created_count": int(row["created_count"]),
        "error_message": str(row["error_message"] or ""),
        "frozen_pins": parse_object(row.get("frozen_pins_json")),
        "stats": parse_object(row.get("stats_json")),
        "created_by": str(row["created_by"] or ""),
        "created_at": iso_optional(row["created_at"]),
        "updated_at": iso_optional(row["updated_at"]),
    }


class RunService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
    ):
        self.job_db = job_db
        self.settings = settings
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer

    def create_run(
        self,
        workspace_id: str,
        *,
        workflow_key: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        workspace = get_workspace(self.job_db, workspace_id)
        active_revision = self.job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if active_revision is None:
            raise InvalidOperationError(
                "Workspace has no active workflow revision; publish a workflow revision first"
            )
        definition = workflow_definition_from_dict(json.loads(active_revision["definition_json"]))
        if not items:
            raise InvalidOperationError("At least one item is required")
        max_items = self.settings.executor_runtime.workflows.max_items_per_run
        if max_items and len(items) > max_items:
            raise InvalidOperationError(
                f"Run items exceed the per-run limit: {len(items)} > {max_items}."
                " Split the submission into smaller runs (the limit is"
                " workflows.max_items_per_run in instance settings)."
            )
        validate_run_item_types(definition, items)
        # Runtime profile (#359): intake-stage rate gauges. Counted after the
        # cheap contract checks but before the heavy resolution/insert path,
        # so the gauge measures submitted load (what a campaign operator
        # pacing batches cares about), not just accepted load.
        from server.app.services.runtime_profile import profile

        profile.note_run_intake(len(items))

        # Validate everything (items, node config, pins) before the first
        # write so a rejected request leaves no half-created run behind.
        candidates = resolve_run_items(self.job_db, workspace_id, items)
        try:
            node_config = resolve_workflow_node_configs(
                definition,
                published_agent_definitions(self.job_db, workspace_id),
                workspace,
            )
        except ValueError as exc:
            raise InvalidOperationError(f"Invalid node configuration: {exc}") from exc
        node_code_versions = freeze_node_code_versions(
            self.job_db,
            self.settings.executor_runtime.workflows.custom_nodes_enabled,
            workspace_id,
            workflow_key,
            list(definition.executable_nodes),
        )

        # Same dedup contract as intake: items whose (source_type, source_id)
        # already has a job in this workflow drop out; accepted keys grow the
        # set so intra-request duplicates filter exactly like pre-existing jobs.
        # #467 A2: point lookups over this request's keys (indexed IN probes)
        # instead of loading the whole workspace's keys — same workspace-
        # scoped semantics, cost tracks the submission size.
        existing_keys = self.job_db.filter_existing_dedup_keys(
            workspace_id,
            (
                (str(candidate["entity_type"]), str(candidate["entity_id"]))
                for candidate in candidates
            ),
        )
        fresh: list[dict[str, Any]] = []
        for candidate in candidates:
            key = (str(candidate["entity_type"]), str(candidate["entity_id"]))
            if key in existing_keys:
                continue
            existing_keys.add(key)
            fresh.append(candidate)
        if not fresh:
            raise InvalidOperationError("No tasks were resolved from input")

        digest_payload = {
            "workflow_key": workflow_key,
            "source_kind": ITEMS_SOURCE_KIND,
            "items": items,
            "node_config": node_config,
        }
        run = self.job_db.create_run(
            workflow_key,
            ITEMS_SOURCE_KIND,
            digest_payload,
            workspace_id=workspace_id,
            frozen_pins={"node_code_versions": node_code_versions},
        )
        try:
            job_ids = self.job_db.create_jobs_bulk(
                candidates=fresh,
                workflow_key=workflow_key,
                run_id=run["id"],
                node_keys=list(definition.executable_nodes),
                workspace_id=workspace_id,
                revision=active_revision,
                frozen_config=node_config,
            )
        except Exception as exc:
            # #204 broad-except audit: compensate-then-re-raise after the
            # run row committed (create_jobs_bulk commits in chunks, so the
            # failure space is the bulk-insert's mixed psycopg/dedup
            # surface). The width is required because the compensation must
            # run on EVERY failure mode — ValueError (normalize-collision,
            # converted to the user-facing InvalidOperationError) and
            # unexpected errors alike — or a run row would linger; nothing is
            # masked: the non-ValueError branch is a bare re-raise preserving
            # type and traceback, and the failure bookkeeping below is
            # itself #204-audited to never mask this error.
            # A fresh item can still collide at insert time: two items can
            # normalize to the same job id (``a/b`` vs ``a_w``), or an item
            # can hit a legacy-path job with a different source_type but the
            # same source_id. Chunked commits (#467 A3) change the outcome
            # only when a chunk already committed: the partial run STAYS
            # (delete_run_without_jobs's not-exists guard is a no-op) and is
            # marked failed with its progress so the operator sees what was
            # created; a resubmission resumes through the dedup filter.
            # With no committed chunk the compensation removes the run row
            # exactly like the pre-chunking shape.
            try:
                committed = self.job_db.count_jobs_in_run(str(run["id"]))
            except Exception:
                # #204 broad-except audit: progress bookkeeping inside the
                # compensate-then-re-raise path — a failure here must not
                # mask the original creation error, so it degrades to 0
                # (the empty-run compensation branch) and logs. Nothing is
                # swallowed downstream: the original exception still raises.
                logger.exception("run %s progress count failed", run["id"])
                committed = 0
            if committed > 0:
                self._mark_partial_run_failed(str(run["id"]), committed, exc)
                # #467 review P1-2/P2-1: EVERY failure mode after a committed
                # chunk maps to the structured partial-failure error — the
                # operator legibility requirement (created_so_far in the 400
                # detail) does not depend on the exception family. The
                # original exception rides along as __cause__; with no
                # committed chunk the branches below keep the pre-chunking
                # semantics verbatim (ValueError → 400, anything else →
                # bare re-raise → 500).
                raise PartialRunCreationError(
                    partial_failure_message(committed, exc),
                    run_id=str(run["id"]),
                    created_so_far=committed,
                ) from exc
            self._discard_empty_run(str(run["id"]))
            if isinstance(exc, ValueError):
                raise InvalidOperationError(str(exc)) from exc
            raise
        if job_ids:
            notify_schedulable_work()
        # Persist the final creation progress so the run row alone answers
        # the detail endpoint (legacy sync intake kept this only in memory).
        # #467 review P1-2: created_count is the run's WHOLE job slice
        # (count_jobs_in_run, accumulating — the intake queue's semantics),
        # not this request's inserts: a resubmission that resumes a
        # partially-failed run must heal the row (failed→created via the
        # upsert, count back to the run total, error_message cleared).
        run = self.job_db.update_intake_run(
            str(run["id"]),
            created_count=self.job_db.count_jobs_in_run(str(run["id"])),
            status=str(run["status"]),
        )

        if self.job_event_buffer is not None:
            # #467 A5: chunked record — the buffer is the batching layer (one
            # lock acquisition + one revision per call, SSE payloads
            # aggregated by the drain loop), so recording per insert chunk
            # keeps memory bounded for very large runs while the wire stays
            # on the compacted batch path.
            for start in range(0, len(job_ids), _EVENT_RECORD_CHUNK):
                self.job_event_buffer.record_jobs_created(
                    workspace_id, job_ids[start : start + _EVENT_RECORD_CHUNK]
                )
        elif self.job_event_manager is not None:
            # Bufferless fallback (tests/legacy wiring): broadcast ids only —
            # the create response no longer carries job rows (#467 A4), and
            # the manager's job_patch_batch consumers key off ids. Stats stay
            # empty: the aggregator's periodic flush owns the numbers on the
            # buffered path, and this fallback's SSE consumers re-read them.
            self.job_event_manager.broadcast_jobs_created(
                workspace_id, [{"id": job_id} for job_id in job_ids], {}
            )
        # #467 A4: the response carries run + created_count only; job rows
        # moved to the read paths (run detail + paginated job list), so a
        # 万级-items run no longer serializes a proportional JSON payload
        # inside the request thread.
        return {"run": _run_record(run), "created_count": len(job_ids), "job_ids": job_ids}

    def _mark_partial_run_failed(self, run_id: str, committed: int, exc: Exception) -> None:
        """Record the partial outcome on the run row (operator legibility).

        Mirrors the async intake queue's chunk-error bookkeeping (status
        "failed" + error_message), no new state values. Best-effort: a DB
        failure here must not mask the original creation error.
        """
        try:
            self.job_db.update_intake_run(
                run_id,
                created_count=committed,
                status="failed",
                error_message=partial_failure_message(committed, exc),
            )
        except (OSError, psycopg.Error) as exc2:
            # #204: same compensation-only catch as _discard_empty_run.
            logger.warning("run %s partial-failure marking failed: %s", run_id, exc2)

    def _discard_empty_run(self, run_id: str) -> None:
        # Best-effort cleanup of the run row after job creation failed;
        # never mask the original failure.
        try:
            self.job_db.delete_run_without_jobs(run_id)
        except (OSError, psycopg.Error) as exc:
            # #204: the compensation is one guarded DELETE via the JobQueries
            # facade — a DB connectivity failure here must not mask the
            # original creation error. Programming errors propagate (the
            # facade is exercised by every create_run test).
            logger.warning("run %s left orphaned after job creation failed: %s", run_id, exc)

    def list_runs(self, workspace_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.job_db.list_runs(workspace_id, limit)
        return [_run_record(row) for row in rows]

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        row = self.job_db.get_run(run_id)
        if row is None or str(row["workspace_id"]) != workspace_id:
            raise NotFoundError("Run not found")
        by_status = self.job_db.count_jobs_by_status_in_run(run_id)
        return {
            "run": _run_record(row),
            "job_stats": {"total": sum(by_status.values()), "by_status": by_status},
        }
