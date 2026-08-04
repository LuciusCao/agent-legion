"""Buffered executor-claim flush for the workflow worker's claim loop.

``claim_executor_node`` only buffers a prepared claim while the pass pops
ready candidates; this module leases everything buffered at pass end in one
transaction per executor, trading one commit + advisory-lock round trip per
node (~30-100ms each, serialized by the lock) for one per executor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors.models import ExecutionContext, LeaseClaimRequest
from server.app.workflow_worker.execution import submit_claim
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


@dataclass
class PreparedClaim:
    """Everything needed to lease and submit one executor-routed node."""

    request: LeaseClaimRequest
    executor_id: str
    workspace: dict[str, Any]
    job: dict[str, Any]
    node: WorkflowNode
    job_dir: Path
    log_path: Path
    inputs: tuple[str, ...]
    node_config: dict[str, Any]
    node_code: str | None = None


def flush_prepared_claims(worker: WorkflowWorkerThread) -> None:
    """Lease all buffered claims (one transaction per executor) and submit."""
    pending = worker._pending_claims
    if not pending:
        return
    for executor_id in sorted({p.executor_id for p in pending}):
        group = [p for p in pending if p.executor_id == executor_id]
        results = worker.leases.try_claim_many([p.request for p in group])
        for prepared, claimed in zip(group, results, strict=True):
            if claimed is None:
                continue
            context = ExecutionContext(
                execution_id=claimed.execution_id,
                lease_id=claimed.lease_id,
                node_run_id=claimed.node_run_id,
                executor_id=claimed.executor_id,
                workspace_id=claimed.workspace_id,
                job_id=claimed.job_id,
                workflow_key=claimed.workflow_key,
                node_key=claimed.node_key,
                capability=claimed.capability,
                workspace=dict(prepared.workspace),
                job=dict(prepared.job),
                job_dir=prepared.job_dir,
                log_path=prepared.log_path,
                inputs=prepared.inputs,
                expected_outputs=tuple(prepared.node.outputs),
                runtime={"node_execution": asdict(prepared.node.execution)},
                node_config=prepared.node_config,
                node_code=prepared.node_code,
            )
            submit_claim(worker, executor_id, claimed, context)
