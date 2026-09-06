"""Structured JSON-lines events for the Agent Worker claim/execution path (#490).

排障时「Worker 为什么拿不到任务 / 执行卡在哪」不再靠 grep 访问日志拼时间
线：Host 侧把 Worker 数据面生命周期转折点各打一条单行 JSON 事件（名空间见
``_KNOWN_EVENTS``），落点与既有日志同一 stderr 管道，聚合/上报是后续工作。

- 事件名 ``<域>.<事件>``；字段 ``ts``（ISO-8601 UTC）+ 语义载荷。
- ``reason`` 是重点（issue 核心缺口）：``note_skip_reasons`` 把
  ``claim_evaluate`` 的 skip-reason 计数折叠进 ``claim.empty`` /
  ``claim.rejected``，判定点命名保留原名（完整对照见 runbook §7）。
- 级别纪律（费用边界）：正常节奏（granted / empty（含非拒绝原因）/ 执行
  完成 / 纯容量饱和拒绝——#5125358408）DEBUG；转折（registered / offline
  / 错配类 rejected / lease_expired / 409/500 拒绝）INFO+，默认不开。
- ``emit_worker_event`` 永不抛（观测不得击穿被观测路径）；secret 已在上游
  被 VAULT-SECRET-001 白名单挡住。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.agent_control.registry import ONLINE_THRESHOLD_SECONDS

if TYPE_CHECKING:
    from server.app.agent_broker.claim_scan import AgentClaim, WorkerView

logger = logging.getLogger("agent_legion.worker_events")

# 事件名单空间（runbook §7 的事件码表与之一一对应；测试钉住全集）。
_KNOWN_EVENTS = frozenset(
    (
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
    )
)

# claim_evaluate 的 skip-reason → 是否属于「准入拒绝」（有 stock 但这个
# Worker 进不来）。映射到 unclaimable_reasons / claim_scan 的判定点命名：
# capacity_full/code_capacity_full/capacity_raced = 并发池满；
# runtime_mismatch = runtime 不匹配；model_mismatch = model 未声明；
# workspace_not_allowed = scope 拒绝。其余（workspace_paused、
# execution_contract_invalid、labels_mismatch、lock_raced …）语义各异，
# 原样透传，见 runbook §7 的完整对照。
_REJECT_REASONS = frozenset(
    (
        "capacity_full",
        "code_capacity_full",
        "capacity_raced",
        "runtime_mismatch",
        "model_mismatch",
        "workspace_not_allowed",
    )
)

# 饱和拒绝（#5125358408 P1-A）：纯容量类拒绝是饱和稳态节奏（每 poll 一
# 条），与 workspace_paused 的 DEBUG 先例同族；错配类保持 INFO。
_SATURATION_REASONS = frozenset(("capacity_full", "code_capacity_full", "capacity_raced"))


def _render_event(event: str, payload: dict[str, Any] | None) -> str:
    body = {"event": event, "ts": datetime.now(UTC).isoformat(), **(payload or {})}
    return json.dumps(body, ensure_ascii=False, default=str, sort_keys=True)


def emit_worker_event(event: str, payload: dict[str, Any] | None = None) -> None:
    """Log one lifecycle event as a single JSON line; never raises. The
    ``isEnabledFor`` check gates the ``json.dumps`` too — the sweep still
    emits under row locks (PR #497 review)."""
    if event not in _KNOWN_EVENTS:
        # Unknown event name is a programming slip, not a runtime condition:
        # WARN with the full line (payload included) and drop the second
        # emission — a duplicate INFO copy of the same line is pure noise
        # (#494 review P2: the double emission rode the visibility fix).
        logger.warning("worker event with unregistered name: %s", _render_event(event, payload))
        return
    level = _event_level(event, payload)
    if logger.isEnabledFor(level):
        logger.log(level, _render_event(event, payload))


def _event_level(event: str, payload: dict[str, Any] | None) -> int:
    # The normal rhythm stays DEBUG: claim.granted / execution.finished (a
    # committed outcome), and claim.empty WITH non-rejection reasons —
    # admission rejections route to claim.rejected (INFO) instead, while the
    # skips riding claim.empty (workspace_paused — in dev every workspace is
    # reset paused —, lock_raced, job_paused …) are the blocked-queue
    # diagnosis an operator pulls debug for, not a per-minute INFO rhythm.
    if event == "execution.finished" and (payload or {}).get("outcome") == "rejected":
        # A rejected terminal commit is a turn (#494 P2): the Host refused the
        # attempt (409) and this line is the LAST Host-side clue about that
        # execution — the Worker's own http.error is INFO on its side — so it
        # must stay visible at the default level, not sink with the rhythm.
        return logging.INFO
    if event == "claim.rejected":
        # Saturation is rhythm, not a turn (#5125358408 P1-A): capacity_full
        # fires every poll on a saturated worker — the same shape that keeps
        # workspace_paused at DEBUG. Misconfigurations (model/runtime/scope)
        # and mixed lines stay INFO.
        reasons = (payload or {}).get("reasons") or {}
        if reasons and set(reasons) <= _SATURATION_REASONS:
            return logging.DEBUG
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

# Heartbeat refusal reasons (#499): shared literals so the single and batch
# renewal paths cannot drift (runbook §7 names them).
HEARTBEAT_NOT_OWNED = "not_owned"
HEARTBEAT_LEASE_NOT_ACTIVE = "lease_not_active"


def note_claim_outcome(
    worker_id: str,
    claim: AgentClaim | None,
    view: WorkerView,
    skip_reasons: dict[str, int],
    *,
    scan_skipped: bool = False,
) -> None:
    """claim.granted / claim.empty / claim.rejected per COMMITTED claim pass
    — the broker calls this after the transaction commits (#498); rejected =
    admission mismatch / empty = drained queue or non-admission skips;
    scan_skipped = see the synthesis branch below."""
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
    if scan_skipped and not rejected and not reasons:
        # The scan never ran, so no skip reason fired: the worker's own live
        # pool state IS the admission reason — classify from the view. A
        # zero-DECLARED-capacity pool (0 >= 0) is not "full": that lane was
        # never advertised. Synthesizing on any other pass would misattribute.
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
    try:
        started_at = claimed_at(dsn, execution_id)
    except Exception:
        # #204 broad-except audit: claimed_at opens its own read connection
        # AFTER mark_done committed the result — pool exhaustion or a failed
        # query must not turn the caller's committed 204 into a 500 (this
        # module's never-raise contract); the failure costs wall_seconds only.
        started_at = None
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
                # Overwrite, not setdefault: a worker online across buckets
                # keeps refreshing its DB last_seen, and the offline event
                # must carry the LAST one (setdefault froze the first — hours
                # or days early — and broke the outage timeline).
                self._known_workers[worker_id] = seen_at
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
                            "threshold_seconds": ONLINE_THRESHOLD_SECONDS,
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
