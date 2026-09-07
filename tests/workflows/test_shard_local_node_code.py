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
``shard_dispatch`` so no lease rows or futures are needed.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.workflow_worker.shard_dispatch import claim_shard_locally
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)
from server.app.workflows.schema import WorkflowShardSpec

pytestmark = pytest.mark.postgres

# Keep these distinct so a leaked None (the #495 shape) cannot pass the
# node_code assertion by accident.
CUSTOM_V1 = "def run(job, job_dir, runtime):\n    return 'v1'\n"
CUSTOM_V2 = "def run(job, job_dir, runtime):\n    return 'v2'\n"


def _shard_node(key: str = "fan") -> WorkflowNode:
    return WorkflowNode(
        key=key,
        label=key,
        capability=key,
        outputs=["out.json"],
        shard=WorkflowShardSpec(count=4),
    )


def _configured_shard_node(key: str = "fan") -> WorkflowNode:
    """A shard node declaring ``config_schema``/``config`` (the P2 shape: a
    pre-fix local lane silently dropped both). One schema default, one
    node-config value and one workspace-override slot, so the resolution
    chain is observable per layer."""
    return WorkflowNode(
        key=key,
        label=key,
        capability=key,
        outputs=["out.json"],
        shard=WorkflowShardSpec(count=4),
        config_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "default": "standard"},
                "batch_size": {"type": "integer", "default": 10},
                "retries": {"type": "integer", "default": 1},
            },
        },
        config={"mode": "fast", "batch_size": 25},
    )


def _definition(node: WorkflowNode, key: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        key=key, label="Test", intake=WorkflowIntake(), nodes={node.key: node}
    )


def _publish_code(job_db, workspace_id: str, node_key: str, code: str) -> None:
    """Publish one version of ``code`` under the v62 binding (the workspace id
    IS the workflow key, so shard_dispatch's resolve finds it)."""
    from server.app.services.node_codes import NodeCodeService

    codes = NodeCodeService(job_db)
    codes.save_draft(workspace_id, workspace_id, node_key, code, "user:u1")
    codes.publish(workspace_id, workspace_id, node_key)


def _seed_workspace_and_job(
    job_db, workspace_id: str, job_id: str, node: WorkflowNode, shard_count: int = 2
) -> None:
    """Create the workspace/job/node/shard rows claim_shard_node's loop reads."""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'ws', 'demo_workflow') on conflict do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, title,"
            " status, storage_dir) values (%s, %s, 'question', %s, 't',"
            " 'running', 'd') on conflict do nothing",
            (job_id, workspace_id, job_id),
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status)"
            # 'pending' keeps fail_without_lease's status guard
            # (pending/ready/stale) effective — a 'running' row would be
            # ignored and the config failure would never land.
            " values (%s, %s, 'pending') on conflict do nothing",
            (job_id, node.key),
        )
        conn.executemany(
            "insert into node_shards(job_id, node_key, shard_index, status, input_json)"
            " values (%s, %s, %s, 'pending', '{}')",
            [(job_id, node.key, index) for index in range(shard_count)],
        )


