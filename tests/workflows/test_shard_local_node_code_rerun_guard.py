"""Shard dispatch rerun-generation protection (#520 review P1).

从 ``test_shard_local_node_code.py`` 按主题拆出的姊妹文件（AGENTS.md §4：
超 800 行按被测主题拆姊妹文件，用例零改动迁移）：dispatch 时刻快照节点
代次（job_nodes.created_at，rerun 重建 shard 行的同一事务刷新它），失败
写事务内 re-guard——迟到于用户 rerun 的旧轮失败被丢弃而不是污染新一轮
的 pending shard。#520 四轮 P2 后代次经显式参数传递（不再注入 job 载
荷），本文件同时锁这条参数通道的传递链。共享脚手架在
``tests/workflows/helpers.py``。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server.app.executors.scheduling.capacity import CapacitySnapshot
from tests.workflows.helpers import (
    CUSTOM_V1,
    _job_status,
    _local_worker,
    _node_status,
    _publish_code,
    _seed_workspace_and_job,
    _shard_node,
    _unique_job_id,
    _unique_workspace,
)

pytestmark = pytest.mark.postgres


def _remote_lane_failure_harness(
    job_db, tmp_path: Path, ws_id: str, job_id: str, node
) -> MagicMock:
    """远程 lane 的 worker/dispatch 骨架 + shard 级失败写指到测试库。

    返回 worker mock（``worker.code_dispatch`` 是 dispatch mock）；调用方
    再决定 resolve 失败的时机（断言口径不变）。"""
    from server.app.db.transaction import write_transaction
    from server.app.executors._lease_shard_fail import fail_shard_without_lease

    dispatch = MagicMock()
    dispatch.is_in_flight.return_value = False
    dispatch.broker.has_active_request.return_value = False
    dispatch.online_code_worker_available.return_value = True
    worker = _local_worker(job_db, tmp_path)
    worker.code_dispatch = dispatch
    worker.settings.root_dir = tmp_path
    worker.settings.config = {}
    worker.settings.executor_runtime.workflows.custom_nodes_enabled = True

    def _record_shard_failure(
        _job_id: str, node_key: str, shard_index: int, message: str, **kwargs
    ) -> bool:
        with write_transaction(job_db.dsn_identity) as conn:
            return fail_shard_without_lease(conn, job_id, node_key, shard_index, message, **kwargs)

    worker.leases.fail_shard = _record_shard_failure
    return worker


def _lean_job(job_db, job_id: str) -> dict:
    with job_db.read() as conn:
        row = conn.execute(
            "select id, workspace_id, run_id from jobs where id=%s", (job_id,)
        ).fetchone()
    return {"id": row["id"], "workspace_id": row["workspace_id"], "run_id": row["run_id"]}


def _rerun_rebuild(job_db, job_id: str, node_key: str, shard_count: int) -> None:
    """rerun 重建（mark_nodes_for_rerun 的最小语义：刷新 created_at + 删并
    重建 shard 行——真实路径 delete_shards + materialize_shards 同事务）。"""
    with job_db.connect() as conn:
        conn.execute("delete from node_shards where job_id=%s and node_key=%s", (job_id, node_key))
        conn.execute(
            "update job_nodes set status='pending', created_at=current_timestamp"
            " where job_id=%s and node_key=%s",
            (job_id, node_key),
        )
        conn.executemany(
            "insert into node_shards(job_id, node_key, shard_index, status, input_json)"
            " values (%s, %s, %s, 'pending', '{}')",
            [(job_id, node_key, index) for index in range(shard_count)],
        )


def test_late_shard_failure_after_rerun_does_not_pollute_the_new_round(
    job_db, tmp_path, monkeypatch
) -> None:
    """#520 review P1 回归锁（迟到失败的身份校验）：远程 fan-out 的异步
    _enqueue 失败晚于用户 rerun 到达——rerun 删除并按相同 (job_id,
    node_key, shard_index) 重建了 pending shard。修复前迟到失败无条件
    on_shard_finished，把新一轮的 pending shard 错杀成 failed；修复后写事务
    内 re-guard 节点代次（job_nodes.created_at，rerun 重建 shard 行的同一
    事务刷新它），旧轮失败被丢弃，新轮 shard 保持 pending 等本轮 claim。"""
    from server.app.db.transaction import write_transaction
    from server.app.executors._lease_shard_fail import (
        fail_shard_without_lease,
        read_shard_dispatch_generation,
    )
    from tests.workflows.helpers import _shard_rows

    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node, shard_count=2)
    dispatch = _remote_lane_failure_harness(job_db, tmp_path, ws_id, job_id, node)
    job = _lean_job(job_db, job_id)

    # Round 1 dispatch：claim_shard_node 在 dispatch 时刻快照的节点代次。
    monkeypatch.setattr(
        "server.app.workflow_worker.shards.try_claim_code_worker_node", lambda *a, **k: False
    )
    from server.app.workflow_worker.shards import claim_shard_node

    claim_shard_node(
        _local_worker(job_db, tmp_path),
        {"id": ws_id},
        job,
        node,
        tmp_path / job_id,
        None,
        None,
        CapacitySnapshot(global_remaining=0),
    )
    with job_db.read() as conn:
        generation_r1 = read_shard_dispatch_generation(conn, job_id, node.key)
    assert generation_r1, "the node row must exist for the generation snapshot"

    _rerun_rebuild(job_db, job_id, node.key, 2)
    with job_db.read() as conn:
        generation_r2 = read_shard_dispatch_generation(conn, job_id, node.key)
    assert generation_r2 != generation_r1, "rerun must refresh the generation"

    # 旧轮（快照 r1）的失败迟到到达：直接走 fail_shard 的 re-guard 路径。
    with write_transaction(job_db.dsn_identity) as conn:
        dropped = fail_shard_without_lease(
            conn, job_id, node.key, 0, "late round-1 failure", dispatch_generation=generation_r1
        )
    assert dropped is False, "the stale-round failure must be discarded (P1 re-guard)"

    rows = _shard_rows(job_db, job_id, node.key)
    assert rows[0]["status"] == "pending", "the new round's shard stays pending"
    assert rows[0]["error_message"] == ""
    assert rows[1]["status"] == "pending"
    status, _error = _node_status(job_db, job_id, node.key)
    assert status == "pending", "the rerun round is not failed by the stale write"
    assert _job_status(job_db, job_id) != "failed"
    dispatch.enqueue.assert_not_called()

    # 对照：带新轮快照的失败正常落地（同 shard、同轮——re-guard 通过）。
    with write_transaction(job_db.dsn_identity) as conn:
        landed = fail_shard_without_lease(
            conn, job_id, node.key, 0, "round-2 failure", dispatch_generation=generation_r2
        )
    assert landed is True
    rows = _shard_rows(job_db, job_id, node.key)
    assert rows[0]["status"] == "failed"
    assert "round-2 failure" in str(rows[0]["error_message"])


def test_late_shard_failure_after_rerun_via_remote_lane_dispatch(
    job_db, tmp_path, monkeypatch
) -> None:
    """P1 的 lane 级序列：dispatch（快照 r1）→ rerun 重建 → 迟到失败经
    fail_claim_target_config 携旧快照进入——锁 job dict 载体的传递链，而不
    只是直调 _lease_shard_fail（快照由 claim_shard_node 写进 job dict，
    远程 lane 的 _fail 闭包捕获同一 job dict）。"""
    from server.app.workflow_worker.shard_failure import fail_claim_target_config
    from server.app.workflow_worker.shards import claim_shard_node
    from tests.workflows.helpers import _shard_rows

    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node, shard_count=1)
    _publish_code(job_db, ws_id, node.key, CUSTOM_V1)
    worker = _remote_lane_failure_harness(job_db, tmp_path, ws_id, job_id, node)
    job = _lean_job(job_db, job_id)

    # Round 1 dispatch：claim_shard_node 塞入快照后的 job dict。
    dispatched_job: dict = {}

    def _fake_remote_claim(_worker, _ws, lane_job, *_a, **_k) -> bool:
        dispatched_job.update(lane_job)
        return False  # 落回本地 lane，但本地 lane 无代码也能构造 context

    monkeypatch.setattr(
        "server.app.workflow_worker.shards.try_claim_code_worker_node", _fake_remote_claim
    )
    claim_shard_node(
        worker,
        {"id": ws_id},
        job,
        node,
        tmp_path / job_id,
        None,
        None,
        CapacitySnapshot(global_remaining=0),
    )
    assert dispatched_job.get("shard_dispatch_generation"), (
        "claim_shard_node must snapshot the generation into the job dict (P1)"
    )

    # rerun 重建 + 迟到失败经 fail_claim_target_config 携旧 job dict。
    _rerun_rebuild(job_db, job_id, node.key, 1)

    dropped = fail_claim_target_config(
        worker,
        ws_id,
        {**dispatched_job},  # 迟到的旧轮 job dict（携带 r1 快照）
        ws_id,
        node,
        tmp_path / "late.log",
        0,
        "late round-1 failure",
    )
    assert dropped is False, "the stale-round failure is discarded end to end"
    rows = _shard_rows(job_db, job_id, node.key)
    assert rows[0]["status"] == "pending", "the new round's shard is not polluted"
