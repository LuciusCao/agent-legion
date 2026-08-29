"""Synchronize Worker-token-authenticated Host state without exposing the token."""

from __future__ import annotations

from typing import Any

from worker.host.client import Client, WorkerAuthError
from worker.metrics_cache import WorkerMetricsCache
from worker.registration.retry import last_registration_workspaces
from worker.status import ExecutionStatusReporter


def _remote_status(
    worker: dict[str, Any] | None,
    *,
    host_reachable: bool,
    connection_error: str | None = None,
) -> dict[str, Any]:
    registered = worker is not None and not bool(worker.get("revoked", False))
    # 显式 Any：字面量推断会把值类型窄化成 bool/dict/str，与下方追加
    # list 值的 workspaces 键不兼容（mypy）。
    remote: dict[str, Any] = {
        "host_reachable": host_reachable,
        "registered": registered,
        "connected": registered,
        "host_worker": worker,
        "connection_error": connection_error,
    }
    if workspaces := last_registration_workspaces():
        # set_remote 是整体替换：注册时 Host 汇报的 workspace 明细（控制台
        # token→workspace 名称的唯一来源）必须随每次同步重新携带，否则一次
        # 心跳就把启动时写入的字段抹掉。Host 删除某个 scoped token 后会收窄
        # 存活 Worker 的 allowed_workspaces，因此按当前 scope 过滤，避免控制台
        # 一直显示已删除的 workspace。executor 的不可达路径传入上一次成功的
        # worker dict，此时按最后已知 scope 过滤；仅当没有可用 worker 视图
        # （鉴权拒绝 / 首次同步失败）时不过滤，恢复后的下一次同步重新对齐。
        scope = worker.get("allowed_workspaces") if worker else None
        current = [row for row in workspaces if not scope or row["workspace_id"] in scope]
        if current:
            remote["workspaces"] = current
    return remote


def sync_host_status(
    client: Client,
    status: ExecutionStatusReporter,
    metrics: WorkerMetricsCache,
    previous: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Publish Host status; authentication rejection remains fatal to the caller."""
    try:
        worker = client.get_self()
    except WorkerAuthError as exc:
        status.set_remote(_remote_status(None, host_reachable=True, connection_error=str(exc)))
        raise
    except Exception as exc:
        status.set_remote(_remote_status(previous, host_reachable=False, connection_error=str(exc)))
        return previous
    status.set_remote(_remote_status(worker, host_reachable=True))
    metrics.refresh(client)
    return worker
