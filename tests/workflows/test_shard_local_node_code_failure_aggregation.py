"""Shard dispatch failure aggregation and diagnostic visibility (#520 P2/P2-1).

从 ``test_shard_local_node_code.py`` 按主题拆出的姊妹文件（AGENTS.md §4：
超 800 行按被测主题拆姊妹文件，用例零改动迁移）：多轮 fan-out 中途的
resolve 失败按 shard 粒度终结（shard 行终态 → any-failed 聚合推进节点/
job），失败对诊断面可见（synthetic node_run）——与执行失败的聚合语义
同构，而非节点级写的 status-guard no-op wedge。共享脚手架在
``tests/workflows/helpers.py``。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.workflows.helpers import (
    CUSTOM_V1,
    CUSTOM_V2,
    _attach_replay_payload,
    _attach_snapshot_pins,
    _job_status,
    _local_worker,
    _no_remote_lane,
    _node_status,
    _publish_code,
    _run_local_pass,
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
    再决定 resolve 失败的时机（本文件远程 lane 既有用例的内联骨架抽出来
    供 P1 用例复用，断言口径不变）。"""
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


def test_mid_fanout_resolve_failure_terminates_the_pending_shard_not_the_node(
    job_db, tmp_path, monkeypatch
) -> None:
    """#520 review P2 回归锁（本地 lane）：多轮 fan-out 中途归档代码——
    节点已 running、一个 shard 已 running、剩余 shard 重新入队时 resolve
    失败。修复前 fail_node_config 对 running 节点是 no-op：失败无处落地，
    shard 永远 pending、job 永远 running。修复后：这个 shard 的行带真实
    原因置 failed，any-failed 聚合把节点推进 failed，job 聚合 failed；
    已 running 的兄弟 shard 行不被这个失败触碰（由其自身 finisher 收敛）。"""
    from tests.workflows.helpers import _shard_rows

    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node, shard_count=3)
    # 模拟第一轮 fan-out 的产物：节点 running + shard 0 已被 claim 置 running。
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key=%s",
            (job_id, node.key),
        )
        conn.execute(
            "update node_shards set status='running', execution_id='exec-first'"
            " where job_id=%s and node_key=%s and shard_index=0",
            (job_id, node.key),
        )
    # 代码从未发布（或两轮之间被归档）：下一个 shard 的 resolve 失败。
    _no_remote_lane(monkeypatch)
    worker = _local_worker(job_db, tmp_path)

    claimed, contexts, _requests = _run_local_pass(
        monkeypatch, job_db, worker, ws_id, job_id, node, tmp_path / job_id
    )

    assert claimed is True, "the shard-level failure still counts as pass work"
    assert contexts == [], "no execution may be submitted without code"
    rows = _shard_rows(job_db, job_id, node.key)
    assert rows[0]["status"] == "running", "the in-flight sibling stays untouched"
    for index in (1, 2):
        assert rows[index]["status"] == "failed"
        assert "no published node code" in str(rows[index]["error_message"])
    status, error = _node_status(job_db, job_id, node.key)
    assert status == "failed", "any-failed aggregate advances the node"
    assert "no published node code" in error
    assert _job_status(job_db, job_id) == "failed", "the job aggregate follows"


def test_mid_fanout_failure_with_completed_siblings_keeps_node_running(
    job_db, tmp_path, monkeypatch
) -> None:
    """聚合语义另一半：失败的 shard 之外的兄弟全部 completed 时，any-failed
    优先级仍然把节点置 failed（与 shard 执行失败的聚合完全同构——这里锁
    的是 dispatch 失败与执行失败共享同一条聚合规则，不是新语义）。"""
    from tests.workflows.helpers import _shard_rows

    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node, shard_count=2)
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key=%s",
            (job_id, node.key),
        )
        conn.execute(
            "update node_shards set status='completed', output_json='{}'"
            " where job_id=%s and node_key=%s and shard_index=0",
            (job_id, node.key),
        )
    _no_remote_lane(monkeypatch)
    worker = _local_worker(job_db, tmp_path)

    claimed, contexts, _requests = _run_local_pass(
        monkeypatch, job_db, worker, ws_id, job_id, node, tmp_path / job_id
    )

    assert claimed is True and contexts == []
    rows = _shard_rows(job_db, job_id, node.key)
    assert rows[0]["status"] == "completed"
    assert rows[1]["status"] == "failed"
    status, _error = _node_status(job_db, job_id, node.key)
    assert status == "failed", "any failed shard fails the node aggregate"


