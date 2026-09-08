"""Local-pool shard claim execution (#389 split from ``shards.py``).

The shard claim loop lives in ``shards.py``; the local fallback half moved
here when the remote lane (#389) made that file outgrow its size budget.
Pure-remote mode (``code_capacity == 0``) never reaches this module — the
caller gates on the same config.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.models import (
    CODE_EXECUTOR_ID,
    ExecutionContext,
    LeaseClaimRequest,
)
from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.services.job_errors import JobServiceError
from server.app.services.vault import VaultError
from server.app.workflow_worker.agent_claim import cached_run_payload
from server.app.workflow_worker.code_dispatch import resolve_code_node_dispatch
from server.app.workflow_worker.dispatch_config import resolve_dispatch_node_config
from server.app.workflow_worker.execution import submit_claim
from server.app.workflow_worker.shard_failure import fail_claim_target_config
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


def claim_shard_locally(
    worker: WorkflowWorkerThread,
    workspace: dict[str, Any],
    job: dict[str, Any],
    node: WorkflowNode,
    job_dir: Path,
    log_path: Path,
    *,
    shard_index: int,
    shard_input: Any,
    local_node_limit: int | None,
    control_snapshot: dict[str, Any] | None,
    allowed_node_keys: frozenset[str] | None,
    snapshot: CapacitySnapshot,
) -> bool:
    """Lease and submit one shard on the local code pool; False = no capacity.

    #495: the local shard lane used to build its ExecutionContext without
    ``node_code``, so every shard of a pure-local deployment died on the
    EXEC-CODE-002 backstop with a misleading "no published node code" error
    while the remote lane (``code_claim``) resolved the same code fine. The
    resolution now mirrors the ordinary local path (``schedule`` →
    ``code_dispatch``): resolve first and fail the shard with the true reason
    when the code is unrunnable — the backstop stays a backstop. A resolve
    failure terminates THIS shard, not the node (#520 review P2): the
    node-level write's status guard no-ops on the ``running`` row.

    PR #520 review P2: the same gap held for config — a shard node's
    declared ``config_schema``/``config`` (secrets, connections,
    ``timeout_seconds`` …) never resolved, so ``ExecutionContext.node_config``
    stayed empty and ``config_snapshot_json`` blank while the remote lane
    shipped the fully resolved config. Config now rides the same
    ``resolve_dispatch_node_config`` chain as ``schedule``.
    """
    workspace_id = workspace["id"]
    # Schema v61: workspace id IS the workflow key — the same source the lease
    # request below and the shard remote lane (code_claim) use.
    workflow_key = str(job["workspace_id"])
    # The lease claim's execution-control snapshot fields; None snapshot →
    # the defaults the claim guard accepts (mirror of the executor lane).
    control = control_snapshot or {}
    if not snapshot.has_capacity(workspace_id, node.key):
        return False
    run_payload = cached_run_payload(worker, job)
    # Config first, then code — the order of the ordinary local path
    # (schedule.py): both resolve before the lease, and an unresolvable
    # value fails the shard with the true reason instead of surfacing as a
    # mid-execution crash or a silently ignored config. #520 review P2: the
    # failure terminates THIS shard through the aggregate
    # (fail_claim_target_config) — the node-level write's status guard
    # no-ops once an earlier fan-out round flipped the node to running,
    # silently wedging the shard in pending forever.
    try:
        # Frozen snapshot (runtime-mutable keys re-resolved live) → vault
        # secret_refs → connection config + token; in-memory only
        # (VAULT-SECRET-001). The non-secret snapshot rides the lease as the
        # dispatch-time audit (CONFIG-RUNTIME-MUTABLE-001).
        node_config, config_snapshot_json = resolve_dispatch_node_config(
            worker, node, workflow_key, workspace_id, workspace, run_payload
        )
        # #495: same resolve order as the ordinary local path (#115) and the
        # shard remote lane — the currently published workspace code; frozen
        # pins apply only to quality-replay batches (per-pass memo inside).
        # resolve raises (ValueError) exactly when the node can never run; the
        # message then names the real reason instead of the executor backstop's
        # generic text.
        node_code = resolve_code_node_dispatch(
            worker, workspace_id, workflow_key, node, run_payload, job.get("node_code_pins")
        )
    except (ValueError, VaultError, JobServiceError) as exc:
        return fail_claim_target_config(
            worker, workspace_id, job, workflow_key, node, log_path, shard_index, str(exc)
        )
    claim = worker.leases.try_claim(
        LeaseClaimRequest(
            executor_id=CODE_EXECUTOR_ID,
            global_capacity=worker.settings.executor_runtime.code_capacity,
            workspace_id=workspace_id,
            job_id=job["id"],
            workflow_key=str(job["workspace_id"]),
            node_key=node.key,
            capability=node.capability,
            local_node_limit=local_node_limit,
            lease_ttl_seconds=worker.settings.executor_runtime.lease_ttl_seconds,
            log_path=str(log_path),
            execution_mode=control.get("execution_mode", "full"),
            target_node_key=control.get("target_node_key"),
            allowed_node_keys=tuple(sorted(allowed_node_keys)) if allowed_node_keys else (),
            shard_index=shard_index,
            # Non-secret resolved config audit, same channel as the ordinary
            # local path (CONFIG-RUNTIME-MUTABLE-001).
            config_snapshot_json=config_snapshot_json,
        )
    )
    if claim is None:
        return False  # capacity lost to a race; the next poll pass re-evaluates
    snapshot.record_claim(workspace_id, node.key)
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
        log_path=log_path,
        inputs=tuple(node.inputs),
        # Mirror the remote contract (#389 review P2-2): the shard output
        # file is an expected output on both paths, so a shard node whose
        # code writes no shard payload fails identically locally and
        # remotely instead of silently yielding an empty reduce payload.
        # #401 review P1-2 (both paths): the node's ordinary outputs are
        # EXCLUDED — a shard's product contract is only the per-index file.
        # Concurrent shards share one job dir and one object-storage
        # authority key per (job, node, name); with many shards of a node
        # running at once (v79), sibling shards writing the same ordinary
        # output name would clobber each other's artifacts and races the
        # per-shard missing-output check.
        expected_outputs=(f"shard_output-{shard_index}.json",),
        runtime={
            "node_execution": asdict(node.execution),
            "shard_index": shard_index,
            "shard_input": shard_input,
        },
        node_code=node_code,
        node_config=node_config,
    )
    submit_claim(worker, CODE_EXECUTOR_ID, claim, context)
    return True
