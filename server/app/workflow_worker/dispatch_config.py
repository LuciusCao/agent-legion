"""Dispatch-time node config resolution for code-pool claims.

One place for the full chain: frozen intake snapshot (falling back to live
resolution) → workspace vault secret_ref resolution → instance-level external
connection injection. Everything here is in-memory only: frozen payloads keep
secret refs, and the injected connection block (endpoint config + plaintext
token) is never persisted (VAULT-SECRET-001) and never reaches agent
manifests (CONFIG-MANIFEST-001).

P-0.5 step 2: every executor-routed node is code-routed (the implicit code
pool), and the schema comes from the node-declared ``config_schema`` (v47
harvested executor declarations onto the revision nodes) plus the
platform-reserved execution keys — no executor definition is consulted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from server.app.services.connection_tokens import (
    ConnectionTokenService,
    inject_connection_config,
)
from server.app.services.node_config import dispatch_effective_config
from server.app.services.node_execution_config import (
    merge_reserved_execution_schema,
    node_config_reserved_defaults,
)
from server.app.services.vault import VaultService
from server.app.workflows.definition import WorkflowNode

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread


def resolve_dispatch_node_config(
    worker: WorkflowWorkerThread,
    node: WorkflowNode,
    workflow_key: str,
    workspace_id: str,
    workspace: Mapping[str, Any] | None,
    batch_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Effective node config with secrets and connection config resolved."""
    config_schema = merge_reserved_execution_schema(node.config_schema)
    # Frozen batches predating the reserved keys are padded from the node's
    # own declared config values (the v47 harvest target); frozen wins.
    fallback_defaults = node_config_reserved_defaults(node.config)
    node_config = dispatch_effective_config(
        config_schema, node, workflow_key, workspace, batch_payload, fallback_defaults
    )
    vault = VaultService(worker.job_db.path, worker.settings.config)
    node_config = vault.resolve_secret_refs(node_config, workspace_id)
    # Plaintext tokens never enter agent manifests (CONFIG-MANIFEST-001); the
    # connection block is injected in memory for the code runtime only.
    return inject_connection_config(
        node_config,
        config_schema,
        ConnectionTokenService(worker.job_db.path, worker.settings.config),
    )
