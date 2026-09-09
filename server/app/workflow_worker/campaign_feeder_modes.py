"""CampaignFeeder 的 rerun/upgrade 模式方法（#532 PR-B / PR-C 预算纪律）。

_submit_rerun / _submit_upgrade 从 campaign_feeder.py 拆出：PR-C 的
submit mixin 合入后，骨架文件超出了 PR-B 注册的 file_budget 棘轮上限，
rerun/upgrade 两个模式方法与 submit 的 CampaignSubmitMixin 同级为 mixin
（同一 self 合同：job_db / rerun_service / upgrade_service）。

崩溃纪律语义见 campaign_batch_staging（stage/replay/partition）与 v81
投递标记（campaign_job_deliveries）——本模块只保留编排顺序：切片 →
stage（filter 形态）→ 标记分拣 → 投递 → BatchOutcome 计数。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.job_rerun.batch import batch_rerun
from server.app.workflow_worker.campaign_batch_staging import (
    next_slice,
    partition_replay,
    stage_batch,
)
from server.app.workflow_worker.campaign_feeder_types import BatchOutcome, target_spec

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.services.job_rerun import JobRerunService
    from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService


class CampaignRerunModesMixin:
    """rerun/upgrade 两个模式的批次投递（feeder 骨架的 mixin 之一）。

    self 合同（由 CampaignFeeder 提供）：job_db、rerun_service、
    upgrade_service——与 CampaignSubmitMixin 共享同一宿主。
    """

    job_db: JobQueries
    rerun_service: JobRerunService
    upgrade_service: JobWorkflowUpgradeService

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
