"""Service-plane status projection for the Worker supervisor panel.

Split from ``worker/supervisor.py`` (#250 budget floors): the supervisor keeps
process orchestration and lock ownership, while this module assembles the
status payload the local UI consumes — the local process snapshot fields and
the Host-view fallback shown while no runtime status file has been written
yet. Pure functions; state reads happen on the caller's side of the locks.
"""

from __future__ import annotations

from typing import Any

from worker.status.aggregates import execution_counts


def process_snapshot(
    running: bool,
    pid: int | None,
    started_at: float | None,
    exit_code: int | None,
    restart_count: int,
    next_restart_delay: float | None,
    failed_reason: str | None,
) -> dict[str, Any]:
    """Local supervisor state fields for the status payload."""
    return {
        "worker_running": running,
        "pid": pid,
        "started_at": started_at,
        "exit_code": exit_code,
        "restart_count": restart_count,
        "next_restart_delay": next_restart_delay,
        "failed": failed_reason,
    }


def host_view(configured: bool, remote: dict[str, Any], worker_running: bool) -> dict[str, Any]:
    """Host-view fields with the waiting-on-first-sync fallback."""
    if configured and not remote:
        return {
            "host_reachable": False,
            "registered": False,
            "connected": False,
            "host_worker": None,
            "connection_error": (
                "等待 Worker 使用签发 token 同步 Host 状态"
                if worker_running
                else "Worker 执行进程未运行"
            ),
        }
    return remote


def status_payload(
    configured: bool,
    config: dict[str, Any],
    executions: list[dict[str, Any]],
    bootstrap_error: str | None,
    mounted_config_diverged: bool,
    snapshot: dict[str, Any],
    remote: dict[str, Any],
) -> dict[str, Any]:
    """Compose the /api/status payload from its independently-read inputs."""
    return {
        "service": "running",
        "configured": configured,
        "claim_enabled": config["claim_enabled"],
        "max_concurrency": config["max_concurrency"],
        "upload_max_concurrency": config.get("upload_max_concurrency", 4),
        **execution_counts(executions),
        "bootstrap_error": bootstrap_error,
        "mounted_config_diverged": mounted_config_diverged,
        **snapshot,
        "current_executions": executions,
        **remote,
    }