def test_remote_lane_shard_resolve_failure_terminates_the_shard(
    job_db, tmp_path, monkeypatch
) -> None:
    """#520 review P2 回归锁（远程 lane）：shard 形状的远程 claim 遇到
    resolve 失败（frozen pin 漂移）时终结该 shard 而非整个节点——修复前
    远程 lane 与本地 lane 一样走节点级 fail_node_config，同样的 running
    no-op wedge。普通（非分片）远程 claim 的失败路径保持节点级不变。"""
    from server.app.services.node_codes import code_hash
    from server.app.workflow_worker.code_claim import try_claim_code_worker_node
    from tests.workflows.helpers import _shard_rows

    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node, shard_count=2)
    _publish_code(job_db, ws_id, node.key, CUSTOM_V1)
    # 节点 running + shard 0 已被远程 claim 置 running（claim_evaluate 的
    # try_start_shard 产物）。
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key=%s",
            (job_id, node.key),
        )
        conn.execute(
            "update node_shards set status='running', execution_id='exec-remote-0'"
            " where job_id=%s and node_key=%s and shard_index=0",
            (job_id, node.key),
        )
    # 质量回放 pin 冻结 v1 却给出别的 hash：resolve fail closed（EXEC-CODE-003）。
    bad_pin = {node.key: {"version": 1, "code_hash": code_hash(CUSTOM_V2)}}
    _attach_replay_payload(
        job_db,
        ws_id,
        job_id,
        {"node_code_versions": bad_pin, "quality_replay": {"replay_id": "r1"}},
    )
    snapshot_pins = _attach_snapshot_pins(job_db, job_id, node, ws_id, bad_pin)
    with job_db.read() as conn:
        row = conn.execute(
            "select id, workspace_id, run_id from jobs where id=%s", (job_id,)
        ).fetchone()
    lean_job = {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "run_id": row["run_id"],
        "node_code_pins": snapshot_pins,
    }

    dispatch = MagicMock()
    dispatch.is_in_flight.return_value = False
    dispatch.broker.has_active_request.return_value = False
    dispatch.online_code_worker_available.return_value = True
    worker = _local_worker(job_db, tmp_path)
    worker.code_dispatch = dispatch
    worker.settings.root_dir = tmp_path
    worker.settings.config = {}
    worker.settings.executor_runtime.workflows.custom_nodes_enabled = True
    # 同 _run_local_pass 的 harness 补丁：把 shard 级失败写指到测试库。
    from server.app.db.transaction import write_transaction
    from server.app.executors._lease_shard_fail import fail_shard_without_lease

    def _record_shard_failure(
        _job_id: str, node_key: str, shard_index: int, message: str, **kwargs
    ) -> bool:
        with write_transaction(job_db.dsn_identity) as conn:
            return fail_shard_without_lease(conn, job_id, node_key, shard_index, message, **kwargs)

    worker.leases.fail_shard = _record_shard_failure

    handled = try_claim_code_worker_node(
        worker,
        {"id": ws_id},
        lean_job,
        node,
        tmp_path / job_id,
        tmp_path / "claim.log",
        tuple(node.inputs),
        ws_id,
        shard_runtime={"shard_index": 1, "shard_input": {"q": 1}},
    )

    assert handled is True, "the shard-level failure counts as handled"
    dispatch.enqueue.assert_not_called()
    rows = _shard_rows(job_db, job_id, node.key)
    assert rows[0]["status"] == "running", "the in-flight sibling stays untouched"
    assert rows[1]["status"] == "failed"
    assert "frozen node code hash mismatch" in str(rows[1]["error_message"])
    status, error = _node_status(job_db, job_id, node.key)
    assert status == "failed"
    assert "frozen node code hash mismatch" in error
    assert _job_status(job_db, job_id) == "failed"


