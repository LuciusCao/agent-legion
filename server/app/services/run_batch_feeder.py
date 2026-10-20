"""RunBatchFeeder (#1117): drip-feeds async runs' items into jobs.

The intake-queue precedent (``job_intake_queue.JobIntakeQueue``) mapped onto
the runs item families: one claimed batch per tick, one bounded chunk per
tick, chunk-level error containment, batch-level failure containment, stale
claim reclaim. The differences are the candidate resolver family
(``resolve_run_items`` — material/bundle/ref (+ text materialization), NOT
the intake RESOLVERS registry) and the watermark gate (below). Design doc:
docs/architecture/runs-async-mode.md.

Explicitly NO process pool: the loop is one in-process coroutine with
``asyncio.to_thread`` isolation (run_batch_background); the bottleneck is DB
round trips, not CPU.

Crash/replay idempotency: a chunk that committed jobs but died before the
cursor advance is replayed on the stale reclaim; the per-chunk dedup probe
plus create_jobs_bulk's ON CONFLICT ownership absorb the re-walk, and
``jobs_created`` is recomputed as ``count_jobs_in_run`` at every advance
rather than incremented.

runs.status discipline: async runs only ever take created/completed/failed —
never queued/processing, which ``claim_intake_run`` would pick up as an
intake batch. Feeding progress lives on the batch row and runs.created_count.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from server.app.jobs import JobQueries
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.job_creation_pipeline import JobCreationPipeline
from server.app.services.run_item_resolution import resolve_run_items
from server.app.services.run_text_items import is_text_item, materialize_text_items
from server.app.settings import Settings
from server.app.workflows.definition import workflow_definition_from_dict

logger = logging.getLogger(__name__)

# Per-tick item chunk. The intake queue's 25 is sized for external CMS calls;
# item resolution is local chunked probes, so a larger chunk amortizes the
# claim/update round trips while create_jobs_bulk still commits in its own
# bounded sub-chunks.
RUN_BATCH_CHUNK_SIZE = 500
# Workspace-wide drip watermark (#505 CLI semantics): stop feeding while the
# workspace's non-terminal job backlog is at/above this level; clamped by
# workflows.max_items_per_run so the instance's per-run cap stays the tighter
# bound (0 = unlimited → bare 30000).
RUN_BATCH_WATERMARK = 30_000
MAX_CHUNK_ERRORS = 20


class RunBatchFeeder:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        job_event_buffer: Any | None = None,
        materials_service: Any | None = None,
    ) -> None:
        self.job_db = job_db
        self.settings = settings
        self.materials_service = materials_service
        # #724: dedup probe / insert / notify / event record are the shared
        # pipeline stages (buffer-only event wiring, the queue's precedent).
        self.pipeline = JobCreationPipeline(job_db, settings, job_event_buffer=job_event_buffer)

    def _watermark(self) -> int:
        cap = int(self.settings.executor_runtime.workflows.max_items_per_run or 0)
        return min(RUN_BATCH_WATERMARK, cap) if cap > 0 else RUN_BATCH_WATERMARK

    def _workspace_backlog(self, workspace_id: str) -> int:
        counts = self.job_db.count_jobs_by_status(workspace_id)
        # count_jobs_by_status renames queued→pending for display.
        return int(counts.get("pending", 0)) + int(counts.get("running", 0))

    def consume_once(self) -> bool:
        batch = self.job_db.claim_run_batch()
        if batch is None:
            return False
        try:
            return self._consume_chunk(batch)
        except Exception as exc:
            # #204 broad-except audit: batch-level containment, same shape as
            # JobIntakeQueue.consume_once. _consume_chunk's inner catch
            # already contains per-chunk item failures; what reaches here is
            # the batch bookkeeping itself failing (corrupt target_spec_json,
            # a workflow revision deleted after accept, a DB failure mid
            # status update). The claimed batch must be recorded failed (or
            # it would be re-claimed forever); if the fail-write itself
            # raises, that propagates to the background loop's backoff arm
            # and the row stays claimable for a retry — both correct.
            logger.exception("run batch %s failed", batch["run_id"])
            message = str(exc)[:1000]
            self.job_db.fail_run_batch(str(batch["run_id"]), message)
            self.job_db.update_intake_run(
                str(batch["run_id"]), status="failed", error_message=message
            )
        return True

    def _consume_chunk(self, batch: dict[str, Any]) -> bool:
        """One chunk of the claimed batch; False = nothing fed (watermark gate)."""
        run_id = str(batch["run_id"])
        workspace_id = str(batch["workspace_id"])
        payload = json.loads(str(batch["target_spec_json"]))
        items = payload["items"]
        start = int(batch["next_index"])
        end = min(start + RUN_BATCH_CHUNK_SIZE, len(items))

        # Watermark gate: release the claim (back to pending) and report
        # no-work so the loop idles instead of hot-spinning on a saturated
        # workspace; claim ordering by updated_at round-robins the backlog
        # across workspaces instead of starving later batches.
        if self._workspace_backlog(workspace_id) >= self._watermark():
            self.job_db.transition_run_batch(
                run_id, from_statuses=("running",), to_status="pending"
            )
            return False

        revision = self.job_db.get_workflow_revision(
            workspace_id,
            # #211 M2: runs carry no workflow_key; the workspace id is the
            # workflow identifier (intake consumer precedent).
            workspace_id,
            str(payload["workflow_revision_id"]),
        )
        if revision is None:
            raise ValueError("Queued workflow revision no longer exists")
        definition = workflow_definition_from_dict(json.loads(revision["definition_json"]))

        chunk_errors = json.loads(str(batch["chunk_errors_json"] or "[]"))
        try:
            self._create_chunk_jobs(batch, payload, definition, revision, items[start:end])
        except Exception as exc:
            # A bad chunk (unknown material, disabled connection, text
            # materialization failure, DB hiccup mid-insert) must not fail
            # the whole batch: record the error, skip these items, let the
            # remaining chunks proceed — the intake consumer's chunk contract
            # verbatim (jobs already committed stay; the skipped items can be
            # re-submitted in a new run, dedup filters the created ones).
            # #204 broad-except audit: the resolver/materialization space is
            # genuinely mixed (InvalidOperationError/NotFoundError contract
            # errors, storage transport errors, psycopg surfaces) and all
            # take the same record-and-skip path because the cursor is the
            # recovery mechanism; the outer catch contains what this misses.
            logger.exception("run batch %s chunk [%s:%s] failed", run_id, start, end)
            if len(chunk_errors) < MAX_CHUNK_ERRORS:
                chunk_errors.append(
                    {
                        "chunk_start": start,
                        "error": str(exc)[:300],
                    }
                )

        done = end >= len(items)
        error_message = "; ".join(
            f"chunk {error['chunk_start']}: {error['error']}" for error in chunk_errors[-5:]
        )[:1000]
        advanced = self.job_db.advance_run_batch(
            run_id,
            next_index=end,
            jobs_created=self.job_db.count_jobs_in_run(run_id),
            chunk_errors=chunk_errors,
            error_message=error_message,
            done=done,
        )
        if advanced is None:
            # Pause/cancel landed mid-chunk (advance CASes on 'running'):
            # keep the operator intent; the committed chunk replays
            # idempotently on resume via the dedup probe.
            return True
        # Mirror progress onto the run row (the detail endpoint's handle):
        # created while feeding, completed at cursor exhaustion — never
        # queued/processing, which claim_intake_run would pick up.
        self.job_db.update_intake_run(
            run_id,
            created_count=int(advanced["jobs_created"]),
            status="completed" if done else "created",
            error_message=error_message,
        )
        return True

    def _create_chunk_jobs(
        self,
        batch: dict[str, Any],
        payload: dict[str, Any],
        definition: Any,
        revision: dict[str, Any],
        chunk: list[dict[str, Any]],
    ) -> None:
        workspace_id = str(batch["workspace_id"])
        # Text items materialize per chunk (content-hash idempotent), then the
        # whole chunk resolves through the run item families — the sync path's
        # two-phase probe order exists to fail the REQUEST fast; the feeder's
        # equivalent containment is the chunk error record.
        if any(is_text_item(item) for item in chunk):
            chunk = materialize_text_items(
                self.job_db,
                self.materials_service,
                workspace_id,
                chunk,
                created_by=str(batch["created_by"] or ""),
                default_filename=str(payload.get("text_default_filename") or ""),
            )
        candidates = resolve_run_items(self.job_db, workspace_id, chunk)
        fresh = self.pipeline.collect_fresh_candidates(workspace_id, candidates)
        if not fresh:
            return
        job_ids = self.pipeline.insert_jobs(
            candidates=fresh,
            workflow_key=workspace_id,
            run_id=str(batch["run_id"]),
            definition=definition,
            workspace_id=workspace_id,
            revision=revision,
            node_config=payload.get("node_config") or {},
            notify=notify_schedulable_work,
        )
        if job_ids:
            self.pipeline.record_jobs_created(workspace_id, [str(job_id) for job_id in job_ids])
