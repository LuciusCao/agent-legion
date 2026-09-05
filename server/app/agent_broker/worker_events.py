"""Structured JSON-lines events for the Agent Worker claim/execution path (#490).

排障时「Worker 为什么拿不到任务 / 执行卡在哪」不能再靠 grep 访问日志拼
时间线：Host 侧把 Worker 数据面的生命周期转折点各打一条**单行 JSON**
事件（``event`` 名空间见 ``_KNOWN_EVENTS``），落点是与既有日志体系同一
个 stderr 管道——uvicorn/容器/原生部署的既有采集面原样收到，无需新
聚合面（聚合/上报是后续独立工作）。

- 事件名 ``<域>.<事件>``（``worker.registered`` / ``claim.granted`` /
  ``execution.finished`` …）；字段 ``ts``（ISO-8601 UTC）+ 事件自身载荷
  （worker_id / execution_id / job_id / workspace_id / outcome / reason /
  耗时秒），字段按事件语义取舍，不强制全量。
- ``reason`` 是重点（issue 的核心缺口）：claim 空手而归的归因直接可读。
  ``note_skip_reasons`` 把 ``claim_evaluate`` 的 skip-reason 计数
  （判定点原命名，判定逻辑零改动）折叠进 ``claim.empty`` /
  ``claim.rejected``——四大准入不匹配（并发池满 / runtime 不匹配 /
  model 未声明 / scope 拒绝）与其它 skip 原因都保留原名。
- 事件载荷的组装（``_note_*`` 系列与 execution.finished 的 wall time）
  也在本模块：发射点所在文件只留一条调用，预算治理（只降不升）把
  观测增量集中到这个新登记的模块里。
- 级别纪律（费用边界，高频路径只多一次 dict 构造 + 一条 log 调用）：
  正常节奏（claim.granted / claim.empty 无拒绝原因 / 执行完成）DEBUG；
  异常/转折（registered / offline / rejected / lease_expired / 409/500
  拒绝）INFO+。DEBUG 默认不开——排障窗口按需拉（uvicorn ``--log-level
  debug`` 或 logging config），生产日志不因高频事件膨胀。
- ``emit_worker_event`` 纯函数 + 模块级 logger，永不抛（观测不得击穿
  被观测路径）；payload 里的 secret 已在上游被 VAULT-SECRET-001 白名单
  挡住，这里不二次处理。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.agent_control.registry import (
    ONLINE_THRESHOLD_SECONDS as _ONLINE_THRESHOLD_SECONDS,
)

if TYPE_CHECKING:
    from server.app.agent_broker.claim_scan import AgentClaim, WorkerView

logger = logging.getLogger("agent_legion.worker_events")

# 事件名单空间（runbook §7 的事件码表与之一一对应；测试钉住全集）。
_KNOWN_EVENTS = frozenset(
    {
        "worker.registered",
        "worker.offline",
        "claim.granted",
        "claim.empty",
        "claim.rejected",
        "execution.started",
        "execution.finished",
        "execution.heartbeat_rejected",
        "execution.lease_expired",
    }
)

# claim_evaluate 的 skip-reason → 是否属于「准入拒绝」（有 stock 但这个
# Worker 进不来）。映射到 unclaimable_reasons / claim_scan 的判定点命名：
# capacity_full/code_capacity_full/capacity_raced = 并发池满；
# runtime_mismatch = runtime 不匹配；model_mismatch = model 未声明；
# workspace_not_allowed = scope 拒绝。其余（workspace_paused、
# execution_contract_invalid、labels_mismatch、lock_raced …）语义各异，
# 原样透传，见 runbook §7 的完整对照。
_REJECT_REASONS = frozenset(
    {
        "capacity_full",
        "code_capacity_full",
        "capacity_raced",
        "runtime_mismatch",
        "model_mismatch",
        "workspace_not_allowed",
    }
)


def emit_worker_event(event: str, payload: dict[str, Any] | None = None) -> None:
    """Log one worker-path lifecycle event as a single JSON line.

    Never raises: the observed path must survive the observer. The level
    discipline is event-intrinsic (see the module docstring); callers pass
    ``level`` only for the few events that vary (claim.empty carries
    INFO when rejection reasons are present, DEBUG otherwise).
    """
    body = {"event": event, "ts": datetime.now(UTC).isoformat(), **(payload or {})}
    line = json.dumps(body, ensure_ascii=False, default=str, sort_keys=True)
    if event not in _KNOWN_EVENTS:
        # Unknown event name is a programming slip, not a runtime condition:
        # log at WARNING so the test-side full-set pin gets noticed, but still
        # emit the line (the payload is usually the useful part).
        logger.warning("worker event with unregistered name: %s", line)
    logger.log(_event_level(event, payload), line)


def _event_level(event: str, payload: dict[str, Any] | None) -> int:
    if event == "claim.empty":
        # Empty with no skip reasons is the normal idle rhythm; reasons
        # present (blocked queue / admission mismatch) is worth INFO.
        return logging.INFO if any(payload or {}) else logging.DEBUG
    if event in ("claim.granted", "execution.started", "execution.finished"):
        return logging.DEBUG
    return logging.INFO


def note_skip_reasons(
    worker_id: str, skip_reasons: dict[str, int] | None
) -> tuple[bool, dict[str, int]]:
    """Split one empty claim's skip-reason counter into (rejected?, reasons).

    ``rejected`` = any admission-rejection reason fired (the claim had stock
    but THIS worker was not admitted). Returns the nonzero reasons as-is;
    ``claim.empty`` / ``claim.rejected`` emitters consume this directly.
    """
    reasons = {key: count for key, count in (skip_reasons or {}).items() if count}
    rejected = bool(_REJECT_REASONS & reasons.keys())
    return rejected, reasons


# ---------------------------------------------------------------------------
# Payload builders: one per emit site, so the emitting file carries a single
# call and the field selection lives next to the reason mapping above.


def note_claim_outcome(
    worker_id: str, claim: AgentClaim | None, view: WorkerView, skip_reasons: dict[str, int]
) -> None:
    """claim.granted / claim.empty / claim.rejected for one claim pass.

    Called once at each exit of the claim transaction: a claimed request
    emits granted; an empty pass folds skip_reasons into rejected (admission
    mismatch families) or empty (drained queue / non-admission skips)."""
    if claim is not None:
        # The runtime (declared by the definition, 'code' for code claims)
        # and the worker's live pool occupancy ride the event so "which
        # machine took which runtime" is one grep away.
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
                "model": str(claim.manifest.get("execution", {}).get("model") or ""),
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
            reasons = {"capacity_full": 1}
            rejected = True
        elif view.code_capacity > 0 and view.code_active >= view.code_capacity:
            reasons = {"code_capacity_full": 1}
            rejected = True
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
        payload: dict[str, Any] = {"worker_id": worker_id}
        if reasons:
            payload["reasons"] = reasons
        emit_worker_event("claim.empty", payload)


def note_execution_finished_rejected(
    execution_id: str, worker_id: str, payload: Any = None
) -> None:
    """execution.finished with outcome=rejected: the result commit rejected
    the attempt (409 — lease/ownership lost); the Host owns the outcome."""
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
    """execution.finished at the terminal commit: outcome + wall time
    (claim → committed result; the wall clock spans the Worker's whole
    execution — download/run/upload). ``outcome`` is the AgentOutcome; a
    missing claimed_at (read post-done) omits wall_seconds."""
    started_at = claimed_at(dsn, execution_id)
    wall = (
        round((datetime.now(UTC) - started_at).total_seconds(), 3)
        if started_at is not None
        else None
    )
    emit_worker_event(
        "execution.finished",
        {
            "worker_id": worker_id,
            "execution_id": execution_id,
            "job_id": str(payload["job_id"]),
            "outcome": str(outcome.status),
            "exit_code": int(outcome.exit_code),
            "wall_seconds": wall,
        },
    )


def claimed_at(dsn: Any, execution_id: str) -> datetime | None:
    """When the current attempt was claimed, for the finished wall time.

    Reads best-effort AFTER mark_done flipped the row to 'done' — a read now
    may miss; the caller treats None as "omit wall_seconds"."""
    from server.app.db.dialect import ConnectSource
    from server.app.db.transaction import read_connection

    source: ConnectSource = dsn
    with read_connection(source) as conn:
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
    """execution.heartbeat_rejected: the heartbeat was refused (not_owned /
    lease_not_active) — the Worker must stop beating for this execution."""
    emit_worker_event(
        "execution.heartbeat_rejected",
        {"worker_id": worker_id, "execution_id": execution_id, "reason": reason},
    )


def note_worker_registered(payload: Any, scope: list[dict[str, Any]]) -> None:
    """worker.registered: the registration's runtime version matrix,
    concurrency declarations and resolved workspace scope, emitted right
    after the registration transaction commits («哪台机器何时以什么配置
    出现» stops being reverse-engineered from log traffic)."""
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


def as_utc(value: Any) -> datetime | None:
    """Coerce a last_seen cell (datetime / ISO string / naive) to aware UTC;
    None for missing or unparseable values (the #490 offline fold treats
    None as "not online")."""
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

    State memo is process-local: after a Host restart the first bucket only
    seeds the memo (no event), so a restart never fabricates an offline
    storm. Deleted/revoked workers leave the memo silently — their
    disappearance is a management action, not a health event.
    """

    def __init__(self) -> None:
        # worker_id -> the sample time it was last seen online.
        self._known_workers: dict[str, datetime] = {}

    def note(self, sampled_at: datetime, worker_last_seen: dict[str, Any]) -> None:
        """Fold one sampling bucket's last_seen map into transitions."""
        try:
            online_now = {k for k, v in worker_last_seen.items() if as_utc(v) is not None}
            for worker_id in online_now - self._known_workers.keys():
                self._known_workers[worker_id] = sampled_at
            for worker_id in sorted(self._known_workers.keys() - online_now):
                last_seen = self._known_workers.pop(worker_id)
                emit_worker_event(
                    "worker.offline",
                    {
                        "worker_id": worker_id,
                        "last_seen_at": last_seen.isoformat(),
                        "threshold_seconds": _ONLINE_THRESHOLD_SECONDS,
                    },
                )
        except Exception:
            # #204 broad-except audit: the offline detector is a rider on
            # the ops sampling pass, not part of its contract — a failure
            # here must not lose the samples the caller just committed. The
            # outcome space is datetime parsing of DB rows already read;
            # swallowing only drops this bucket's memo update (the next
            # bucket rebuilds it). No log: the ops sample write is the
            # pass's own success signal; a persistent failure shows up as a
            # missing offline event.
            pass


_offline_detector = WorkerOfflineDetector()


def note_worker_offline(sampled_at: datetime, worker_last_seen: dict[str, Any]) -> None:
    """Module-level fold entry (one detector per Host process)."""
    _offline_detector.note(sampled_at, worker_last_seen)


def note_lease_expired(row: Any, requeue_limit: int) -> None:
    """execution.lease_expired: the Worker went silent past the lease TTL;
    attempt/requeue_limit answer "will it rerun here?"."""
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
