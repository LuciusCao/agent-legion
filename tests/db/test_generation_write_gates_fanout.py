"""EXEC-GENERATION-001 空 fan-out 完成面的代次闸（#645 P3）。

自 test_generation_write_gates.py 拆出（#779 codex 列车复审 P1-4——文件
超 800 拆分线，按写面拆成 upload/fanout/finish 三姊妹文件，用例零改动
迁移）。共享种子/同步工具见 tests/db/generation_write_gate_helpers.py。

P3（空 fan-out 完成无 CAS）：``complete_empty_shard_node`` 的锁 + 代次 CAS
保证 reset 交错时新代次的 pending 节点不被无执行翻成 completed；
``materialize_shards_guarded`` 把锁提到 node_shards 行写之前（锁序：
job-mutation advisory → 行锁；mutation 侧持同锁删 shard 行）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from server.app.db.transaction import write_transaction
from server.app.executors._lease_shards import complete_empty_shard_node
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import lease_guarded_mutation, mark_nodes_for_rerun
from server.app.workflow_worker.shard_fanout import materialize_shards_guarded
from tests.db.generation_write_gate_helpers import (
    TIMED_DATABASE_URL,
    _await_job_mutation_waiter,
    _job_row,
    _join,
    _node_row,
    _seed_job,
    _start,
)
from tests.postgres_support import TEST_DATABASE_URL


def test_empty_shard_completion_rejected_after_reset(job_db: JobQueries) -> None:
    """reset bump 代次后，旧代次的空 fan-out 完成被 CAS 拒绝：节点保持
    pending（新戳），不被无执行翻成 completed；按新代次调用则照常完成。"""
    _seed_job(job_db, workspace_id="gate5-ws", job_id="gate5-job")
    with lease_guarded_mutation(
        TEST_DATABASE_URL, "gate5-job", datetime.now(UTC), reject_running_nodes=True
    ) as conn:
        mark_nodes_for_rerun(conn, "gate5-job", ["node_a"], {"node_a": []})
    assert _job_row("gate5-job")["execution_generation"] == 1

    with write_transaction(TEST_DATABASE_URL) as conn:
        applied = complete_empty_shard_node(conn, "gate5-job", "node_a", 0)

    assert applied is False
    node = _node_row("gate5-job", "node_a")
    assert node["status"] == "pending"
    assert int(node["execution_generation"]) == 1

    with write_transaction(TEST_DATABASE_URL) as conn:
        applied = complete_empty_shard_node(conn, "gate5-job", "node_a", 1)
    assert applied is True
    assert _node_row("gate5-job", "node_a")["status"] == "completed"


def test_guarded_fanout_waits_on_mutation_lock_then_skips(job_db: JobQueries) -> None:
    """交错用例（锁序 + CAS）：mutation 持 job-mutation 锁未提交时，guard 的
    物化事务必须先等锁（证明 advisory 锁先于 node_shards 行写），mutation
    提交（代次 → 1）后读到代次不符整段跳过——不物化、不完成，节点保持
    pending 新戳。最终状态 == 串行序「reset → 旧代次 fan-out 被跳过」。"""
    _seed_job(job_db, workspace_id="gate6-ws", job_id="gate6-job")
    with lease_guarded_mutation(
        TIMED_DATABASE_URL, "gate6-job", datetime.now(UTC), reject_running_nodes=True
    ) as conn_a:
        mark_nodes_for_rerun(conn_a, "gate6-job", ["node_a"], {"node_a": []})

        def _late_fanout() -> None:
            with write_transaction(TIMED_DATABASE_URL) as conn_b:
                # 非空输入：只有 wrapper 的前置闸能拦住物化（空输入会落到
                # complete_empty_shard_node 的内层 CAS，测不到闸的锁序）。
                materialize_shards_guarded(conn_b, "gate6-job", "node_a", [{"i": 0}], 4, 0)

        thread, outcome = _start(_late_fanout)
        _await_job_mutation_waiter("gate6-job")  # B 卡在 advisory 锁上（未写行）
    _join(thread)

    assert outcome.get("error") is None
    with write_transaction(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select count(*) as cnt from node_shards where job_id='gate6-job'"
        ).fetchone()
    assert int(row["cnt"]) == 0  # 未物化
    node = _node_row("gate6-job", "node_a")
    assert node["status"] == "pending"  # 未被无执行翻成 completed
    assert int(node["execution_generation"]) == 1


def test_guarded_fanout_materializes_and_completes_on_current_epoch(
    job_db: JobQueries,
) -> None:
    """对照组：代次一致时物化与空 fan-out 完成照常（空输入 → 节点完成；
    非空输入 → shard 行落库、节点保持 pending 待认领）。"""
    _seed_job(job_db, workspace_id="gate7-ws", job_id="gate7-job")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("insert into job_nodes(job_id, node_key) values ('gate7-job', 'node_b')")
        materialize_shards_guarded(conn, "gate7-job", "node_a", [], 4, 0)
        materialize_shards_guarded(conn, "gate7-job", "node_b", [{"i": 0}, {"i": 1}], 4, 0)

    assert _node_row("gate7-job", "node_a")["status"] == "completed"
    assert _node_row("gate7-job", "node_b")["status"] == "pending"
    with write_transaction(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select shard_index, status from node_shards"
            " where job_id='gate7-job' and node_key='node_b' order by shard_index"
        ).fetchall()
    assert [dict(row) for row in rows] == [
        {"shard_index": 0, "status": "pending"},
        {"shard_index": 1, "status": "pending"},
    ]
