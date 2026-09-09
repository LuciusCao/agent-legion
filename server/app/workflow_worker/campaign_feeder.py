"""CampaignFeeder: the watermark-gated drip-feed loop (#532 / #505, design §2).

A dedicated daemon thread (name ``campaign-feeder``), NOT an inline step of
``WorkflowWorkerThread._poll``: submitting one batch can take seconds (the
6.9s / 5k-item baseline of the chunked run path) while the poll loop runs at
0.2s cadence — inlining would stall every workspace's scheduling behind one
workspace's batch (the WorkflowMaintenance precedent: slow work goes to its
own daemon thread, never the poll loop).

Durable state lives in the campaign row (CAMPAIGN-STATE-001): this thread
holds only in-memory scheduling artifacts — the workspace round-robin
pointers, per-campaign backoff deadlines, and the submit-mode manifest
cache — all of which are safe to lose on restart (the row re-initializes
them; re-submitting a batch is idempotent through job dedup / rerun
eligibility).

Crash discipline for the filter form (PR #545 P1): a filter batch's
matching fields are rewritten by its own submission, so a crash between
the batch's commits and the CAS advance would make the fed jobs
unfindable by the re-run filter — their counters lost forever. The
stage/replay/pop mechanics live in campaign_batch_staging.py (split at
the budget ceiling); explicit-ids and submit forms need no staging —
their slice sources are stable across the crash.

The three mode dispatches: rerun/upgrade live here (PR-B); submit is the
CampaignSubmitMixin (campaign_feeder_submit.py, PR-C) — the per-campaign
manifest cache (inline spec or the object-store manifest object),
item_offset slicing, ``run_service.create_run(campaign_id=...)``, and the
all-duplicates InvalidOperationError absorb that keeps re-fed batches
advancing the cursor without creating anything.

Failure classification (design §2.3): ``JobServiceError`` families raised
while feeding are deterministic (corrupt target spec, vanished revision)
and flip the campaign to ``failed`` with the sample error; anything else is
the transient infrastructure family (connection loss, OS errors) and backs
off linearly — never a ``failed`` row, never an escape from the loop.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.campaigns import CAMPAIGN_ACTIVE_STATUSES
from server.app.services.job_errors import InvalidOperationError, JobServiceError
from server.app.services.job_rerun.batch import batch_rerun
from server.app.workflow_worker.campaign_batch_staging import (
    next_slice,
    partition_replay,
    stage_batch,
)
from server.app.workflow_worker.campaign_feeder_submit import CampaignSubmitMixin
from server.app.workflow_worker.campaign_feeder_types import (
    BatchOutcome,
    copy_progress,
    target_spec,
)

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.services.job_rerun import JobRerunService
    from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
    from server.app.services.run_service import RunService
    from server.app.settings import Settings
    from server.app.storage import ObjectStorage
    from server.app.worker_control import WorkspaceWorkerControl

logger = logging.getLogger(__name__)

# Wide-set non-terminal watermark (CAMPAIGN-STATE-001): total minus the
# terminal statuses. paused / awaiting_approval deliberately count toward
# the level — the #349 red line's set definition, transplanted from the
# issue-505 CLI's non_terminal_count.
_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed"})

# Transient-error linear backoff (design §2.3, the CLI's MAX_RETRY_WAIT
# server-side): min(5s × consecutive failures, 60s), held in memory. A
# restart clears it, which is harmless — re-feeding is idempotent.
_BACKOFF_BASE_SECONDS = 5.0
_BACKOFF_MAX_SECONDS = 60.0

# Watermark sampling trail length (design §2.2 step 5): the last N
# (level, ts) pairs ride progress_json for the UI sparkline.
_WATERMARK_TRAIL_LENGTH = 50


class CampaignFeeder(CampaignSubmitMixin):
    """Tick loop draining active campaign rows in watermark-gated batches."""

    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        *,
        rerun_service: JobRerunService,
        upgrade_service: JobWorkflowUpgradeService,
        run_service: RunService | None = None,
        workspace_worker_control: WorkspaceWorkerControl | None = None,
        object_storage: ObjectStorage | None = None,
    ) -> None:
        self.job_db = job_db
        self.settings = settings
        self.rerun_service = rerun_service
        self.upgrade_service = upgrade_service
        self.run_service = run_service
        self.workspace_worker_control = workspace_worker_control
        self.object_storage = object_storage
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        # In-memory scheduling state (lost on restart by design): the feed
        # deadlines / backoff counters, the round-robin fairness pointers,
        # and the submit-mode manifest cache (PR-C).
        self._next_feed_at: dict[str, float] = {}
        self._attempts: dict[str, int] = {}
        self._round_robin: dict[str, int] = {}
        # campaign_id → loaded submit manifest, plus its byte accounting
        # (the submit mixin's cache: LRU order by move-to-back, globally
        # byte-budgeted via campaigns.manifest_cache_max_bytes — PR-C
        # review P1; both dicts share the campaign_id key space and are
        # pruned together below).
        self._manifest_cache: dict[str, Any] = {}
        self._manifest_cache_bytes: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Thread plumbing
    # ------------------------------------------------------------------

    def wake(self) -> None:
        """Break the tick sleep immediately (the resume endpoint's hook).

        Self-held event, mirroring register_wakeup's pattern without the
        global registry: the feeder is the only consumer of its own wake.
        """
        self._wake_event.set()

    def start(self) -> None:
        # Idempotency guard (ArtifactOrphanGcThread.start precedent): a
        # second start() — repeated lifespan entry or a future multi-
        # lifespan host — must not resurrect the thread after stop().
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="campaign-feeder", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop_event.set()
        self.wake()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _loop(self) -> None:
        tick = self._tick_seconds
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                # #204 broad-except audit: deliberate feeder-loop safety
                # net. Killing this thread would freeze every active
                # campaign at its stored cursor until a Host restart; the
                # next tick is the built-in retry, and the tick body
                # already narrows per-campaign failures into backoff /
                # failed-row flips — an escape here is a tick-level error,
                # logged and never fatal to the thread.
                logger.exception("campaign feeder tick failed")
            self._sleep(tick)

    def _sleep(self, seconds: float) -> None:
        """Tick cadence, interruptible by wake/stop."""
        self._wake_event.wait(timeout=seconds)
        self._wake_event.clear()

    # ------------------------------------------------------------------
    # Config accessors
    # ------------------------------------------------------------------

    @property
    def _config(self) -> Any:
        return self.settings.executor_runtime.campaigns

    @property
    def _tick_seconds(self) -> float:
        return float(self._config.feeder_tick_seconds)

    @property
    def _feed_interval_seconds(self) -> float:
        return float(self._config.feed_interval_seconds)

    # ------------------------------------------------------------------
    # One tick (design §2.2 steps 1-5)
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        campaigns = self.job_db.list_active_campaigns()
        if not campaigns:
            self._prune_memory(())
            return
        self._prune_memory(campaigns)
        by_workspace: dict[str, list[dict[str, Any]]] = {}
        for campaign in campaigns:
            by_workspace.setdefault(str(campaign["workspace_id"]), []).append(campaign)
        # Per-tick per-workspace memoization of the pause check and the
        # watermark level (is_paused opens a connection per call).
        paused: dict[str, bool] = {}
        levels: dict[str, int] = {}
        for workspace_id, group in by_workspace.items():
            picked = self._pick_campaign(workspace_id, group)
            if picked is None:
                continue
            if paused.setdefault(workspace_id, self._is_paused(workspace_id)):
                # A paused workspace suspends feeding but never rewrites
                # the campaign's own status (design §2.4): the row stays
                # running, its counters stop moving.
                continue
            level = levels.setdefault(workspace_id, self._non_terminal_level(workspace_id))
            try:
                self._feed_one(picked, level)
            except JobServiceError as exc:
                # Deterministic family: the campaign's target is broken
                # (corrupt spec, vanished revision); retrying cannot change
                # the outcome. Fail the row with the sample error — repair
                # path is a fresh campaign (design §2.3). PR-B review P1:
                # attribute to `picked` (this tick's round-robin selection)
                # — the grouping loop's `campaign` binding points at the
                # group's last row, so a feed failure would fail an
                # unrelated, possibly healthy campaign.
                logger.error(
                    "campaign feeder: campaign %s failed deterministically: %s",
                    picked["id"],
                    exc,
                )
                self._fail_campaign(picked, str(exc))
            except Exception:
                # #204 broad-except audit: per-campaign containment. The
                # feed body narrows the expected failures (per-job
                # rerun/upgrade outcomes are result dicts, not exceptions),
                # so an escape here is the transient infrastructure family
                # (DB connection loss, OS errors): log with traceback, back
                # this campaign off linearly, keep ticking. Same P1
                # attribution: the backoff counters belong to `picked`.
                logger.exception("campaign feeder: feeding campaign %s failed", picked["id"])
                self._note_transient_failure(picked)

    def _pick_campaign(
        self, workspace_id: str, group: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Round-robin pick: one campaign per workspace per tick, feed-gated.

        The scan order is stable, so the memory pointer walks the group
        fairly; campaigns inside their feed-interval window or backoff
        deadline are skipped. None when every campaign is gated.
        """
        now = time.monotonic()
        count = len(group)
        start = self._round_robin.get(workspace_id, 0)
        for offset in range(count):
            index = (start + offset) % count
            campaign = group[index]
            if self._next_feed_at.get(str(campaign["id"]), 0.0) > now:
                continue
            self._round_robin[workspace_id] = (index + 1) % count
            return campaign
        return None

    def _prune_memory(self, campaigns: Any) -> None:
        active = {str(campaign["id"]) for campaign in campaigns}
        workspaces = {str(campaign["workspace_id"]) for campaign in campaigns}
        for registry in (
            self._next_feed_at,
            self._attempts,
            self._manifest_cache,
            self._manifest_cache_bytes,
        ):
            for campaign_id in list(registry):
                if campaign_id not in active:
                    del registry[campaign_id]
        for workspace_id in list(self._round_robin):
            if workspace_id not in workspaces:
                del self._round_robin[workspace_id]

    # ------------------------------------------------------------------
    # Workspace gates
    # ------------------------------------------------------------------

    def _is_paused(self, workspace_id: str) -> bool:
        control = self.workspace_worker_control
        return control is not None and control.is_paused(workspace_id)

    def _non_terminal_level(self, workspace_id: str) -> int:
        """Wide-set non-terminal level: total − completed − failed.

        The trigger-maintained counter table is a PK-point-read whose cost
        is independent of workspace size (DB-JOB-STATUS-COUNTS-001).
        """
        counts = self.job_db.count_jobs_by_status(workspace_id)
        return sum(n for status, n in counts.items() if status not in _TERMINAL_JOB_STATUSES)

    # ------------------------------------------------------------------
    # Feeding one campaign
    # ------------------------------------------------------------------

    def _feed_one(self, campaign: dict[str, Any], level: int) -> None:
        """Gate on the watermark, then feed one batch of one campaign.

        The watermark is a replenishment trigger level, not a capacity
        promise: level >= watermark merely skips this round; a low watermark
        plus a large batch is a legal burst configuration and the batch is
        NOT refused (the CLI check_watermark semantics).
        """
        campaign_id = str(campaign["id"])
        if str(campaign["status"]) == "pending":
            # CAS pickup: pending → running, one winner among racing feeders
            # or a racing cancel. Precedes the watermark gate so a gated
            # campaign still shows running ("drained, waiting on the level").
            picked = self.job_db.transition_campaign_status(campaign_id, ("pending",), "running")
            if picked is None:
                return
            campaign = picked
        watermark = int(campaign["watermark"])
        if watermark >= 1 and level >= watermark:
            self._attempts.pop(campaign_id, None)
            return
        outcome = self._submit_batch(campaign)
        if outcome is None:
            return  # submit mode: PR-C fills this branch in
        if not outcome.ids:
            # Empty slice: the target is gone (post-creation deletions) or
            # the completed flip lost the race to a crash — finish without
            # counting a phantom batch; a lost stage race (PR #545 P1)
            # returns exhausted=False and just skips (the row is terminal).
            if outcome.exhausted:
                self.job_db.transition_campaign_status(campaign_id, ("running",), "completed")
            return
        progress = copy_progress(campaign)
        self._advance_cursor(progress, campaign, outcome)
        samples = list(progress.get("watermark_samples") or [])
        samples.append({"level": level, "ts": time.time()})
        progress["watermark_samples"] = samples[-_WATERMARK_TRAIL_LENGTH:]
        progress.pop("consecutive_failures", None)
        advanced = self.job_db.advance_campaign_progress(
            campaign_id,
            expected_progress=copy_progress(campaign),
            progress=progress,
            batches_submitted=int(campaign["batches_submitted"]) + 1,
            jobs_succeeded=int(campaign["jobs_succeeded"]) + outcome.succeeded,
            jobs_skipped=int(campaign["jobs_skipped"]) + outcome.skipped,
            jobs_failed=int(campaign["jobs_failed"]) + outcome.failed,
        )

        if advanced is None:
            # Lost the CAS race against pause/cancel: the submitted batch
            # stays (dedup / rerun eligibility make it idempotent), the
            # cursor stays frozen, and the next active scan will not match
            # this row. PR #545 P1：filter 形态下 stage 的 pending_batch
            # 也留存——resume 后 next_slice 优先消化，计数不漏记。
            return
        if outcome.exhausted:
            self.job_db.transition_campaign_status(campaign_id, ("running",), "completed")
        else:
            # Round-4 P1（v81）：本批计数已落账，投递标记的守护职责结束
            # ——同一 campaign 的后续重试/新批次必须全新投递。exhausted
            # 的终态翻转走上面的 transition，它在同一事务里兜底清标记。
            self.job_db.clear_campaign_deliveries(campaign_id)
        self._attempts.pop(campaign_id, None)
        # A fed campaign waits out the feed interval before its next batch
        # (the single-campaign pacing rule; the picker enforces it).
        self._next_feed_at[campaign_id] = time.monotonic() + self._feed_interval_seconds

    def _advance_cursor(
        self, progress: dict[str, Any], campaign: dict[str, Any], outcome: BatchOutcome
    ) -> None:
        """Move the mode-specific cursor forward past the fed slice."""
        # PR #545 P1：本批目标已消化（提交完成、计数即将落账），从文档
        # 摘除 pending_batch——它是投递前的崩溃恢复锚点，正常推进路径
        # 不留残迹；transient / 竞态路径不经过这里，锚点保留待重放。
        progress.pop("pending_batch", None)
        if "cursor" in progress:
            # rerun/upgrade filter form: keyset cursor + processed count.
            progress["processed"] = int(progress.get("processed") or 0) + len(outcome.ids)
            progress["cursor"] = outcome.next_cursor
            if outcome.exhausted:
                # Round-3 P1-2：耗尽标记只在落账时写（与 pop pending_batch
                # 同一份 progress、同一次 advance CAS 原子提交）——末页落
                # 账后、completed 翻转前的崩溃窗口里，恢复路径凭它短路；
                # stage 时不写：重放优先级必须高于 exhausted（否则末页
                # 崩溃的 pending_batch 永远消化不掉、计数漏记）。
                progress["exhausted"] = True
            else:
                progress.pop("exhausted", None)
        elif "offset" in progress:
            # rerun/upgrade explicit-ids form: the list offset.
            progress["offset"] = int(progress.get("offset") or 0) + len(outcome.ids)
        elif "item_offset" in progress:
            # submit form: the manifest line offset. The batch outcome's
            # ids field carries the campaign id marker (len 1), NOT the
            # slice size — the slice length is batch_size-bounded and the
            # created count is the dedup outcome, so recompute the advance
            # from the stored offset + the row's batch_size (design §1.4).
            batch_size = int(campaign["batch_size"])
            progress["item_offset"] = int(progress.get("item_offset") or 0) + batch_size

    # ------------------------------------------------------------------
    # Mode dispatch (design §2.3)
    # ------------------------------------------------------------------

    def _submit_batch(self, campaign: dict[str, Any]) -> BatchOutcome | None:
        """Feed one batch (rerun / upgrade / submit).

        Per-job outcomes are result dicts on the rerun/upgrade paths
        (byte-identical to the synchronous entry points): succeeded = flips
        that landed, skipped = ineligible/not-found/busy, failed = per-job
        failures. The submit path folds create_run's single verdict into the
        same triple: created = succeeded, the dedup-dropped remainder =
        skipped (PR-C review P2-1: a mixed batch's counters sum to the
        slice), and the all-duplicates absorb counts the whole slice as
        skipped.
        """
        mode = str(campaign["mode"])
        if mode == "rerun":
            return self._submit_rerun(campaign)
        if mode == "upgrade":
            return self._submit_upgrade(campaign)
        if mode == "submit":
            return self._submit_manifest_batch(campaign)
        raise InvalidOperationError(f"Unsupported campaign mode {mode!r}")

    def _submit_rerun(self, campaign: dict[str, Any]) -> BatchOutcome:
        ids, next_cursor, exhausted = next_slice(self.job_db, campaign)
        if not ids:
            return BatchOutcome([], 0, 0, 0, True, None)
        if not stage_batch(self.job_db, campaign, ids, next_cursor, exhausted):
            # PR #545 P1：stage 输给 pause/cancel——空 ids 且非 exhausted
            # 让 _feed_one 安静跳过（不投、不翻终态、不推进）。
            return BatchOutcome([], 0, 0, 0, False, None)
        # Round-4 P1：重放分拣（标记归属 + completed 产物保护，语义见
        # campaign_batch_staging.partition_replay）。
        campaign_id = str(campaign["id"])
        delivered, completed, to_deliver = partition_replay(self.job_db, campaign_id, ids)
        spec = target_spec(campaign)
        results = (
            batch_rerun(
                self.rerun_service,
                str(campaign["workspace_id"]),
                job_ids=to_deliver,
                node_key=spec.get("node_key"),
                from_failed_node=bool(spec.get("from_failed_node")),
                campaign_id=campaign_id,
            )
            if to_deliver
            else []
        )
        return BatchOutcome(
            ids=list(ids),
            succeeded=len(delivered.intersection(ids))
            + sum(1 for r in results if r.get("status") == "succeeded"),
            skipped=len(completed - delivered)
            + sum(1 for r in results if r.get("status") == "skipped"),
            failed=sum(1 for r in results if r.get("status") == "failed"),
            exhausted=exhausted,
            next_cursor=next_cursor,
        )

    def _submit_upgrade(self, campaign: dict[str, Any]) -> BatchOutcome:
        ids, next_cursor, exhausted = next_slice(self.job_db, campaign)
        if not ids:
            return BatchOutcome([], 0, 0, 0, True, None)
        if not stage_batch(self.job_db, campaign, ids, next_cursor, exhausted):
            # PR #545 P1：同 _submit_rerun——stage 失败即放弃本批。
            return BatchOutcome([], 0, 0, 0, False, None)
        campaign_id = str(campaign["id"])
        # Round-4 P1（v81）：标记归属，同 _submit_rerun——已投递计
        # succeeded 不重投（already_current 无法归属崩溃 pass 的翻新）。
        delivered = self.job_db.campaign_delivered_job_ids(campaign_id)
        succeeded = len(delivered.intersection(ids))
        skipped = failed = 0
        for job_id in ids:
            if job_id in delivered:
                continue
            result = self.upgrade_service.upgrade(
                str(campaign["workspace_id"]), job_id, campaign_id=campaign_id
            )
            status = str(result.get("status"))
            if status == "succeeded":
                succeeded += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
        return BatchOutcome(
            ids=list(ids),
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
            exhausted=exhausted,
            next_cursor=next_cursor,
        )

    # ------------------------------------------------------------------
    # Failure accounting (design §2.3)
    # ------------------------------------------------------------------

    def _note_transient_failure(self, campaign: dict[str, Any]) -> None:
        """Linear backoff on a transient feed failure: min(5×attempt, 60s).

        No retry cap: the watermark loop always comes back around, and
        re-feeding is idempotent. The consecutive-failure count is surfaced
        into progress_json for UI alerting — best-effort, CAS-guarded, so
        a racing pause/cancel simply wins.
        """
        campaign_id = str(campaign["id"])
        attempts = self._attempts.get(campaign_id, 0) + 1
        self._attempts[campaign_id] = attempts
        self._next_feed_at[campaign_id] = time.monotonic() + min(
            _BACKOFF_BASE_SECONDS * attempts, _BACKOFF_MAX_SECONDS
        )
        progress = copy_progress(campaign)
        progress["consecutive_failures"] = attempts
        self.job_db.advance_campaign_progress(
            campaign_id,
            expected_progress=copy_progress(campaign),
            progress=progress,
            batches_submitted=int(campaign["batches_submitted"]),
            jobs_succeeded=int(campaign["jobs_succeeded"]),
            jobs_skipped=int(campaign["jobs_skipped"]),
            jobs_failed=int(campaign["jobs_failed"]),
        )

    def _fail_campaign(self, campaign: dict[str, Any], message: str) -> None:
        """Deterministic failure: flip the row to failed with the sample."""
        self.job_db.transition_campaign_status(
            str(campaign["id"]), CAMPAIGN_ACTIVE_STATUSES, "failed", error_message=message
        )
