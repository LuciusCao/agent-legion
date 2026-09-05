"""Structured JSON-lines events for the Worker's claim/execution loop (#490).

与 Host 侧 ``server/app/agent_broker/worker_events.py`` 同一事件 schema
（单行 JSON：``event`` / ``ts`` / 载荷字段），Worker 侧落在 supervisor 的
console 流（executor 子进程 stdout → supervisor `_log` 面板 + 部署日志）。
排障时两侧按 ``execution_id`` / ``worker_id`` 对时间线，不再 grep 访问日志。

- ``emit_event``：纯函数、不抛、单行 JSON（面板 deque 与日志采集都按行吃）。
- ``http_error_fields``：issue #490 的核心痛点——上游 HTTP 错误响应必须
  带 ``status_code`` 与目标 URL 落日志（生产实例曾出现中间层 502，Host
  侧无记录，worker 侧只有异常消息、无 URL）。URL 只含 scheme/host/path，
  查询串截断——claim/result 等控制面路径本无查询串，防御性截断避免
  未来路径把 token 类参数带进日志。
- 级别纪律同 Host 侧：正常节奏事件（claim.attempt、execution.claimed）也
  全量输出——worker 子进程日志本来只进 supervisor 面板 deque（有界）与部署
  日志，量级由事件本身稀疏性控制（每 claim 轮一次、每执行两次），无
  logging 级别面可调（print + flush=True 是 executor 的既有输出约定）。
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

_KNOWN_EVENTS = frozenset(
    {
        "claim.attempt",
        "claim.backoff",
        "http.error",
        "execution.claimed",
        "execution.completed",
        "execution.failed",
    }
)  # tests pin the full set; runbook §7 documents each name


def _scrub_url(url: str) -> str:
    """Query string stripped, length bounded: URLs in events must never carry
    tokens or unbounded parameters into logs (the control paths used today
    carry none — this is forward defense)."""
    clean = url.split("?", 1)[0]
    return clean[:_MAX_URL_CHARS]


# URL 截断上限：控制面路径都很短；防未来带长查询串的路径撑爆日志行。
_MAX_URL_CHARS = 200


def emit_event(event: str, payload: dict[str, Any] | None = None) -> None:
    """Print one lifecycle event as a single JSON line; never raises."""
    body = {"event": event, "ts": datetime.now(UTC).isoformat(), **(payload or {})}
    try:
        line = json.dumps(body, ensure_ascii=False, default=str, sort_keys=True)
    except (TypeError, ValueError):
        # default=str above already covers exotic payloads; this is belt and
        # braces so the observer can never break the observed loop.
        line = f'{{"event": "{event}", "ts": "{datetime.now(UTC).isoformat()}"}}'
    print(line, flush=True)


def http_error_fields(url: str, status_code: int, body: bytes | str = "") -> dict[str, Any]:
    """Fields for "the Host (or a middlebox) answered with an error": the
    upstream HTTP code and the target URL are the two facts issue #490 found
    missing. Body snippet is bounded; the URL is scrubbed (see _scrub_url)."""
    snippet = body[:200] if isinstance(body, str) else body[:200].decode("utf-8", "replace")
    return {
        "status_code": int(status_code),
        "url": _scrub_url(url),
        "body": snippet,
    }


def _since(started: float) -> float:
    """Seconds since a time.monotonic() anchor, rounded for the event line."""
    return round(time.monotonic() - started, 3)


def execution_base(claim: dict[str, Any]) -> dict[str, Any]:
    """Common payload for the per-execution events (#490): identity fields
    off the claim dict; the executor's execution.claimed and run's
    completed/failed share the shape so both sides align by execution_id."""
    return {
        "execution_id": str(claim["execution_id"]),
        "job_id": str(claim.get("job_id", "")),
        "workspace_id": str(claim.get("workspace_id", "")),
        "node_key": str(claim["node_key"]),
        "kind": str(claim.get("kind") or "agent"),
    }


def note_claim_attempt(
    worker_id: str, budget: dict[str, int], upload_backlog: int, claim_enabled: bool
) -> None:
    """claim.attempt: one claim poll's local budget snapshot (idle rhythm =
    one per poll_interval; a single JSON line the Host-side
    claim.granted/empty/rejected events align against by worker_id)."""
    emit_event(
        "claim.attempt",
        {
            "worker_id": worker_id,
            "agent_budget": budget["agent"],
            "code_budget": budget["code"],
            "upload_backlog": upload_backlog,
            "claim_enabled": claim_enabled,
        },
    )


def note_claim_received(worker_id: str, claim: dict[str, Any]) -> None:
    """execution.claimed: a claim arrived (the Worker-side view of the
    Host's claim.granted — same execution, two timelines)."""
    emit_event("execution.claimed", {"worker_id": worker_id, **execution_base(claim)})


def note_claim_backoff(worker_id: str, error: BaseException, wait: float, failures: int) -> None:
    """claim.backoff: the #437 jitter sequence's state (which failure, how
    long the wait) as one line — the HTTP code/URL live in http.error."""
    emit_event(
        "claim.backoff",
        {
            "worker_id": worker_id,
            "error": str(error)[:200],
            "wait_seconds": round(wait, 3),
            "failures": failures,
        },
    )


def note_run_outcome(claim: dict[str, Any], task: Any, started: float) -> None:
    """execution.completed for a process-kind task: exit_code reads the
    UploadTask because the agent branch's local variable is undefined on
    the code branch (reading it would NameError every code claim into a
    fabricated failure). Host acceptance is its own event."""
    if task is None or task.kind != "process":
        return
    emit_event(
        "execution.completed",
        {
            **execution_base(claim),
            "exit_code": int(task.exit_code),
            "wall_seconds": _since(started),
        },
    )


def note_execution_failed(claim: dict[str, Any], error: BaseException, started: float) -> None:
    """execution.failed: the local containment boundary fired (download /
    spawn / wait raised); the error summary rides the event."""
    emit_event(
        "execution.failed",
        {**execution_base(claim), "error": str(error)[:200], "wall_seconds": _since(started)},
    )


def status_fields(claim: dict[str, Any], run_dir: Any, exec_kind: str) -> dict[str, str]:
    """The supervisor-status dict for one execution (identity keys off the
    same base as the #490 events so the two can never drift apart; the
    execution_id key is dropped — status.start takes it positionally)."""
    base = execution_base(claim)
    base.pop("execution_id")
    base.pop("kind")
    base.update(
        agent_id=str(claim.get("agent_id", "")),
        run_dir="" if exec_kind == "code" else str(run_dir),
    )
    return base


def note_http_transport_error(host: str, path: str, method: str, exc: BaseException) -> None:
    """http.error for a transport-level failure (timeout/reset/refused/DNS):
    the URL is the fact the issue found missing ("worker saw what, to which
    endpoint"). Callers keep their existing exception semantics."""
    emit_event(
        "http.error",
        {
            "url": _scrub_url(f"{host}{path}"),
            "error": str(exc) or type(exc).__name__,
            "method": method,
        },
    )


def note_http_error_response(host: str, path: str, status_code: int, body: bytes | str) -> None:
    """http.error for a non-2xx answer: the upstream code + target URL (the
    middle-502 blind spot — the Host never sees the response, only the
    Worker does). 4xx on the control plane is normal flow (claim 204 is
    success; 401/409 convert to WorkerAuthError), so callers fire this only
    for status >= 400."""
    emit_event("http.error", http_error_fields(f"{host}{path}", status_code, body))