def test_mid_fanout_failure_clears_stale_reason_on_stale_node(
    job_db, tmp_path, monkeypatch
) -> None:
    """#520 四轮 P2 回归锁（stale→failed 清 stale_reason）：上游 rerun 把
    节点置 stale（stale_reason='upstream rerun'）后，本轮 dispatch 失败把它
    翻成 failed——遗留的 stale_reason 与终态矛盾（stale 是未终态、failed
    是终态），必须同一条 update 清空，与 record_failed_node_without_execution
    的写法对齐。"""
    from tests.workflows.helpers import _shard_rows

    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node, shard_count=2)
    # 上游 rerun 的产物：节点 stale + stale_reason（mark_node_for_rerun 的
    # downstream 语义），shard 行保持 pending（rerun 重建后的新一轮）。
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='stale', stale_reason='upstream rerun'"
            " where job_id=%s and node_key=%s",
            (job_id, node.key),
        )
    _no_remote_lane(monkeypatch)
    worker = _local_worker(job_db, tmp_path)

    claimed, contexts, _requests = _run_local_pass(
        monkeypatch, job_db, worker, ws_id, job_id, node, tmp_path / job_id
    )

    assert claimed is True and contexts == []
    rows = _shard_rows(job_db, job_id, node.key)
    assert all(row["status"] == "failed" for row in rows.values())
    with job_db.connect() as conn:
        row = conn.execute(
            "select status, stale_reason from job_nodes where job_id=%s and node_key=%s",
            (job_id, node.key),
        ).fetchone()
    assert row["status"] == "failed", "the any-failed aggregate advances the node"
    assert row["stale_reason"] == "", "the stale→failed flip must clear stale_reason (round-4 P2)"


def test_shard_dispatch_failure_is_visible_in_list_failed_node_runs(
    job_db, tmp_path, monkeypatch
) -> None:
    """#520 review P2-1 回归锁：shard dispatch 失败必须落到 node_runs（带
    failure_category/failure_detail），list_failed_node_runs（按类别诊断 /
    批量 rerun 只查 node_runs）能看到——修复前该路径只写 node_shards/
    job_nodes/jobs，失败对诊断面不可见。"""
    from tests.workflows.helpers import _shard_rows

    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node, shard_count=2)
    _no_remote_lane(monkeypatch)
    worker = _local_worker(job_db, tmp_path)

    claimed, contexts, _requests = _run_local_pass(
        monkeypatch, job_db, worker, ws_id, job_id, node, tmp_path / job_id
    )

    assert claimed is True and contexts == []
    rows = _shard_rows(job_db, job_id, node.key)
    assert all(row["status"] == "failed" for row in rows.values())
    failed_runs = job_db.list_failed_node_runs(ws_id, job_ids=[job_id])
    assert failed_runs, "the dispatch failure must be queryable (P2-1)"
    assert all(run["job_id"] == job_id and run["node_key"] == node.key for run in failed_runs)
    latest = failed_runs[0]
    assert latest["failure_category"], "the synthetic node_run carries a category"
    assert latest["failure_detail"]
    assert "no published node code" in str(latest["error_message"])
    with job_db.connect() as conn:
        node_row = conn.execute(
            "select failure_category, failure_detail from job_nodes"
            " where job_id=%s and node_key=%s",
            (job_id, node.key),
        ).fetchone()
    assert node_row["failure_category"] == latest["failure_category"], (
        "the aggregate node row classifies identically to the synthetic run"
    )
