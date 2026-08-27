"""Synchronize Worker-token-authenticated Host state without exposing the token."""

from __future__ import annotations

from typing import Any

from worker.host_client import Client, WorkerAuthError
from worker.metrics_cache import WorkerMetricsCache
from worker.registration_retry import last_registration_workspaces
from worker.status import ExecutionStatusReporter


def _remote_status(
    worker: dict[str, Any] | None,
    *,
    host_reachable: bool,
    connection_error: str | None = None,
) -> dict[str, Any]:
    registered = worker is not None and not bool(worker.get("revoked", False))
    remote = {
        "host_reachable": host_reachable,
        "registered": registered,
        "connected": registered,
        "host_worker": worker,
        "connection_error": connection_error,
    }
    if workspaces := last_registration_workspaces():
        # set_remote 是整体替换：注册时 Host 汇报的 workspace 明细（控制台
        # token→workspace 名称的唯一来源）必须随每次同步重新携带，否则一次
        # 心跳就把启动时写入的字段抹掉。
        remote["workspaces"] = workspaces
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
