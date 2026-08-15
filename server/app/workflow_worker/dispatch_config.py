"""Dispatch-time node config resolution for executor claims.

One place for the full chain: frozen intake snapshot (falling back to live
resolution) → workspace vault secret_ref resolution → instance-level external
connection injection. Everything here is in-memory only: frozen payloads keep
secret refs, and the injected connection block (endpoint config + plaintext
token) is never persisted (VAULT-SECRET-001) and never reaches agent
manifests (CONFIG-MANIFEST-001).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from server.app.executors.config import CodeExecutorConfig
from server.app.services.connection_tokens import (
    ConnectionTokenService,
    inject_connection_config,
)
from server.app.services.node_config import (
    dispatch_effective_config,
    executor_definition_capability_schema,
)
from server.app.services.vault import VaultService
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


def resolve_dispatch_node_config(
    worker: WorkflowWorkerThread,
    executor_id: str,
    node: WorkflowNode,
    workflow_key: str,
    workspace_id: str,
    workspace: Mapping[str, Any] | None,
    batch_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Effective node config with secrets and connection config resolved."""
    # Definitions come from the registry (atomically swapped on hot reload),
    # not settings.executor_definitions: the worker mixes definitions with
    # registry capacities/pools in one pass, so reading both from the one
    # authority keeps the pass consistent.
    config_schema = executor_definition_capability_schema(
        worker.registry.definitions(), executor_id, node.capability
    )
    node_config = dispatch_effective_config(
        config_schema,
        node,
        workflow_key,
        workspace,
        batch_payload,
    )
    vault = VaultService(worker.job_db.path, worker.settings.config)
    node_config = vault.resolve_secret_refs(node_config, workspace_id)
    # Only code executors receive the injected connection: agent-runtimes
    # build a manifest, and plaintext tokens must never enter it
    # (CONFIG-MANIFEST-001).
    definition = worker.registry.definitions().get(executor_id)
    if not isinstance(definition, CodeExecutorConfig):
        return node_config
    return inject_connection_config(
        node_config,
        config_schema,
        ConnectionTokenService(worker.job_db.path, worker.settings.config),
    )
