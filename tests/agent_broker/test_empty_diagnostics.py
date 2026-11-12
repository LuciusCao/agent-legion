"""#1239 A2：blocked-queue 信号落库移出读块的契约钉子。

池 1 形态（AGENT_LEGION_DB_POOL_MAX_SIZE=1）下 blocked 分支必须把信号
落进 agent_queue_signals 且不抛 PoolTimeout：旧实现在持读连接期间开写
事务，嵌套 checkout 白等 10s 后 PoolTimeout 传播进 Worker claim 响应路
径。修复后队列头采样骑读连接、信号落库在读连接释放后走自己的写事务。
"""

from __future__ import annotations

import json
import uuid

from server.app.agent_broker.empty import EmptyClaimTrigger
from server.app.db.transaction import read_connection, write_transaction
from server.app.jobs import JobQueries


def _pool_one_dsn(job_db: JobQueries) -> str:
    # 独立 application_name = 独立池键，env 在池创建前生效。
    return f"{job_db.dsn_identity}&application_name=m18-a2-{uuid.uuid4().hex[:12]}"


def test_note_empty_claim_blocked_signal_survives_pool_size_one(job_db, monkeypatch) -> None:
    """池 1 下 blocked 分支不抛 PoolTimeout 且信号落库（嵌套 checkout 形态下
    本用例红：10s checkout timeout 后 PoolTimeout 传播、信号缺失）。"""
    monkeypatch.setenv("AGENT_LEGION_DB_POOL_MAX_SIZE", "1")
    dsn = _pool_one_dsn(job_db)
    with write_transaction(dsn) as conn:
        conn.execute("insert into workspaces(id, name) values (%s, 'Ops')", ("a2-ws",))
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values (%s, %s, 'question', %s)",
            ("a2-job", "a2-ws", "a2-job"),
        )
        conn.execute(
            "insert into agent_execution_requests("
            "execution_id, workspace_id, job_id, node_key, agent_id,"
            " agent_definition_hash, node_concurrency_limit, state, queued_at, manifest_json)"
            " values ('a2-ex', 'a2-ws', 'a2-job', 'node-a', 'agent-1', 'hash', 5,"
            " 'queued', current_timestamp, '{}')"
        )

    trigger = EmptyClaimTrigger(debounce_seconds=0.0)
    trigger.note_empty_claim(dsn, skip_reasons={"model_mismatch": 3})

    with read_connection(dsn) as conn:
        row = conn.execute(
            "select kind, reasons_json from agent_queue_signals where id=1"
        ).fetchone()
    assert row is not None
    assert row["kind"] == "blocked"
    assert json.loads(row["reasons_json"]) == {"model_mismatch": 3}
