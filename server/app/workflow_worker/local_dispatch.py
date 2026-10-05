"""The local code pool's single dispatch decision entry (#869).

Every execution the local code pool runs — an ordinary code node
(``claim_submit``) and a local shard execution (``shard_dispatch``) — is
decided here, so the ``timeout_seconds`` decision + ``_config_resolution``
audit (CONFIG-RUNTIME-TIMEOUT-001), the node's business config and the
published node code (EXEC-CODE-002) resolve identically on every local path.
Before #869 the local shard fallback built its context without this
decision: no node config (timeout stuck at the platform default, business
config missing), no audit and no node code.

The structural guard ``tests/services/test_runtime_timeout_paths_guard.py``
pins that every local ``ExecutionContext`` construction goes through here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import JobServiceError
from server.app.services.vault import VaultError
from server.app.workflow_worker.agent_claim import cached_run_payload, fail_node_config
from server.app.workflow_worker.code_dispatch import resolve_code_node_dispatch
from server.app.workflow_worker.dispatch_config import resolve_dispatch_node_config
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


@dataclass(frozen=True)
class LocalCodeDispatch:
    """Everything the local code pool decides at dispatch for one execution."""

    node_config: dict[str, Any]
    node_code: str
    config_snapshot_json: str

    @property
    def implementation_hash(self) -> str:
        # Claim-time mirror of CodeDispatchService's enqueue digest (v85, #645).
        return hashlib.sha256(self.node_code.encode("utf-8")).hexdigest()


def resolve_local_code_dispatch(
    worker: WorkflowWorkerThread,
    node: WorkflowNode,
    workflow_key: str,
    workspace_id: str,
    workspace: dict[str, Any],
    job: dict[str, Any],
) -> LocalCodeDispatch:
    """Resolve the decision; raises ``ValueError`` / ``VaultError`` /
    ``JobServiceError`` for a configuration failure of this node."""
    run_payload = cached_run_payload(worker, job)
    # Frozen snapshot (runtime-mutable keys re-resolved live) → vault
    # secret_refs → connection config + token; all in-memory only
    # (VAULT-SECRET-001, CONFIG-MANIFEST-001). The non-secret snapshot is
    # persisted onto the node_runs row as the dispatch-time audit
    # (CONFIG-RUNTIME-MUTABLE-001).
    node_config, snapshot_json = resolve_dispatch_node_config(
        worker, node, workflow_key, workspace_id, workspace, run_payload
    )
    # Node code (EXEC-CODE-002): since #115 ordinary jobs dispatch the
    # currently published workspace code; the frozen pins (job snapshot's
    # node_code_pins, then the intake batch's node_code_versions) are honored
    # only for quality-replay batches, where a hash mismatch fails the node
    # (fail closed, EXEC-CODE-003).
    node_code = resolve_code_node_dispatch(
        worker, workspace_id, workflow_key, node, run_payload, job.get("node_code_pins")
    )
    return LocalCodeDispatch(node_config, node_code, snapshot_json)


def decide_local_code_dispatch(
    worker: WorkflowWorkerThread,
    workspace: dict[str, Any],
    job: dict[str, Any],
    node: WorkflowNode,
    workflow_key: str,
    log_path: Path,
    *,
    execution_generation: int = 0,
) -> LocalCodeDispatch | None:
    """The decision, or None after failing the node as a configuration error."""
    workspace_id = str(workspace["id"])
    try:
        return resolve_local_code_dispatch(worker, node, workflow_key, workspace_id, workspace, job)
    except (ValueError, VaultError, JobServiceError) as exc:
        fail_node_config(
            worker,
            workspace_id,
            job,
            workflow_key,
            node,
            log_path,
            str(exc),
            execution_generation=execution_generation,
        )
        return None
