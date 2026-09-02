"""Bottleneck classifier for the runtime profile (#359 L2).

Turns the L1 gauges plus the existing queue-alert signal into one verdict:
a machine-readable ``stage`` plus a human-readable ``conclusion`` with the
evidence that fired. The rules are the #351 review's sawtooth
discrimination table productized, generalizing the blocked/stalled split in
``ops_metrics.queue_alert`` (which stays the input signal for claim-side
blockage).

Priority order matters: upstream starvation is checked before downstream
saturation because an empty queue makes every downstream stage look idle —
the same evidence, opposite diagnosis. DB-pool contention outranks stage
rules because it inflates every latency the stage rules compare. Inside the
deep-queue branch, claim-side blockage (the #351 discrimination table's
"blocked" row: workers claim, candidates all unclaimable — surfaced by the
empty-claim skip-reason signal / queue_alert) is checked before host-side
stage rules, because a blocked claim path makes every host stage look slow.
"""

from __future__ import annotations

from typing import Any

# Verdict thresholds. Depths are counts; rates are per second over the
# window; latencies are seconds. All are heuristics for human-readable
# triage, not alarms — the panel shows the evidence either way.
_QUEUED_DEEP = 20
_IDLE_CLAIM_RATIO = 0.8  # empty / total claims above this = workers starved
_CLAIM_LATENCY_HIGH = 0.25
_PASS_SCAN_HIGH = 5.0
_DB_POOL_WAIT_SHARE = 0.1  # pool wait / claim+result service time


def classify_bottleneck(sample: dict[str, Any], **context: Any) -> dict[str, Any]:
    """Classify the latest profile bucket; ``context`` carries what the
    gauges cannot see (online workers, queue-alert signal).

    Returns ``{"stage": ..., "conclusion": ..., "evidence": {...}}`` —
    stage ``none`` with an empty conclusion means "no bottleneck signal".
    """
    queued = _as_int(sample, "queued_depth", context.get("queued", 0))
    online = _as_int(context, "online_workers", 0)
    active = _as_int(sample, "execute_active", context.get("active_executions", 0))
    claim_count = _as_int(sample, "claim_count", 0)
    claim_empty = _as_int(sample, "claim_empty_count", 0)
    claim_avg = _avg(sample, "claim_seconds_total", claim_count)
    pool_waiting = _as_int(sample, "db_pool_waiting", 0)
    pool_wait = _as_float(sample, "db_pool_wait_seconds_total")
    service_seconds = _as_float(sample, "claim_seconds_total") + _as_float(
        sample, "result_seconds_total"
    )
    scan_max = _as_float(sample, "pass_scan_seconds_max")
    pool_skipped = _as_int(sample, "enqueue_pool_skipped", 0)
    stock_gated = _as_int(sample, "enqueue_stock_gated", 0)
    pass_slow = _as_int(sample, "pass_slow_count", 0)

    evidence: dict[str, Any] = {
        "queued": queued,
        "online_workers": online,
        "active_executions": active,
        "claim_count": claim_count,
        "claim_empty_count": claim_empty,
        "claim_avg_seconds": round(claim_avg, 4) if claim_avg is not None else None,
        "db_pool_waiting": pool_waiting,
        "enqueue_pool_skipped": pool_skipped,
        "enqueue_stock_gated": stock_gated,
        "pass_slow_count": pass_slow,
    }

    # --- upstream starvation (checked first: empty queue idles everything) --
    if queued == 0 and online > 0 and claim_count > 0:
        empty_ratio = claim_empty / claim_count if claim_count else 0.0
        if empty_ratio >= _IDLE_CLAIM_RATIO and active == 0:
            evidence["empty_claim_ratio"] = round(empty_ratio, 3)
            if _as_int(sample, "intake_items", 0) == 0:
                return _verdict(
                    "intake",
                    "上游空泡：无在途 items 且 worker 空闲——提交断流（intake 侧无新 run）",
                    evidence,
                )
            return _verdict(
                "schedule",
                "上游空泡：队列空、worker 空闲——DAG 依赖波次间隙（等待上游节点完成）",
                evidence,
            )

    # --- DB pool contention (inflates every latency below) -------------------
    if (
        pool_waiting > 0
        and service_seconds > 0
        and pool_wait / service_seconds >= _DB_POOL_WAIT_SHARE
    ):
        evidence["pool_wait_share"] = round(pool_wait / service_seconds, 3)
        return _verdict(
            "db_pool",
            "DB 连接池挤兑：claim/result 服务时间里池等待占比过高（心跳余量见 lease TTL）",
            evidence,
        )

    # --- downstream saturation needs a deep queue first -----------------------
    if queued >= _QUEUED_DEEP:
        # Blocked claim path (#351 table's blocked row): deep queue, workers
        # actively claiming but every candidate skipped — the queue-alert
        # signal (blocked) or a high empty ratio with traffic names it.
        queue_alert_kind = str(context.get("queue_alert", "") or "")
        if claim_count > 0:
            empty_ratio = claim_empty / claim_count
            if empty_ratio >= 0.5 or queue_alert_kind == "blocked":
                evidence["empty_claim_ratio"] = round(empty_ratio, 3)
                evidence["queue_alert"] = queue_alert_kind or None
                return _verdict(
                    "claim",
                    "claim 侧阻塞：队列有货但候选全部不可领取（worker 兼容性/模型/容量不匹配，"
                    "见 skip-reason 直方图与 queue_alert 信号）",
                    evidence,
                )
        if pool_skipped > 0:
            return _verdict(
                "enqueue",
                "入队池饱和：max_pending 触顶产生跳过（P1-1：调大 agent_enqueue.workers）",
                evidence,
            )
        if stock_gated > 0 and stock_gated >= max(queued // 10, 1):
            return _verdict(
                "enqueue",
                "stock 门控拒绝：agent_stock 窗口限流在拒绝提交",
                evidence,
            )
        if pass_slow > 0 or scan_max >= _PASS_SCAN_HIGH:
            evidence["pass_scan_seconds_max"] = round(scan_max, 2)
            return _verdict(
                "schedule",
                "单线程调度慢：pass 扫描阶段耗时过高（P0-2：非终态集合过大或水位失效）",
                evidence,
            )
        if claim_avg is not None and claim_avg >= _CLAIM_LATENCY_HIGH:
            return _verdict(
                "claim",
                "claim 串行瓶颈：服务端延迟高（advisory lock 等待，见 #351）",
                evidence,
            )

    return {"stage": "none", "conclusion": "", "evidence": evidence}


def _verdict(stage: str, conclusion: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"stage": stage, "conclusion": conclusion, "evidence": evidence}


def _as_int(source: dict[str, Any], key: str, default: int = 0) -> int:
    value = source.get(key, default) if isinstance(source, dict) else default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(source: dict[str, Any], key: str) -> float:
    value = source.get(key)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _avg(source: dict[str, Any], total_key: str, count: int) -> float | None:
    if count <= 0:
        return None
    return _as_float(source, total_key) / count
