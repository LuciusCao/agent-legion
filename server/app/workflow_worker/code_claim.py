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
from server.app.executors.config import CodeExecutorConfig
from server.app.services.connection_tokens import (
    ConnectionTokenService,
    inject_connection_config,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.node_codes import resolve_dispatch_node_code
from server.app.services.node_config import dispatch_effective_config
from server.app.services.vault import VaultError, VaultService
from server.app.workflow_worker.agent_claim import cached_batch_payload, fail_node_config
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
    executor_id: str,
    workflow_key: str,
) -> bool:
    """Route a code-executor candidate to a remote code Worker when possible.

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
    if not dispatch.online_code_worker_available(node.capability):
        return False
    definition = worker.settings.executor_definitions.get(executor_id)
    capability_config = (
        definition.capabilities.get(node.capability)
        if isinstance(definition, CodeExecutorConfig)
        else None
    )
    if capability_config is None:
        return False

    batch_payload = cached_batch_payload(worker, job)
    # Same resolution order as the local path: frozen job version → published
    # → builtin repo file (resolve_dispatch_node_code, EXEC-CODE-001/002).
    frozen_pins = (batch_payload or {}).get("node_code_versions") or {}
    try:
        node_code = resolve_dispatch_node_code(
            worker.job_db.path,
            worker.settings.executor_runtime.workflows.custom_nodes_enabled,
            workspace_id,
            workflow_key,
            node.key,
            frozen_pins.get(node.key),
        )
        if node_code is not None:
            code_text = node_code
        else:
            code_text = (Path(worker.settings.root_dir) / capability_config.path).read_text(
                encoding="utf-8"
            )
    except (ValueError, OSError) as exc:
        return fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
    if not is_worker_eligible(code_text, Path(worker.settings.root_dir)):
        return False

    schema = capability_config.config_schema
    try:
        unresolved = dispatch_effective_config(schema, node, workflow_key, workspace, batch_payload)
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

    if not dispatch.try_mark_in_flight(job_id, node.key):
        return True

    def _enqueue() -> None:
        try:
            dispatch.enqueue(
                capability=node.capability,
                capability_config=capability_config,
                workspace=workspace,
                job=job,
                workflow_key=workflow_key,
                node=node,
                job_dir=job_dir,
                log_path=log_path,
                inputs=inputs,
                code_text=code_text,
                custom_code=node_code is not None,
                config=config,
                secret_config=secret_config,
            )
        except (ValueError, VaultError, JobServiceError) as exc:
            # Same trade-off as the agent enqueue pool: a configuration error
            # fails this node instead of poisoning every later poll pass.
            fail_node_config(worker, workspace_id, job, workflow_key, node, log_path, str(exc))
        except Exception:
            # Unexpected failure (unconfigured bundle dir, DB errors): same
            # trade-off as the agent enqueue pool — log and leave the node
            # pending so the next poll pass re-evaluates it.
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
    worker._pass_claim_counts[key] = worker._pass_claim_counts.get(key, 0) + 1
    return True
