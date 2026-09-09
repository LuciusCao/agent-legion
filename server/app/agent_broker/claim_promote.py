"""Claim promote write sequence (split from ``claim_evaluate.py``, #551 budget).

After a candidate passes admission and its job/node flips succeed, this
module owns the rest of the write transaction: the node_runs insert, the
executor lease, the request row's claimed flip, the jobs promote (since
#555 narrowed to the queued→running transition — see below), and — since
#551 — the queue-wait gauge fold, which belongs exactly here because the
scan row's ``queued_at`` is only visible on this path.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.agent_broker.claim_paths import claim_log_path
from server.app.agent_broker.claim_scan import ClaimRacedError
from server.app.services.runtime_profile import profile

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker


def promote_claim(
    broker: AgentExecutionBroker,
    conn: Any,
    worker_id: str,
    selected: Mapping[str, Any],
    manifest: dict[str, Any],
    kind: str,
) -> tuple[str, int]:
    """Write the claim: run row, lease, request flip, job promote.

    Returns ``(lease_id, node_run_id)``; raises ``ClaimRacedError`` when the
    job left the runnable set mid-claim (the caller's transaction — or the
    batch's savepoint, #546 — rolls the attempt back). Must run inside the
    caller's write transaction.
    """
    log_path = claim_log_path(manifest, broker.data_dir)
    # Dispatch-time config audit (CONFIG-RUNTIME-MUTABLE-001): the manifest
    # config is the non-secret resolved config built at enqueue on the Host —
    # frozen keys repeat the intake snapshot, runtime_mutable keys carry the
    # enqueue-time re-resolution. Secret values never enter the manifest
    # (CONFIG-MANIFEST-001), so this is safe to persist.
    config_snapshot_json = json.dumps(manifest.get("config") or {}, sort_keys=True, default=str)
    run = conn.execute(
        """
        insert into node_runs(
          job_id, node_key, status, command_json, log_path, run_dir, session_dir,
          started_at, config_snapshot_json
        ) values (%s, %s, 'running', '[]', %s, '', '', current_timestamp, %s)
        returning id
        """,
        (selected["job_id"], selected["node_key"], log_path, config_snapshot_json),
    ).fetchone()
    if run is None:
        raise RuntimeError("node run insert did not return an id")
    lease_id = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(seconds=broker.lease_ttl_seconds)
    # Code leases share the 'agent:' prefix so the generic lease sweeper
    # keeps leaving them to the Agent broker sweep (requeue semantics).
    executor_id = (
        f"agent:code:{selected['agent_id']}" if kind == "code" else f"agent:{selected['agent_id']}"
    )
    conn.execute(
        """
        insert into executor_leases(
          id, execution_id, executor_id, workspace_id, job_id,
          node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
        ) values (%s, %s, %s, %s, %s, %s, %s, 'active', current_timestamp, current_timestamp, %s)
        """,
        (
            lease_id,
            selected["execution_id"],
            executor_id,
            selected["workspace_id"],
            selected["job_id"],
            selected["node_key"],
            run["id"],
            expires_at,
        ),
    )
    conn.execute(
        """
        update agent_execution_requests set
          state='claimed', worker_id=%s, lease_id=%s, node_run_id=%s,
          attempt=attempt+1, claimed_at=current_timestamp, heartbeat_at=current_timestamp
        where execution_id=%s and state='queued'
        """,
        (worker_id, lease_id, run["id"], selected["execution_id"]),
    )
    # #555: 收窄为 queued→running 的真实跃迁——多节点 job 的每个后继节点
    # claim 都曾对同一 jobs 行做「值不变的重写+重锁」，与 result commit 侧
    # 同批 job 的写正面相撞。已 running 的 job 无需再写。
    promoted = conn.execute(
        "update jobs set status='running', updated_at=current_timestamp"
        " where id=%s and status='queued' and execution_paused=0",
        (selected["job_id"],),
    )
    if promoted.rowcount == 0:
        # rowcount 0 的两种语义必须区分：job 已被本 job 前序节点的 claim
        # promote 为 running（稳态放行，无写）vs job 在 claim 中途离开
        # runnable 集（raced——回滚整个 claim，请求保持 queued 而不是复活
        # job）。重读判定；重读后再落 pause 的窗口与旧版条件 UPDATE 的相同
        # （判读与 pause 从不原子），不放大结果空间。
        job = conn.execute(
            "select status, execution_paused from jobs where id=%s", (selected["job_id"],)
        ).fetchone()
        if job is None or job["status"] != "running" or job["execution_paused"]:
            raise ClaimRacedError()
    # #551：供给延迟观测——queued_at→promote 的 queue_wait 直接 fold 进 claim
    # 画像族（per-promote 一条，批内每个 promote 各自计入；与 #448 阶段计时
    # 同为事务内 best-effort gauge，不是事件，不适用 #498 post-commit 纪律）。
    profile.note_claim_stages({"queue_wait": _queue_wait_seconds(selected["queued_at"])})
    return lease_id, int(run["id"])


def _queue_wait_seconds(queued_at: Any) -> float:
    """queued_at 经连接层的 row 工厂可能是 str（worker_events.claimed_at 的
    同款先例）——归一为 aware datetime 再取差。"""
    if isinstance(queued_at, str):
        queued_at = datetime.fromisoformat(queued_at)
    if queued_at.tzinfo is None:
        queued_at = queued_at.replace(tzinfo=UTC)
    # max 钳制：时钟回拨不产生负值污染 total（fold 只挡 0）。
    return max(0.0, float((datetime.now(UTC) - queued_at).total_seconds()))
