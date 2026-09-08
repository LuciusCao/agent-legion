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

from server.app.db.transaction import write_transaction
from server.app.executors._lease_shard_fail import fail_shard_without_lease
from server.app.workflow_worker.agent_claim import fail_node_config
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


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
    """
    if shard_index is None:
        # Ordinary node: the node IS the execution unit — fail it whole.
        return fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, message)
    with write_transaction(worker.leases.path) as conn:
        fail_shard_without_lease(conn, str(job["id"]), node.key, shard_index, message)
    return True
