"""Batch workflow upgrades over selections resolved from ids or a list filter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_selection_resolver import (
    EmptyJobSelectionError,
    resolve_batch_selection,
)
from server.app.services.job_workflow_upgrade_result import upgrade_result

if TYPE_CHECKING:
    from collections.abc import Collection

    from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService

logger = logging.getLogger(__name__)


def batch_upgrade(
    service: JobWorkflowUpgradeService,
    workspace_id: str,
    job_ids: list[str] | None = None,
    *,
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
    mode: str = "clean",
) -> list[dict[str, Any]]:
    """Upgrade each selected job; explicit ids and filters resolve the same way.

    ``mode`` 透传给每个 job 的 upgrade（issue #645：clean 全量重跑 / inherit
    继承未变节点产物）。逐 job 错误隔离（#759 P1）：单 job 抛出的意外异常
    归一化为该 job 的 failed 结果项（reason_code=upgrade_failed），不中断
    整批、不丢已处理 job 的结果。
    """
    ids = resolve_batch_selection(service.job_db, workspace_id, job_ids, job_filter, exclude_ids)
    if not ids:
        raise EmptyJobSelectionError("No job_ids provided or matched by the filter")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job_id in ids:
        normalized = job_id.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            results.append(service.upgrade(workspace_id, normalized, mode=mode))
        except Exception as exc:
            # #204 broad-except audit: batch 错误隔离。service.upgrade 的
            # 业务失败已归一化为结果 dict（skipped/failed 不抛）；冒到这里的
            # 是意外异常（DB 断连、缺陷），结果空间无法按业务族枚举。一个
            # job 的意外失败不得中断整批、丢掉已完成 job 的结果——归一化
            # 为该 job 的 failed 结果项（与 rerun 的 rerun_failed 同款），
            # 继续处理后续 job；logger.exception 保全 traceback。
            logger.exception("Batch workflow upgrade failed for job %s", normalized)
            results.append(
                upgrade_result(normalized, "failed", "upgrade_failed", str(exc), mode=mode)
            )
    return results
