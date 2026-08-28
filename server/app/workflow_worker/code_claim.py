"""Code-capability → code-Worker routing for the workflow worker's claim path.

Split from ``schedule`` for the file-size budget. Batch 2 (design §7.1/§9):
a code-executor candidate goes to a remote code Worker when one is online
and the payload is Worker-eligible; anything else falls back to the local
``claim_executor_node`` path — the local executor stays the safety net.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.agent_broker.code_dispatch import (
    PlaintextSecretError,
    split_manifest_config,
)
from server.app.agent_broker.code_eligibility import is_worker_eligible
from server.app.services.connection_tokens import (
    ConnectionTokenService,
    inject_connection_config,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.node_code_pins import frozen_dispatch_pin
from server.app.services.node_code_resolution import resolve_dispatch_node_code
from server.app.services.node_config import dispatch_effective_config
from server.app.services.node_execution_config import (
    merge_reserved_execution_schema,
    node_config_reserved_defaults,
    resolved_code_capability,
)
from server.app.services.vault import VaultError, VaultService
from server.app.workflow_worker.agent_claim import cached_run_payload, fail_node_config
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


def try_claim_code_worker_node(
    worker: WorkflowWorkerThread,
    workspace: dict[str, Any],
    job: dict[str, Any],
    node: WorkflowNode,
    job_dir: Path,
    log_path: Path,
    inputs: tuple[str, ...],
    workflow_key: str,
) -> bool:
    """Route a code-pool candidate to a remote code Worker when possible.

    True = handled (enqueued, already queued/in flight, or failed as a
    configuration error); False = not Worker-routable right now, the caller
    falls back to local execution.
    """
    dispatch = worker.code_dispatch
    if dispatch is None:
        return False
    workspace_id = str(workspace["id"])
    job_id = str(job["id"])
    if dispatch.is_in_flight(job_id, node.key):
        return True
    if dispatch.broker.has_active_request(job_id, node.key):
        # Already queued/claimed for a Worker: never double-execute locally,
        # even if the last code Worker went away (no queued-timeout fallback,
        # batch-2 decision 3).
        return True
    if not dispatch.online_code_worker_available(node.capability, workspace_id):
        return False
    if not worker.code_stock.allows():
        # Code stockpile full (issue #125): leave the node pending for the
        # local pool instead of flooding the broker queue; a later pass
        # re-evaluates as the stock drains.
        return False

    run_payload = cached_run_payload(worker, job)
    # Same resolution order as the local path (#115): the currently
    # published workspace code; the frozen pins (job snapshot node_code_pins, then the run's
    # node_code_versions) apply only to quality-replay runs
    # (resolve_dispatch_node_code, EXEC-CODE-002).
    try:
        code_text = resolve_dispatch_node_code(
            worker.job_db.path,
            worker.settings.executor_runtime.workflows.custom_nodes_enabled,
            workspace_id,
            workflow_key,
            node.key,
            frozen_dispatch_pin(job.get("node_code_pins"), run_payload, node.key),
        )
        if code_text is None:
            # No published workspace code: nothing to ship to a Worker;
            # the local executor reports the missing code (EXEC-CODE-002).
            return False
    except (ValueError, OSError) as exc:
        return fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
    if not is_worker_eligible(code_text, Path(worker.settings.root_dir)):
        return False

    # P-0.5 step 2: the schema comes from the node-declared config_schema
    # plus the platform-reserved execution keys; frozen batches predating
    # them are padded from the node's own declared config values.
    schema = merge_reserved_execution_schema(node.config_schema)
    reserved_defaults = node_config_reserved_defaults(node.config)
    try:
        unresolved = dispatch_effective_config(
            schema, node, workflow_key, workspace, run_payload, reserved_defaults
        )
        config, secret_config = split_manifest_config(schema, unresolved)
    except PlaintextSecretError:
        # Legacy plaintext secrets can never be persisted for a Worker; the
        # local path resolves them in memory only.
        return False
    except ValueError as exc:
        # Config drift must fail THIS node, not abort the whole poll pass.
        return fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
    try:
        # Validate the full secret-resolution chain now so a broken vault
        # reference or connection fails the node at dispatch, not mid-claim.
        # The resolved plaintext itself is discarded — only references and
        # non-secret values are persisted (VAULT-SECRET-001).
        resolved = VaultService(worker.job_db.path, worker.settings.config).resolve_secret_refs(
            {**config, **secret_config}, workspace_id
        )
        inject_connection_config(
            resolved, schema, ConnectionTokenService(worker.job_db.path, worker.settings.config)
        )
    except (ValueError, VaultError, JobServiceError) as exc:
        return fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))

    # The manifest carries the resolved schema/timeout/network (keys
    # unchanged): the Worker never consults an executor definition (P-0.5).
    effective_capability = resolved_code_capability(schema, unresolved, reserved_defaults)

    if not dispatch.try_mark_in_flight(job_id, node.key):
        return True

    def _enqueue() -> None:
        try:
            dispatch.enqueue(
                capability=node.capability,
                capability_config=effective_capability,
                workspace=workspace,
                job=job,
                workflow_key=workflow_key,
                node=node,
                job_dir=job_dir,
                log_path=log_path,
                inputs=inputs,
                code_text=code_text,
                # All node code is DB-published since #96; the flag stays for protocol
                # stability and is always True now.
                custom_code=True,
                config=config,
                secret_config=secret_config,
            )
        except (ValueError, VaultError, JobServiceError) as exc:
            # Same trade-off as the agent enqueue pool: a configuration error
            # fails this node instead of poisoning every later poll pass.
            fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
        except Exception:
            # #204 broad-except audit: deliberate per-node containment.
            # Expected configuration failures (ValueError / VaultError /
            # JobServiceError) are caught above and fail this node; whatever
            # lands here is unexpected (unconfigured bundle dir, DB error).
            # The poll pass must survive it: the node is left pending for the
            # next pass to re-evaluate (the in-flight marker is discarded in
            # the finally), so a transient outage self-heals while a
            # persistent one repeats loudly — logger.exception keeps the full
            # traceback. Narrowing this further would let one broken node
            # abort the whole claim pass.
            logger.exception("code enqueue failed for %s.%s", job_id, node.key)
        finally:
            dispatch.discard_in_flight(job_id, node.key)

    # Staging + bundling run off the poll thread; the in-flight marker above
    # dedups until the closure finishes (the broker's unique index stays the
    # authoritative dedup).
    if not dispatch.enqueue_pool.submit(_enqueue):
        dispatch.discard_in_flight(job_id, node.key)
        return False
    key = f"code:{node.capability}"
    worker.state.pass_claim_counts[key] = worker.state.pass_claim_counts.get(key, 0) + 1
    return True
