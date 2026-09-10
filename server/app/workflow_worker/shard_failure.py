"""Claim-target-granularity configuration failures (#520 review P2).

``agent_claim.fail_node_config`` fails the whole ``job_nodes`` row through
the pending/ready/stale status guard — the right granularity for ordinary
nodes (the node IS the execution unit). A shard node's execution unit is
the shard row: once an earlier fan-out round flipped the node to running
(capacity or ``max_concurrency`` split the fan-out), the node-level write
no-ops and the failure would record nothing anywhere. This module hosts
the dispatch both shard lanes share: a shard's resolve failure goes
shard-granular through the aggregate (``executors._lease_shard_fail``),
an ordinary node keeps failing whole.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.workflow_worker.agent_claim import fail_node_config
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

# ``claim_shard_node`` 在 dispatch 时刻把节点代次（job_nodes.created_at）
# 快照进 job dict 的这个键，两条 lane 的失败出口由此取回——key 只在本模
# 块与 shards.py 之间流转，不进 broker manifest（runtime_context_stub 只
# 白名单读取 job 的固定键），缺键（未来调用方未快照）回退为事务内现读，
# 失败安全。#520 review P1。
# #520 四轮 P2：本地 lane 构造 ExecutionContext 前必须剥离该键（见
# shard_dispatch.claim_shard_locally）——context.job 会被 build_runtime
# 原样暴露给 node SDK 的 ctx.job，内部调度信号不得混入节点可见的 job
# 载荷（远程 lane 天然不见此键，剥离后两条 lane 的 ctx.job 键集合对齐）。
DISPATCH_GENERATION_JOB_KEY = "shard_dispatch_generation"


def fail_claim_target_config(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    job: dict[str, Any],
    workflow_key: str,
    node: WorkflowNode,
    log_path: Path,
    shard_index: int | None,
    message: str,
) -> bool:
    """按 claim 目标的粒度终结 resolve 失败：普通节点 fail 整节点，shard 只终结该 shard。

    shard 级：shard 行记终态，聚合决定节点（any-failed 优先，与 shard 执
    行失败同构）；非 terminal 聚合保持节点原状，由剩余 shard 的 finisher
    决定终态。两条 shard lane（本地 shard_dispatch、远程 code_claim）共用
    此分派；普通节点（shard_index None）路径完全不变。

    PR #520 review P1/P2-2：dispatch 时刻的节点代次快照随 job dict 传入
    （见 ``DISPATCH_GENERATION_JOB_KEY``）——写事务内 re-guard，迟到于
    rerun 的旧轮失败被丢弃而不是污染新一轮的 pending shard；写路径走
    ``ExecutorLeaseRepository.fail_shard``，commit 后的 job 广播与
    ``fail_without_lease`` 同源。
    """
    if shard_index is None:
        # Ordinary node: the node IS the execution unit — fail it whole.
        return fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, message)
    return worker.leases.fail_shard(
        str(job["id"]),
        node.key,
        shard_index,
        message,
        dispatch_generation=str(job.get(DISPATCH_GENERATION_JOB_KEY, "")),
        log_path=str(log_path),
    )
