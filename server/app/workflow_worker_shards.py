"""Shard fan-out scheduling and reduce fan-in assembly for the workflow worker.

When a ready node declares ``shard:``, the first scheduling pass materializes
its shard rows (one per fan-out input), then each pending shard is claimed
individually through the lease system (SCHED-SHARD-001) and submitted as an
independent execution. ``max_concurrency`` is a pure hint: it only bounds how
many shards of the node are dispatched per pass; authoritative capacity
enforcement stays inside ``leases.try_claim``.

Reduce fan-in: before a reduce node is claimed, the shard outputs of its
``from`` node are aggregated into ``<node_key>.shards.json`` in the job dir.
File writing belongs to the job execution service layer, never to routes.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.db.transaction import write_transaction
from server.app.executors.models import (
    ConfigurationFailureRequest,
    ExecutionContext,
    LeaseClaimRequest,
)
from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.jobs.queries.workspace_node_bindings import (
    get_binding,
    get_local_node_limit,
    has_local_node_limit,
)
from server.app.workflows.definition import WorkflowNode
from server.app.workflows.sharding import (
    ShardLimitExceeded,
    materialize_shards,
    read_shard_outputs,
)

if TYPE_CHECKING:
    from server.app.workflow_worker_thread import WorkflowWorkerThread


def claim_shard_node(
    worker: WorkflowWorkerThread,
    workspace: dict[str, Any],
    job: dict[str, Any],
    node: WorkflowNode,
    job_dir: Path,
    control_snapshot: dict[str, Any] | None,
    allowed_node_keys: frozenset[str] | None,
    snapshot: CapacitySnapshot,
) -> bool:
    """Materialize (once) and claim pending shards of a shard node."""
    shard = node.shard
    if shard is None:
        return False
    workspace_id = workspace["id"]
    workflow_key = str(job["workflow_key"])
    node_key = node.key
    log_path = worker.settings.logs_dir.resolve() / "jobs" / f"{job['id']}-{node_key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with worker.job_db._connect_read() as conn:
        binding = get_binding(conn, workspace_id, workflow_key, node_key)
        if binding is None:
            _fail_node(
                worker, workspace_id, job, workflow_key, node, log_path, "No Executor binding"
            )
            return True
        executor_id = binding["executor_id"]
        try:
            executor = worker.registry.require(executor_id, node.capability)
        except Exception as exc:
            _fail_node(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
            return True
        local_node_limit: int | None = None
        if executor.kind == "local":
            local_node_limit = get_local_node_limit(conn, workspace_id, workflow_key, node_key)
        elif has_local_node_limit(conn, workspace_id, workflow_key, node_key):
            _fail_node(
                worker,
                workspace_id,
                job,
                workflow_key,
                node,
                log_path,
                "Node limits are not supported for agent executors",
            )
            return True

    global_capacity = worker.registry.global_capacity(executor_id)
    if global_capacity is None:
        return False

    rows = _read_shard_rows(worker, job["id"], node_key)
    if not rows:
        try:
            inputs = _resolve_shard_inputs(node, job_dir)
        except ValueError as exc:
            _fail_node(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
            return True
        try:
            with write_transaction(worker.leases.path) as conn:
                materialize_shards(conn, job["id"], node_key, inputs, max_shards=shard.max_shards)
        except ShardLimitExceeded as exc:
            _fail_node(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
            return True
        rows = _read_shard_rows(worker, job["id"], node_key)

    running = sum(1 for row in rows if row["status"] == "running")
    claimed_any = False
    for row in rows:
        if row["status"] != "pending":
            continue
        if shard.max_concurrency is not None and running >= shard.max_concurrency:
            break
        if not snapshot.has_capacity(executor_id, workspace_id):
            break
        shard_index = int(row["shard_index"])
        shard_log_path = log_path.with_name(f"{job['id']}-{node_key}-shard-{shard_index}.log")
        claim = worker.leases.try_claim(
            LeaseClaimRequest(
                executor_id=executor_id,
                global_capacity=global_capacity,
                workspace_id=workspace_id,
                job_id=job["id"],
                workflow_key=workflow_key,
                node_key=node_key,
                capability=node.capability,
                local_node_limit=local_node_limit,
                lease_ttl_seconds=worker.runtime.lease_ttl_seconds,
                log_path=str(shard_log_path),
                execution_mode=control_snapshot.get("execution_mode", "full")
                if control_snapshot
                else "full",
                target_node_key=control_snapshot.get("target_node_key")
                if control_snapshot
                else None,
                allowed_node_keys=tuple(sorted(allowed_node_keys)) if allowed_node_keys else (),
                shard_index=shard_index,
            )
        )
        if claim is None:
            break  # capacity lost to a race; the next poll pass re-evaluates
        snapshot.record_claim(executor_id, workspace_id)
        running += 1
        claimed_any = True
        context = ExecutionContext(
            execution_id=claim.execution_id,
            lease_id=claim.lease_id,
            node_run_id=claim.node_run_id,
            executor_id=claim.executor_id,
            workspace_id=claim.workspace_id,
            job_id=claim.job_id,
            workflow_key=claim.workflow_key,
            node_key=claim.node_key,
            capability=claim.capability,
            workspace=dict(workspace),
            job=dict(job),
            job_dir=job_dir,
            log_path=shard_log_path,
            inputs=tuple(node.inputs),
            expected_outputs=tuple(node.outputs),
            runtime={
                "node_execution": asdict(node.execution),
                "shard_index": shard_index,
                "shard_input": json.loads(row["input_json"]),
            },
        )
        pool = worker._pool_for(executor_id)
        future = pool.submit(worker._run_claim, claim, context)
        worker._futures[claim.execution_id] = future
    return claimed_any


def assemble_reduce_inputs(
    worker: WorkflowWorkerThread, job_id: str, node: WorkflowNode, job_dir: Path
) -> None:
    """Write ``<node_key>.shards.json``: the from-node's shard outputs as one array."""
    reduce_spec = node.reduce
    if reduce_spec is None:
        return
    with worker.job_db._connect_read() as conn:
        outputs = read_shard_outputs(conn, job_id, reduce_spec.from_node)
    payload = [json.loads(item) if item else None for item in outputs]
    (job_dir / f"{node.key}.shards.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _read_shard_rows(worker: WorkflowWorkerThread, job_id: str, node_key: str) -> list[dict]:
    with worker.job_db._connect_read() as conn:
        rows = conn.execute(
            "select shard_index, status, input_json from node_shards"
            " where job_id=? and node_key=? order by shard_index",
            (job_id, node_key),
        ).fetchall()
    return [dict(row) for row in rows]


def _resolve_shard_inputs(node: WorkflowNode, job_dir: Path) -> list[dict]:
    shard = node.shard
    if shard is None:
        return []
    if shard.count is not None:
        return [{"index": i} for i in range(shard.count)]
    # shard.over == "inputs.<name>"; the loader already validated the shape.
    name = str(shard.over).split(".", 1)[1]
    try:
        data = json.loads((job_dir / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"shard over input {name!r} is not readable JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"shard over input {name!r} must be a JSON array")
    return data


def _fail_node(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    job: dict[str, Any],
    workflow_key: str,
    node: WorkflowNode,
    log_path: Path,
    message: str,
) -> None:
    worker.leases.fail_without_lease(
        ConfigurationFailureRequest(
            workspace_id=workspace_id,
            job_id=job["id"],
            workflow_key=workflow_key,
            node_key=node.key,
            capability=node.capability,
            log_path=str(log_path),
        ),
        message,
    )
