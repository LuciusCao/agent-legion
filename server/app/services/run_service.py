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
#358's counter tables carry the rest). #735 restores the bare ``job_ids``
string list on the response — ids only, the row-slimming contract of #467 A4
stays in force (regression-pinned by tests). #735 review P1: the response's
job_ids/created_count derive from the bulk INSERT's ownership RETURNING (the
post-write truth), never from the pre-insert candidate list — two concurrent
submissions racing the same item past the dedup probe are arbitrated by the
ON CONFLICT ownership clause (first writer wins, ownership is never
re-bound), so a response can never claim a job another run owns, and the
GET /jobs run_id filter stays consistent with every returned id.
#1157 phase0 adds the scalar ``skipped_count`` (submitted − created): items
filtered by the dedup probe plus items a concurrent run claimed first, so the
client can show 「新建 N 跳过 M」 without a second round trip.
#724: the creation sequence itself (dedup probe → node setup → version
freeze → create_run → create_jobs_bulk → #501 compensation → notify → event
record) is shared with both intake paths via ``JobCreationPipeline``
(job_creation_pipeline); this module keeps only the /runs-specific value
provenance (items validation, text-item materialization, digest + #501 heal
branch, progress persistence, response assembly).
#1117: ``create_run_async`` adds the async accept mode (202 + run_batches
handle); the shared preamble lives in ``prepare_submission``, the accept and
the pause/resume/cancel control plane in ``run_batch``, the drip-feed
consumption in ``run_batch_feeder`` (docs/architecture/runs-async-mode.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.db.rowmap import iso_optional, parse_object
from server.app.events import JobEventManager
from server.app.jobs import JobQueries
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services import run_batch
from server.app.services.job_creation_pipeline import JobCreationPipeline
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.run_item_resolution import create_run_response, resolve_run_items
from server.app.services.run_text_items import is_text_item, materialize_text_items
from server.app.settings import Settings

if TYPE_CHECKING:
    from server.app.services.materials import MaterialsService

# Runs created from items carry this marker in source_kind; legacy rows keep
# their intake source_kind for display (design §5.2).
ITEMS_SOURCE_KIND = "items"


def _run_record(row: dict[str, Any]) -> dict[str, Any]:
    """Public run record; queue_payload (async intake working state) stays internal."""
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
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
        materials_service: MaterialsService | None = None,
    ):
        self.job_db = job_db
        self.settings = settings
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer
        # Object store seam for ``text`` items (run_text_items); None keeps
        # text submissions failing closed with 503 like the materials API.
        self.materials_service = materials_service
        # #724: the creation sequence itself (dedup probe → node setup →
        # version freeze → create_run → create_jobs_bulk → #501 compensation
        # → notify → event record) is shared with both intake paths.
        self.pipeline = JobCreationPipeline(
            job_db,
            settings,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        )

    def create_run_async(
        self,
        workspace_id: str,
        *,
        workflow_key: str,
        items: list[dict[str, Any]],
        created_by: str = "",
    ) -> dict[str, Any]:
        """Async accept (#1117): 202 handle = run record + batch record.

        The heavy stages (per-item resolution, dedup, job insertion) run in
        the feeder; accept persists the deterministic run row (same digest as
        sync) plus the run_batches state row, and blind retries heal terminal
        batches via guarded resets (run_batch.accept_async_run).
        """
        return run_batch.accept_async_response(
            self, workspace_id, workflow_key=workflow_key, items=items, created_by=created_by
        )

    def create_run(
        self,
        workspace_id: str,
        *,
        workflow_key: str,
        items: list[dict[str, Any]],
        created_by: str = "",
    ) -> dict[str, Any]:
        # #1117: the submission preamble (workspace/revision/definition +
        # cheap fail-fast item checks) is shared with the async accept path
        # and lives in run_batch.prepare_submission (file budget).
        workspace, active_revision, definition, items = run_batch.prepare_submission(
            self, workspace_id, workflow_key, items
        )
        # Validate everything (items, node config, pins) before the first
        # write so a rejected request leaves no half-created run behind.
        # Text items are the one exception: they become materials after the
        # read-only checks (see run_text_items), so the stored items are
        # probed first and the full list re-resolved once texts exist.
        stored_items = [item for item in items if not is_text_item(item)]
        candidates = resolve_run_items(self.job_db, workspace_id, stored_items)
        node_config, node_code_versions = self.pipeline.resolve_node_setup(
            definition, workspace, workspace_id, workflow_key
        )
        if len(stored_items) != len(items):
            start_node = definition.start_node
            text_input = start_node.text_input if start_node is not None else None
            candidates = resolve_run_items(
                self.job_db,
                workspace_id,
                materialize_text_items(
                    self.job_db,
                    self.materials_service,
                    workspace_id,
                    items,
                    created_by=created_by,
                    default_filename=text_input.filename if text_input is not None else "",
                ),
            )

        # Same dedup contract as intake: items whose (source_type, source_id)
        # already has a job in this workflow drop out; accepted keys grow the
        # set so intra-request duplicates filter exactly like pre-existing
        # jobs (the probe's check-then-act race note lives on the pipeline).
        fresh = self.pipeline.collect_fresh_candidates(workspace_id, candidates)
        digest_payload = {
            "workflow_key": workflow_key,
            "source_kind": ITEMS_SOURCE_KIND,
            "items": items,
            "node_config": node_config,
        }
        if not fresh:
            # #501：全量重复提交不再一律 400——同 digest 的 failed run（上次
            # 分块提交中途失败、所有 job 已由重试/他路补齐的场景）用同一
            # 确定性 run id 治愈：status 回 created、created_count 对齐
            # run 实际 job 数、error_message 清空，返回 created_count=0。
            # digest 相同意味着 items 逐字节相同——run id 与失败那次是同一
            # 行；无 failed run（首次全重复/重复提交已成功的 run）保持
            # 「No tasks were resolved」的现行为。
            healed = self.job_db.heal_failed_run_if_duplicate(
                workspace_id, workflow_key, ITEMS_SOURCE_KIND, digest_payload
            )
            if healed is not None:
                # 治愈路径上全部条目都是重复（fresh 为空才走到这里）。
                return create_run_response(_run_record(healed), [], len(items))
            # 文案是对外机器契约（docs/workspace-api-tokens.md 教调用方精确
            # 相等判断「已存在」），不得改动或追加计数后缀；人读侧的可读化
            # 由前端 toast 承担（#1157 phase0，tower 裁决 A）。
            raise InvalidOperationError("No tasks were resolved from input")

        run, job_ids = self.pipeline.create_run_and_jobs(
            workspace_id=workspace_id,
            workflow_key=workflow_key,
            source_kind=ITEMS_SOURCE_KIND,
            source_payload=digest_payload,
            node_code_versions=node_code_versions,
            candidates=fresh,
            revision=active_revision,
            definition=definition,
            node_config=node_config,
            notify=notify_schedulable_work,
        )
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

        self.pipeline.record_jobs_created(workspace_id, job_ids)
        # #467 A4: the response carries run + created_count plus job_ids only
        # (#735 re-added the id list so external callers can poll #703's
        # per-job endpoints right after submit — ids, never job rows); a
        # 万级-items run still does not serialize a proportional JSON payload
        # inside the request thread.
        # #735 review P1: ``job_ids`` here is the bulk INSERT's ownership
        # truth — candidates a concurrent run claimed first (between the
        # dedup probe above and the insert) were skipped by the ON CONFLICT
        # ownership clause and are absent, so created_count == len(job_ids)
        # is this run's real acquisition and every returned id resolves
        # under GET /jobs?run_id=<this run>. When EVERY candidate loses the
        # race the run keeps created_count=0 with an empty id list — the
        # truthful shape of "a concurrent run won all of these items" (the
        # serialized equivalent, the dedup probe's 400/heal branch, never
        # applies to an in-flight race).
        # #1157 phase0: skipped_count 由 create_run_response 组装
        # （submitted − created，语义见其 docstring）。
        return create_run_response(_run_record(run), job_ids, len(items))

    def list_runs(self, workspace_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.job_db.list_runs(workspace_id, limit)
        return [_run_record(row) for row in rows]

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        row = self.job_db.get_run(run_id)
        if row is None or str(row["workspace_id"]) != workspace_id:
            raise NotFoundError("Run not found")
        by_status = self.job_db.count_jobs_by_status_in_run(run_id)
        # #1117: async runs carry their feeder handle on the detail payload —
        # the polling loop reads batch progress + job_stats from one response.
        batch = run_batch.get_run_batch_for_run(self.job_db, workspace_id, run_id)
        return {
            "run": _run_record(row),
            "job_stats": {"total": sum(by_status.values()), "by_status": by_status},
            "batch": run_batch.batch_record(batch) if batch is not None else None,
        }

    def pause_run_batch(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return run_batch.batch_response(
            *run_batch.pause_run_batch(self.job_db, workspace_id, run_id)
        )

    def resume_run_batch(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return run_batch.batch_response(
            *run_batch.resume_run_batch(self.job_db, workspace_id, run_id)
        )

    def cancel_run_batch(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return run_batch.batch_response(
            *run_batch.cancel_run_batch(self.job_db, workspace_id, run_id)
        )