def _no_remote_lane(monkeypatch) -> None:
    """A pure-local deployment: the remote lane declines every shard (no
    online code Worker / Worker-ineligible payload), exactly the #495 shape —
    pre-fix, every shard then fell through to the broken local lane."""

    def fake_remote_claim(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(
        "server.app.workflow_worker.shards.try_claim_code_worker_node", fake_remote_claim
    )


def _fake_claim() -> MagicMock:
    claim = MagicMock()
    claim.execution_id = "exec-local"
    claim.lease_id = "lease-local"
    claim.node_run_id = 1
    claim.executor_id = "code"
    claim.workspace_id = "ws-shard"
    claim.job_id = "job-shard"
    claim.workflow_key = "ws-shard"
    claim.node_key = "fan"
    return claim


def _local_worker(job_db, tmp_path: Path) -> MagicMock:
    """A worker-shaped MagicMock for the local shard lane (mirror of the
    pass-budget harness): real job_db and a REAL ExecutorRuntimeConfig (a
    MagicMock attribute would never compare ``<= 0`` truthfully, silently
    skipping the local lane), code_capacity > 0, per-pass memos.
    code_stock.pass_budget() → None disables the remote-lane budget (these
    tests only exercise the local lane)."""
    from server.app.configuration.executor_runtime import ExecutorRuntimeConfig

    worker = MagicMock()
    worker.job_db = job_db
    worker.settings.logs_dir = tmp_path
    worker.settings.executor_runtime = ExecutorRuntimeConfig.model_validate(
        {"code_capacity": 2, "lease_ttl_seconds": 5}
    )
    worker.state.batch_payload_cache = {}
    worker.state.node_code_cache = {}
    worker.state.pass_claim_counts = {}
    worker.code_stock.pass_budget.return_value = None
    return worker


def _run_local_pass(
    monkeypatch,
    job_db,
    worker,
    workspace_id: str,
    job_id: str,
    node: WorkflowNode,
    job_dir: Path,
    snapshot_pins: dict | None = None,
    workspace: dict | None = None,
) -> tuple[bool, list[Any], list[Any]]:
    """Run claim_shard_node once; return (claimed_any, submitted contexts,
    lease requests captured by the patched leases.try_claim).

    try_claim/submit_claim are patched at the shard_dispatch module so the
    local lane runs its real resolve → context assembly while no lease rows
    or executor futures are created (the harness job is minimal).
    """
    from server.app.workflow_worker.shards import claim_shard_node

    contexts: list[Any] = []
    lease_requests: list[Any] = []
    monkeypatch.setattr(
        "server.app.workflow_worker.shard_dispatch.submit_claim",
        lambda _worker, _executor_id, _claim, context: contexts.append(context),
    )

    def _capture_claim(request):
        lease_requests.append(request)
        return _fake_claim()

    worker.leases.try_claim = _capture_claim

    # fail_without_lease on the real repo runs a lease-repo write transaction;
    # the mock worker carries no repo, so record the config failure on the
    # jobs DB directly with the same request + message the production path
    # passes (the assertion then reads the node row it produces).
    def _record_config_failure(request, error_message: str) -> None:
        from server.app.executors._lease_config_failure import (
            fail_without_lease as record,
        )

        with job_db.connect() as conn:
            record(conn, request, error_message, None)

    worker.leases.fail_without_lease = _record_config_failure
    # The lean job ready evaluation hands the claim path: snapshot text
    # dropped, its node_code_pins kept (ready_cache), run_id live. Rebuild
    # that shape from the DB row so the replay pin rides the claim.
    with job_db.read() as conn:
        row = conn.execute(
            "select id, workspace_id, run_id from jobs where id=%s", (job_id,)
        ).fetchone()
    lean_job = {"id": row["id"], "workspace_id": row["workspace_id"], "run_id": row["run_id"]}
    if snapshot_pins:
        lean_job["node_code_pins"] = snapshot_pins
    claimed = claim_shard_node(
        worker,
        workspace if workspace is not None else {"id": workspace_id},
        lean_job,
        node,
        job_dir,
        None,
        None,
        # code_capacity=2 mirrors the worker; the claim transaction (patched
        # above) is the authoritative enforcement this harness bypasses.
        CapacitySnapshot(global_remaining=2),
    )
    return claimed, contexts, lease_requests


def _node_status(job_db, job_id: str, node_key: str) -> tuple[str, str]:
    with job_db.connect() as conn:
        row = conn.execute(
            "select status, error_message from job_nodes where job_id=%s and node_key=%s",
            (job_id, node_key),
        ).fetchone()
    assert row is not None, "the harness must seed the node row"
    return str(row["status"]), str(row["error_message"])


def _attach_snapshot_pins(
    job_db, job_id: str, node: WorkflowNode, workspace_id: str, pins: dict
) -> dict:
    """Give the job a workflow snapshot carrying node_code_pins, and return
    the lean-job pin dict ready evaluation builds (snapshot text dropped,
    pins kept) — what claim_shard_locally actually receives."""
    from server.app.services.node_code_pins import node_code_pins_from_job_snapshot
    from server.app.services.workflow_revision_format import (
        definition_hash,
        serialize_definition,
    )

    pure = serialize_definition(_definition(node, workspace_id))
    payload = json.loads(pure)
    payload["node_code_pins"] = pins
    snapshot = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set workflow_definition_snapshot_json=%s,"
            " workflow_definition_hash=%s where id=%s",
            (snapshot, definition_hash(pure), job_id),
        )
    return node_code_pins_from_job_snapshot({"workflow_definition_snapshot_json": snapshot})


def _attach_replay_payload(job_db, workspace_id: str, job_id: str, batch_payload: dict) -> None:
    """Freeze a quality-replay run (RUN-FREEZE-001 pins) and bind the job to
    it, so cached_run_payload picks the replay marker + pins up."""
    pins = {
        key: batch_payload[key]
        for key in ("node_code_versions", "quality_replay")
        if key in batch_payload
    }
    run = job_db.create_run(
        workspace_id, "batch_by_ids", batch_payload, workspace_id, frozen_pins=pins
    )
    with job_db.connect() as conn:
        conn.execute("update jobs set run_id=%s where id=%s", (str(run["id"]), job_id))


def _unique_workspace() -> str:
    """v62 shape: the workspace id IS the definition key; unique per test so
    parallel runs against the shared database never collide."""
    return f"ws_shard_{uuid.uuid4().hex[:8]}"


def _unique_job_id() -> str:
    """Job ids are globally unique; a shared constant would let two parallel
    tests fight over the same jobs row."""
    return f"job_shard_{uuid.uuid4().hex[:8]}"


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
    node = _configured_shard_node()
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
