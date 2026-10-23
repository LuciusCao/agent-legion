"""Retry-failed requeue for terminal batch operations (#1358, M52 复评遗留).

终态（completed/failed/cancelled）原 op 的失败项按原 kind + 原参数续跑为
一个新 operation——服务端派生的「原 op 续跑」，比客户端从 results 分页
重组同 kind 新批更诚实（M52 结论）：kind 语义分支由后端集中裁决
（pause/resume 的失败项重试无意义，拒绝；package = 重打包其失败项），
frozen 选择即原 op 的失败集（distinct、按首败 seq 排序），重试 op 经同
一 feeder 执行——容量水位闸门（rerun 族 drip）与崩溃重放幂等（results
replay guard）原样生效，无需新机制。

幂等与既有批量操作一致：重复 retry-failed 各自入队一个新重试 op（同重复
batch-rerun），终态原 op 的结果集不变，重试只读它、从不改写。永远异步
（202）：重试不走同步快路径（句柄即审计记录，读侧路由不持 services
bundle）。
"""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.job_batch_operations import JOB_BATCH_OPERATION_KINDS
from server.app.services.job_batch_operations.service import (
    get_operation_for_workspace,
    submit_operation,
)
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)

# 与 DDL status check 一致；paused 是保留控制面态（非终态）。
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

# M52 分诊：pause/resume 的失败项重试无意义（pause 失败多为 terminal job，
# resume 反之），其余 kind 的失败语义都成立。
RETRYABLE_KINDS = frozenset(
    kind for kind in JOB_BATCH_OPERATION_KINDS if kind not in ("pause", "resume")
)


def submit_retry_failed(
    job_db: JobQueries, workspace_id: str, op_id: str, *, created_by: str
) -> dict[str, Any]:
    """Enqueue the retry operation; returns the accepted row (the 202 handle).

    404 unknown/cross-workspace（对行再校验，不信任路径）；409 非终态；
    400 kind 不可重试或无失败项。
    """
    row = get_operation_for_workspace(job_db, workspace_id, op_id)
    if row is None:
        raise NotFoundError("Job batch operation not found")
    status = str(row["status"])
    if status not in TERMINAL_STATUSES:
        raise ConflictError(f"Job batch operation is not terminal (status: {status})")
    kind = str(row["kind"])
    if kind not in RETRYABLE_KINDS:
        raise InvalidOperationError(f"Batch operation kind {kind!r} does not support retry-failed")
    failed_ids = job_db.list_job_batch_operation_failed_job_ids(op_id)
    if not failed_ids:
        raise InvalidOperationError("Job batch operation has no failed items to retry")
    params = json.loads(str(row["target_spec_json"])).get("params") or {}
    return submit_operation(
        job_db, workspace_id, kind, failed_ids, params, created_by, retry_of=op_id
    )
