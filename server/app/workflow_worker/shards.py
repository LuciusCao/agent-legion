"""Experimental shard fan-out and reduce fan-in support for the workflow worker.

This module is not a production workflow authoring surface yet.  Workflow
Studio cannot currently round-trip shard/reduce declarations, and local
concurrent shards still share their job output directory.  Production
workflows should use ordinary DAG branches until both limitations are closed.

When a ready node declares ``shard:``, the first scheduling pass materializes
its shard rows (one per fan-out input), then each pending shard is claimed
individually through the lease system (EXEC-SHARD-001) and submitted as an
independent execution. ``max_concurrency`` is a pure hint: it only bounds how
many shards of the node are dispatched per pass; authoritative capacity
enforcement stays inside ``leases.try_claim``.

Shard execution follows the same dual path as ordinary code nodes (#389):
each shard first tries the remote code Worker (``try_claim_code_worker_node``
on a shard-shaped node view); the local code pool stays the fallback when it
has capacity. In pure-remote mode (``code_capacity == 0``) only the remote
path exists.

Reduce fan-in: before a reduce node is claimed, the shard outputs of its
``from`` node are aggregated into ``<node_key>.shards.json`` in the job dir.
File writing belongs to the job execution service layer, never to routes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.db.transaction import write_transaction
from server.app.executors._lease_shards import complete_empty_shard_node
from server.app.executors.models import ConfigurationFailureRequest
from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.jobs.queries.workspace_node_limits import get_local_node_limit
from server.app.workflow_worker.code_claim import try_claim_code_worker_node
from server.app.workflow_worker.shard_dispatch import claim_shard_locally
from server.app.workflows.definition import WorkflowNode
from server.app.workflows.sharding import (
    ShardLimitExceeded,
    materialize_shards,
    read_shard_outputs,
)

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


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
    workflow_key = str(job["workspace_id"])
    node_key = node.key
    log_path = worker.settings.logs_dir.resolve() / "jobs" / f"{job['id']}-{node_key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Shard nodes join the implicit code pool like any other code node
    # (P-0.5): no binding/allocation lookup remains.
    with worker.job_db._connect_read() as conn:
        local_node_limit = get_local_node_limit(conn, workspace_id, workflow_key, node_key)

    rows = _read_shard_rows(worker, job["id"], node_key)
    if not rows:
        try:
            inputs = _resolve_shard_inputs(node, job_dir)
        except ValueError as exc:
            _fail_node(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
            return True
        try:
            with write_transaction(worker.leases.path) as conn:
                total = materialize_shards(
                    conn, job["id"], node_key, inputs, max_shards=shard.max_shards
                )
                if total == 0:
                    # Empty fan-out: zero shards aggregate to a completed node
                    # with empty outputs; the reduce fan-in reads an empty array.
                    complete_empty_shard_node(conn, job["id"], node_key)
        except ShardLimitExceeded as exc:
            _fail_node(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
            return True
        rows = _read_shard_rows(worker, job["id"], node_key)
        if not rows:
            return True  # empty fan-out; the node was resolved above

    running = sum(1 for row in rows if row["status"] == "running")
    claimed_any = False
    for row in rows:
        if row["status"] != "pending":
            continue
        if shard.max_concurrency is not None and running >= shard.max_concurrency:
            break
        shard_index = int(row["shard_index"])
        shard_log_path = log_path.with_name(f"{job['id']}-{node_key}-shard-{shard_index}.log")
        shard_input = json.loads(row["input_json"])

        # Remote path first (#389): mirror the ordinary code-node routing —
        # True = handled remotely (or failed as a config error); False falls
        # through to the local pool via shard_dispatch.
        if try_claim_code_worker_node(
            worker,
            workspace,
            job,
            node,
            job_dir,
            shard_log_path,
            tuple(node.inputs),
            workflow_key,
            shard_runtime={"shard_index": shard_index, "shard_input": shard_input},
        ):
            running += 1
            claimed_any = True
            continue
        if worker.settings.executor_runtime.code_capacity <= 0:
            # Pure-remote mode: no local fallback exists; leave the shard
            # pending for the next pass (remote worker offline / ineligible).
            continue
        if claim_shard_locally(
            worker,
            workspace,
            job,
            node,
            job_dir,
            shard_log_path,
            shard_index=shard_index,
            shard_input=shard_input,
            local_node_limit=local_node_limit,
            control_snapshot=control_snapshot,
            allowed_node_keys=allowed_node_keys,
            snapshot=snapshot,
        ):
            running += 1
            claimed_any = True
        else:
            break  # local capacity exhausted; the next poll pass re-evaluates
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
            " where job_id=%s and node_key=%s order by shard_index",
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
