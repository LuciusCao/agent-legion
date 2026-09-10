"""Shard local-lane node code resolution (#495).

``claim_shard_locally`` used to build its ``ExecutionContext`` without
``node_code``, so every shard of a pure-local deployment (``code_capacity > 0``
and no remote code Worker taking the shard) died on the ``executors/code.py``
EXEC-CODE-002 backstop with a misleading "has no published node code" error —
while the same code resolved fine on the ordinary local path and the shard
remote lane. These tests pin the fixed contract: the local shard lane resolves
the published code through the same ``resolve_code_node_dispatch`` chain
(quality-replay pins honored, unrunnable code failing the node with the true
reason) and the resolved text rides the context.

The harness mirrors ``tests/helpers/sharding.py``'s e2e shape (v62 binding:
the workspace id IS the definition key) but stops one level lower — real
``claim_shard_node`` + real resolve, with the lease/executor seam patched at
``shard_dispatch`` so no lease rows or futures are needed. 共享脚手架在
``tests/workflows/helpers.py``（超 800 行按主题拆姊妹文件时抽出）；
姊妹文件：``test_shard_local_node_code_failure_aggregation.py``（shard 级
失败聚合/诊断可见性）、``test_shard_local_node_code_rerun_guard.py``
（rerun 代次保护）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from server.app.workflow_worker.shard_dispatch import claim_shard_locally
from server.app.workflows.definition import WorkflowNode
from tests.workflows.helpers import (
    CUSTOM_V1,
    CUSTOM_V2,
    _attach_replay_payload,
    _attach_snapshot_pins,
    _configured_shard_node,
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


def test_local_shard_dispatch_carries_published_node_code(job_db, tmp_path, monkeypatch) -> None:
    """#495 回归锁：纯本地分片（远程 lane 拒收 + 本地池有容量）的
    ExecutionContext 必须携带 resolve 出的 published 节点代码——修复前
    该字段恒为 None，全部 shard 死在 EXEC-CODE-002 backstop 上。"""
    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node)
    _publish_code(job_db, ws_id, "fan", CUSTOM_V1)
    _no_remote_lane(monkeypatch)
    worker = _local_worker(job_db, tmp_path)

    claimed, contexts, lease_requests = _run_local_pass(
        monkeypatch, job_db, worker, ws_id, job_id, node, tmp_path / job_id
    )

    assert claimed is True
    assert len(contexts) == 2, "both pending shards must claim on the local lane"
    for context in contexts:
        assert context.node_code == CUSTOM_V1, (
            "the local shard lane must carry the published node code (#495)"
        )


def test_local_shard_dispatch_job_payload_carries_no_dispatch_generation(
    job_db, tmp_path, monkeypatch
) -> None:
    """#520 四轮 P2 回归锁：调度代次（rerun 保护的身份快照）只走显式参数
    通道，绝不注入 job 载荷——本地 lane 的 ExecutionContext.job 原样暴露
    给 node SDK 的 ctx.job（``_code_runtime.build_runtime``），而远程 lane
    只见 ``runtime_context_stub`` 的白名单键；注入会让读/序列化 ctx.job 的
    节点拿到 lane 相关的输入。代次保护本身由 rerun_guard 姊妹文件锁定。"""
    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node)
    _publish_code(job_db, ws_id, "fan", CUSTOM_V1)
    _no_remote_lane(monkeypatch)
    worker = _local_worker(job_db, tmp_path)

    claimed, contexts, _lease_requests = _run_local_pass(
        monkeypatch, job_db, worker, ws_id, job_id, node, tmp_path / job_id
    )

    assert claimed is True
    assert len(contexts) == 2, "the local lane must still dispatch normally"
    for context in contexts:
        assert "shard_dispatch_generation" not in context.job, (
            "the local lane's ctx.job must stay lane-independent (round-4 P2)"
        )


def test_local_shard_dispatch_runs_latest_published_ignoring_pins(
    job_db, tmp_path, monkeypatch
) -> None:
    """与普通本地路径 (#115) 对齐：普通 job 忽略 intake/snapshot pin，
    本地 shard lane 也 resolve 当前 published 版本（v2），不吃冻结 v1。"""
    from server.app.services.node_codes import code_hash

    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node)
    _publish_code(job_db, ws_id, "fan", CUSTOM_V1)
    _publish_code(job_db, ws_id, "fan", CUSTOM_V2)
    # The job's snapshot carries an audit pin of v1 — an ordinary (non
    # quality-replay) shard must still run the latest published v2.
    pins = {"fan": {"version": 1, "code_hash": code_hash(CUSTOM_V1)}}
    _attach_snapshot_pins(job_db, job_id, node, ws_id, pins)
    _no_remote_lane(monkeypatch)
    worker = _local_worker(job_db, tmp_path)

    claimed, contexts, lease_requests = _run_local_pass(
        monkeypatch, job_db, worker, ws_id, job_id, node, tmp_path / job_id
    )

    assert claimed is True
    assert contexts, "the shard must reach the local executor"
    for context in contexts:
        assert context.node_code == CUSTOM_V2


def test_local_shard_dispatch_without_published_code_fails_with_true_reason(
    job_db, tmp_path, monkeypatch
) -> None:
    """无 published 代码时走 fail_node_config：错误消息来自 resolve
    （capability + EXEC-CODE-002 上下文），节点带真实原因失败，且不再
    提交任何本地执行——backstop 保持兜底而非必经之路。"""
    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node)
    _no_remote_lane(monkeypatch)
    worker = _local_worker(job_db, tmp_path)

    claimed, contexts, lease_requests = _run_local_pass(
        monkeypatch, job_db, worker, ws_id, job_id, node, tmp_path / job_id
    )

    assert claimed is True, "the config failure still counts as pass work"
    assert contexts == [], "no execution may be submitted without code"
    status, error = _node_status(job_db, job_id, "fan")
    assert status == "failed"
    assert "no published node code" in error


def test_local_shard_dispatch_fails_closed_on_frozen_pin_hash_mismatch(
    job_db, tmp_path, monkeypatch
) -> None:
    """quality-replay 冻结 pin 的 hash 漂移在 shard 本地 lane 同样
    fail closed（EXEC-CODE-003）：报真实根因，绝不静默换成 published 版。"""
    from server.app.services.node_codes import code_hash

    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node)
    # Published v1 exists; the replay pin claims a v1 hash of OTHER code.
    _publish_code(job_db, ws_id, "fan", CUSTOM_V1)
    bad_pin = {"fan": {"version": 1, "code_hash": code_hash(CUSTOM_V2)}}
    _attach_replay_payload(
        job_db,
        ws_id,
        job_id,
        {"node_code_versions": bad_pin, "quality_replay": {"replay_id": "r1"}},
    )
    snapshot_pins = _attach_snapshot_pins(job_db, job_id, node, ws_id, bad_pin)
    assert snapshot_pins.get("fan") == bad_pin["fan"], "the harness pin must ride the job"
    _no_remote_lane(monkeypatch)
    worker = _local_worker(job_db, tmp_path)

    claimed, contexts, lease_requests = _run_local_pass(
        monkeypatch, job_db, worker, ws_id, job_id, node, tmp_path / job_id, snapshot_pins
    )

    assert claimed is True
    assert contexts == []
    status, error = _node_status(job_db, job_id, "fan")
    assert status == "failed"
    assert "frozen node code hash mismatch" in error


def test_local_shard_dispatch_carries_resolved_node_config(job_db, tmp_path, monkeypatch) -> None:
    """PR #520 review P2 回归锁：声明 config_schema/config 的 shard 节点
    走本地 lane 时，ExecutionContext.node_config 必须是解析链产物
    （defaults → 节点 config），且 LeaseClaimRequest 携带非空
    config_snapshot_json——修复前两者分别为空字典与空串，config 静默
    丢失而远程 lane 拿到完整配置。"""
    import json as _json

    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node: WorkflowNode = _configured_shard_node()
    _seed_workspace_and_job(job_db, ws_id, job_id, node)
    _publish_code(job_db, ws_id, "fan", CUSTOM_V1)
    _no_remote_lane(monkeypatch)
    worker = _local_worker(job_db, tmp_path)

    claimed, contexts, lease_requests = _run_local_pass(
        monkeypatch, job_db, worker, ws_id, job_id, node, tmp_path / job_id
    )

    assert claimed is True
    assert len(contexts) == 2
    for context in contexts:
        # Node config wins over the schema default; the undeclared key
        # falls back to its schema default — both resolution layers
        # observable in one assertion pass.
        assert context.node_config["mode"] == "fast"
        assert context.node_config["batch_size"] == 25
        assert context.node_config["retries"] == 1
    assert len(lease_requests) == 2
    for request in lease_requests:
        assert request.config_snapshot_json
        snapshot = _json.loads(request.config_snapshot_json)
        assert snapshot["mode"] == "fast", "the audit snapshot rides the lease"


def test_claim_shard_locally_skips_resolution_without_capacity(job_db, tmp_path) -> None:
    """容量已满时先返回 False：不做无谓的代码 resolve（远端 lane 在
    前一行已经拒收，下一个 pass 重新评估）。"""
    ws_id = _unique_workspace()
    job_id = _unique_job_id()
    node = _shard_node()
    worker = _local_worker(job_db, tmp_path)
    snapshot = MagicMock()
    snapshot.has_capacity.return_value = False

    def _boom(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("resolve must not run without local capacity")

    with patch("server.app.workflow_worker.shard_dispatch.resolve_code_node_dispatch", _boom):
        claimed = claim_shard_locally(
            worker,
            {"id": ws_id},
            {"id": job_id, "workspace_id": ws_id},
            node,
            tmp_path / job_id,
            tmp_path / "shard.log",
            shard_index=0,
            shard_input={"q": 0},
            local_node_limit=None,
            control_snapshot=None,
            allowed_node_keys=None,
            snapshot=snapshot,
        )

    assert claimed is False
