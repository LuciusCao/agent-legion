"""Structured JSON-lines events for the Agent Worker claim/execution path (#490).

排障时「Worker 为什么拿不到任务 / 执行卡在哪」不能再靠 grep 访问日志拼
时间线：Host 侧把 Worker 数据面的生命周期转折点各打一条**单行 JSON**
事件（名空间见 ``_KNOWN_EVENTS``），落点是与既有日志体系同一 stderr
管道，既有采集面（uvicorn/容器/原生）原样收到；聚合/上报是后续工作。

- 事件名 ``<域>.<事件>``；字段 ``ts``（ISO-8601 UTC）+ 语义载荷
  （worker_id / execution_id / job_id / outcome / reason / 耗时秒）。
- ``reason`` 是重点（issue 核心缺口）：``note_skip_reasons`` 把
  ``claim_evaluate`` 的 skip-reason 计数（判定点原命名，判定逻辑零改动）
  折叠进 ``claim.empty`` / ``claim.rejected``——四大准入不匹配（并发池满
  / runtime 不匹配 / model 未声明 / scope 拒绝）与其它原因保留原名。
- 载荷组装（``_note_*`` 系列）也在本模块：发射点文件只留一条调用，
  预算治理把观测增量集中到这个新登记的模块。
- 级别纪律（费用边界）：正常节奏（claim.granted / claim.empty（含非
  拒绝原因，如 workspace_paused——dev 形态所有 workspace 重置为暂停，
  升 INFO 就是每分钟一条噪音）/ 执行完成）DEBUG；异常/转折（registered
  / offline / rejected / lease_expired / 409/500 拒绝）INFO+，默认不开。
- ``emit_worker_event`` 纯函数 + 模块级 logger，永不抛（观测不得击穿
  被观测路径）；secret 已在上游被 VAULT-SECRET-001 白名单挡住。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.agent_control.registry import (
    ONLINE_THRESHOLD_SECONDS as _ONLINE_THRESHOLD_SECONDS,
)

# Re-export for tests/sibling importers; registry is the single source.
ONLINE_THRESHOLD_SECONDS = _ONLINE_THRESHOLD_SECONDS

if TYPE_CHECKING:
    from server.app.agent_broker.claim_scan import AgentClaim, WorkerView

logger = logging.getLogger("agent_legion.worker_events")

# 事件名单空间（runbook §7 的事件码表与之一一对应；测试钉住全集）。
_KNOWN_EVENTS = frozenset(
    [
        "worker.registered",
        "worker.register_rejected",
        "worker.offline",
        "claim.granted",
        "claim.empty",
        "claim.rejected",
        "execution.started",
        "execution.finished",
        "execution.heartbeat_rejected",
        "execution.lease_expired",
    ]
)

# claim_evaluate 的 skip-reason → 是否属于「准入拒绝」（有 stock 但这个
# Worker 进不来）。映射到 unclaimable_reasons / claim_scan 的判定点命名：
# capacity_full/code_capacity_full/capacity_raced = 并发池满；
# runtime_mismatch = runtime 不匹配；model_mismatch = model 未声明；
# workspace_not_allowed = scope 拒绝。其余（workspace_paused、
# execution_contract_invalid、labels_mismatch、lock_raced …）语义各异，
# 原样透传，见 runbook §7 的完整对照。
_REJECT_REASONS = frozenset(
    [
        "capacity_full",
        "code_capacity_full",
        "capacity_raced",
        "runtime_mismatch",
        "model_mismatch",
        "workspace_not_allowed",
    ]
)


def emit_worker_event(event: str, payload: dict[str, Any] | None = None) -> None:
    """Log one lifecycle event as a single JSON line; never raises (levels
    are event-intrinsic, see the module docstring)."""
    body = {"event": event, "ts": datetime.now(UTC).isoformat(), **(payload or {})}
    line = json.dumps(body, ensure_ascii=False, default=str, sort_keys=True)
    if event not in _KNOWN_EVENTS:
        # Unknown event name is a programming slip, not a runtime condition:
        # WARN with the full line (payload included) and drop the second
        # emission — a duplicate INFO copy of the same line is pure noise
        # (#494 review P2: the double emission rode the visibility fix).
        logger.warning("worker event with unregistered name: %s", line)
        return
    logger.log(_event_level(event, payload), line)


def _event_level(event: str, payload: dict[str, Any] | None) -> int:
    # The normal rhythm stays DEBUG: claim.granted / execution.finished, and
    # claim.empty WITH non-rejection reasons — admission rejections route to
    # claim.rejected (INFO) instead, while the skips riding claim.empty
    # (workspace_paused — in dev every workspace is reset paused —,
    # lock_raced, job_paused …) are the blocked-queue diagnosis an operator
    # pulls debug for, not a per-minute INFO rhythm.
    if event in ("claim.empty", "claim.granted", "execution.started", "execution.finished"):
        return logging.DEBUG
    return logging.INFO


def note_skip_reasons(
    worker_id: str, skip_reasons: dict[str, int] | None
) -> tuple[bool, dict[str, int]]:
    """Split one empty claim's skip-reason counter into (rejected?, reasons):
    rejected = an admission-rejection reason fired; nonzero reasons pass
    through unchanged."""
    reasons = {key: count for key, count in (skip_reasons or {}).items() if count}
    rejected = bool(_REJECT_REASONS & reasons.keys())
    return rejected, reasons


# ---------------------------------------------------------------------------
# Payload builders: one per emit site, so the emitting file carries a single
# call and the field selection lives next to the reason mapping above.


def note_claim_outcome(
    worker_id: str, claim: AgentClaim | None, view: WorkerView, skip_reasons: dict[str, int]
) -> None:
    """claim.granted / claim.empty / claim.rejected per claim pass, one call
    at each claim-transaction exit (rejected = admission mismatch / empty =
    drained queue or non-admission skips)."""
    if claim is not None:
        # `execution` may be missing, None or a non-mapping (the manifest is
        # caller-built JSON) — the observer must never raise into the claim
        # transaction's exits.
        execution = claim.manifest.get("execution")
        emit_worker_event(
            "claim.granted",
            {
                "worker_id": worker_id,
                "execution_id": claim.execution_id,
                "job_id": claim.job_id,
                "workspace_id": claim.workspace_id,
                "node_key": claim.node_key,
                "kind": claim.kind,
                "runtime": claim.runtime or str(claim.manifest.get("runtime") or ""),
                "model": str(execution.get("model") or "") if isinstance(execution, dict) else "",
                "agent_active": view.agent_active,
                "code_active": view.code_active,
            },
        )
        return
    rejected, reasons = note_skip_reasons(worker_id, skip_reasons)
    if not rejected and not reasons:
        # The scan was skipped entirely (both pools at their cap, or only
        # code headroom on a pre-v2 worker): the worker's own live pool
        # state IS the admission reason — classify from the view. A pool
        # with zero DECLARED capacity (0 >= 0) is not "full": the worker
        # never advertised that lane at all.
        if view.agent_capacity > 0 and view.agent_active >= view.agent_capacity:
            reasons, rejected = {"capacity_full": 1}, True
        elif view.code_capacity > 0 and view.code_active >= view.code_capacity:
            reasons, rejected = {"code_capacity_full": 1}, True
    if rejected:
        emit_worker_event(
            "claim.rejected",
            {
                "worker_id": worker_id,
                "reasons": reasons,
                "agent_active": view.agent_active,
                "agent_capacity": view.agent_capacity,
                "code_active": view.code_active,
                "code_capacity": view.code_capacity,
            },
        )
    else:
        # Empty with no reasons is the plain idle rhythm; reasons present
        # (blocked queue head / non-admission skips) ride along either way.
        emit_worker_event(
            "claim.empty",
            {"worker_id": worker_id, **({"reasons": reasons} if reasons else {})},
        )


def note_execution_finished_rejected(
    execution_id: str, worker_id: str, payload: Any = None
) -> None:
    """execution.finished outcome=rejected: the commit rejected the attempt
    (409 — lease/ownership lost); the Host owns the outcome."""
    emit_worker_event(
        "execution.finished",
        {
            "worker_id": worker_id,
            "execution_id": execution_id,
            "job_id": str(payload["job_id"]) if payload else "",
            "outcome": "rejected",
            "reason": "not_owned",
        },
    )


def note_execution_finished(
    execution_id: str, worker_id: str, payload: Any, outcome: Any, dsn: Any
) -> None:
    """execution.finished at the terminal commit: outcome + wall time (claim
    → committed result, the whole download/run/upload)."""
    started_at = claimed_at(dsn, execution_id)
    emit_worker_event(
        "execution.finished",
        {
            "worker_id": worker_id,
            "execution_id": execution_id,
            "job_id": str(payload["job_id"]),
            "outcome": str(outcome.status),
            "exit_code": int(outcome.exit_code),
            "wall_seconds": (
                round((datetime.now(UTC) - started_at).total_seconds(), 3)
                if started_at is not None
                else None
            ),
        },
    )


def claimed_at(dsn: Any, execution_id: str) -> datetime | None:
    """When the current attempt was claimed (for wall time). Best-effort read
    AFTER mark_done flipped the row — may miss; None omits wall_seconds."""
    from server.app.db.transaction import read_connection

    with read_connection(dsn) as conn:
        row = conn.execute(
            "select claimed_at from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    value = row["claimed_at"] if row is not None else None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


def note_heartbeat_rejected(execution_id: str, worker_id: str, reason: str) -> None:
    """execution.heartbeat_rejected: refused (not_owned / lease_not_active)
    — the Worker must stop beating for this execution."""
    emit_worker_event(
        "execution.heartbeat_rejected",
        {"worker_id": worker_id, "execution_id": execution_id, "reason": reason},
    )


def note_worker_registered(payload: Any, scope: list[dict[str, Any]]) -> None:
    """worker.registered: runtime version matrix, concurrency declarations
    and resolved workspace scope, right after the registration commits
    («哪台机器何时以什么配置出现» leaves the logs)."""
    emit_worker_event(
        "worker.registered",
        {
            "worker_id": str(payload.worker_id),
            "name": str(payload.name),
            "protocol_version": int(payload.protocol_version),
            "runtimes": list(payload.runtimes),
            "runtime_versions": dict(payload.runtime_versions or {}),
            "max_concurrency": int(payload.max_concurrency),
            "max_code_concurrency": int(payload.max_code_concurrency),
            "workspace_ids": sorted({str(row["workspace_id"]) for row in scope}),
        },
    )


def note_worker_register_rejected(
    payload: Any, reason: str, min_protocol_version: int | None
) -> None:
    """worker.register_rejected (#494 review P2): the register endpoint
    refused the Worker (400/401) — pre-claim counterpart of worker.registered."""
    try:
        fields = {
            "worker_id": str(payload.worker_id),
            "name": str(payload.name),
            "protocol_version": int(payload.protocol_version),
            "reason": reason,
        }
        if min_protocol_version is not None:
            fields["min_protocol_version"] = int(min_protocol_version)
        emit_worker_event("worker.register_rejected", fields)
    except Exception:
        # #204 broad-except audit: never-raise contract — a malformed
        # payload must not replace the caller's HTTP error.
        pass


def as_utc(value: Any) -> datetime | None:
    """Coerce a last_seen cell (datetime / ISO string / naive) to aware UTC;
    None for missing/unparseable (the fold reads None as "not online")."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value  # type: ignore[no-any-return]


class WorkerOfflineDetector:
    """#490 worker.offline: one event per online→offline transition.

    ``note`` gets the production shape — the FULL unrevoked last_seen map
    plus the ``online_since`` threshold the online count applies: still in
    the map but past the threshold = offline (#494 P0: presence-only never
    fired in production). The memo stores the true DB last_seen_at; leaving
    the map entirely (revoked/deleted) drops silently — management, not health.
    """

    def __init__(self) -> None:
        # worker_id -> the DB last_seen_at it was last observed online with.
        self._known_workers: dict[str, datetime] = {}

    def note(self, worker_last_seen: dict[str, Any], online_since: datetime) -> None:
        """Fold one sampling bucket's full last_seen map into transitions."""
        try:
            online_now = {
                worker_id: seen_at
                for worker_id, last_seen in worker_last_seen.items()
                if (seen_at := as_utc(last_seen)) is not None and seen_at >= online_since
            }
            for worker_id, seen_at in online_now.items():
                self._known_workers.setdefault(worker_id, seen_at)
            for worker_id in sorted(self._known_workers.keys() - online_now.keys()):
                last_seen = self._known_workers.pop(worker_id)
                # Off-map entirely (row revoked/deleted) = management action,
                # not a health event — drop silently; on-map but stale = the
                # threshold crossing this detector exists to name.
                if worker_id in worker_last_seen:
                    emit_worker_event(
                        "worker.offline",
                        {
                            "worker_id": worker_id,
                            "last_seen_at": last_seen.isoformat(),
                            "threshold_seconds": _ONLINE_THRESHOLD_SECONDS,
                        },
                    )
        except Exception:
            # #204 broad-except audit: a rider on the ops sampling pass, not
            # part of its contract — a failure must not lose the samples the
            # caller committed. Outcome space: datetime parsing of rows
            # already read; the next bucket rebuilds the memo. No log: a
            # persistent failure shows up as a missing offline event.
            pass


_offline_detector = WorkerOfflineDetector()


def note_worker_offline(worker_last_seen: dict[str, Any], online_since: datetime) -> None:
    """Module-level fold entry; ``online_since`` = the caller's threshold."""
    _offline_detector.note(worker_last_seen, online_since)


def note_lease_expired(row: Any, requeue_limit: int) -> None:
    """execution.lease_expired: silent past the lease TTL; attempt /
    requeue_limit answer "will it rerun here?"."""
    emit_worker_event(
        "execution.lease_expired",
        {
            "worker_id": str(row["worker_id"]),
            "execution_id": str(row["execution_id"]),
            "job_id": str(row["job_id"]),
            "workspace_id": str(row["workspace_id"]),
            "attempt": int(row["attempt"]),
            "requeue_limit": requeue_limit,
        },
    )
