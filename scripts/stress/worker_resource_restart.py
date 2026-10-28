"""Current-generation registration and restart readiness."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from scripts.stress.worker_resource_identity import (
    ExecutionReadinessError,
    require_restart_process_response,
)


def _process_identity(status: Mapping[str, Any]) -> dict[str, Any]:
    return {"pid": status.get("pid"), "started_at": status.get("started_at")}


def require_pending_restart(
    previous_status: Mapping[str, Any],
    pending: Mapping[str, Any],
    current_status: Mapping[str, Any],
    runtime: str,
) -> dict[str, Any]:
    """Prove the disabled-runtime restart reached a new process and startup refusal."""
    pid, started = require_restart_process_response(previous_status, pending, runtime)
    disabled = set(pending["config"].get("disabled_runtimes") or [])
    if (
        current_status.get("worker_running") is not False
        or current_status.get("registered") is not False
        or not current_status.get("failed")
        or current_status.get("exit_code") != 2
    ):
        raise ExecutionReadinessError("disabled runtime startup refusal is not observable")
    return {
        "observed": True,
        "runtime": runtime,
        "disabled_runtimes": sorted(disabled),
        "before": _process_identity(previous_status),
        "after": {"pid": pid, "started_at": started},
        "terminal": {
            "worker_running": False,
            "registered": False,
            "exit_code": 2,
            "failed": current_status["failed"],
        },
    }


def require_current_registration(
    status: Mapping[str, Any],
    host_workers: Iterable[Mapping[str, Any]],
    previous_status: Mapping[str, Any] | None,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind restart readiness to the current local process and current Host view."""
    if not status.get("worker_running") or status.get("registered") is not True:
        raise ExecutionReadinessError("current executor is not registered")
    host = status.get("host_worker")
    if not isinstance(host, Mapping):
        raise ExecutionReadinessError("current host_worker is unavailable")
    pid = status.get("pid")
    started_at = status.get("started_at")
    if not isinstance(pid, int) or pid <= 0 or not isinstance(started_at, (int, float)):
        raise ExecutionReadinessError("current executor process identity is unavailable")
    if previous_status is not None:
        if pid == previous_status.get("pid"):
            raise ExecutionReadinessError("executor PID did not change")
        if started_at <= previous_status.get("started_at", 0):
            raise ExecutionReadinessError("executor start time did not advance")
    current_row = next(
        (row for row in host_workers if row.get("worker_id") == expected["worker_id"]), None
    )
    for row in (host, current_row):
        if (
            not isinstance(row, Mapping)
            or row.get("worker_id") != expected["worker_id"]
            or set(row.get("allowed_workspaces") or []) != set(expected["workspace_ids"])
            or expected["runtime"] not in (row.get("runtimes") or [])
            or int(row.get("max_concurrency") or 0) != expected["agent_concurrency"]
            or int(row.get("max_code_concurrency") or 0) != expected["code_concurrency"]
        ):
            raise ExecutionReadinessError("current Host scope/runtime/capacity is not ready")
    try:
        last_seen = [
            datetime.fromisoformat(str(row["last_seen_at"]).replace("Z", "+00:00"))
            for row in (host, current_row)
        ]
    except (KeyError, ValueError) as exc:
        raise ExecutionReadinessError("current registration timestamp is unavailable") from exc
    observed_at = datetime.now(UTC)
    started = datetime.fromtimestamp(started_at, UTC)
    if any(value.tzinfo is None for value in last_seen) or any(
        not started <= value <= observed_at for value in last_seen
    ):
        raise ExecutionReadinessError("Host row is not the current executor registration")
    return {
        "observed": True,
        "observed_at": observed_at.isoformat(),
        "before": _process_identity(previous_status or {}),
        "after": {"pid": pid, "started_at": started_at},
        "host_worker": {
            "worker_id": current_row["worker_id"],
            "allowed_workspaces": sorted(expected["workspace_ids"]),
            "runtimes": current_row["runtimes"],
            "max_concurrency": current_row["max_concurrency"],
            "max_code_concurrency": current_row["max_code_concurrency"],
            "registered_at": current_row.get("registered_at"),
            "last_seen_at": current_row["last_seen_at"],
            "local_last_seen_at": host["last_seen_at"],
        },
    }


def enable_claim(
    run: Any, token: str, session: Any, previous_status: dict[str, Any] | None = None
) -> dict[str, Any]:
    run._worker_request("PUT", "/api/config", token, json={"claim_enabled": True})
    run._wait_worker_process(token)
    expected = {
        "worker_id": run.worker_id,
        "workspace_ids": set(run.workspaces.values()),
        "runtime": "velites",
        "agent_concurrency": run.args.agent_concurrency,
        "code_concurrency": run.args.code_concurrency,
    }

    def ready() -> dict[str, Any]:
        status = run._worker_request("GET", "/api/status", run.control_token).json()
        response = session.get(f"{run.host_url}/api/agent-workers", timeout=5)
        response.raise_for_status()
        return require_current_registration(
            status, response.json().get("workers", []), previous_status, expected
        )

    return run._wait("current executor registration", ready)
