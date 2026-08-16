"""Dispatch-time node code resolution (EXEC-CODE-002, split from
``node_codes`` for the size budget): intake freeze pins, the frozen →
workspace-published → global-seed dispatch chain, and the runnable-capability
guard.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.services.node_codes import (
    _ENTITY_TYPE,
    NodeCodeService,
    _entity_key,
    _split_entity_key,
    _to_row,
)
from server.app.services.versioned_entities import VersionedEntityStore

logger = logging.getLogger(__name__)


def freeze_node_code_versions(
    database_dsn: DatabaseDsn,
    custom_nodes_enabled: bool,
    workspace_id: str,
    workflow_key: str,
    node_keys: list[str],
) -> dict[str, dict[str, Any]]:
    """Intake freeze: published ``{node_key: {version, code_hash}}`` pins.

    Covers workspace-scoped published versions and (as fallback) global
    factory-seeded ones, so jobs pin the exact code they start with. Nodes
    with no published code at either scope simply do not appear; the gate
    short-circuits to an empty mapping so intake never touches the table when
    the feature is off.
    """
    if not custom_nodes_enabled or not node_keys:
        return {}
    store = VersionedEntityStore(database_dsn, _ENTITY_TYPE)
    keys = [_entity_key(workflow_key, node_key) for node_key in node_keys]
    pins: dict[str, dict[str, Any]] = {}
    for entity in store.list_published_keys(workspace_id, keys):
        pins[_split_entity_key(entity.entity_key)[1]] = {
            "version": entity.version,
            "code_hash": entity.definition_hash,
        }
    workspace_pinned = set(pins)
    missing_keys = [
        key
        for key, node_key in zip(keys, node_keys, strict=True)
        if node_key not in workspace_pinned
    ]
    if missing_keys:
        for entity in store.list_published_keys(None, missing_keys):
            pins[_split_entity_key(entity.entity_key)[1]] = {
                "version": entity.version,
                "code_hash": entity.definition_hash,
            }
    return pins


def _get_version_scoped(
    service: NodeCodeService,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    version: int,
) -> dict[str, Any] | None:
    """Version lookup across scopes: workspace first, then the global seed."""
    row = service.get_code_by_version(workspace_id, workflow_key, node_key, version)
    if row is None:
        entity = service._store.get_version(_entity_key(workflow_key, node_key), version, None)
        row = _to_row(entity) if entity else None
    return row


def resolve_dispatch_node_code(
    database_dsn: DatabaseDsn,
    custom_nodes_enabled: bool,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    frozen: dict[str, Any] | None,
) -> str | None:
    """Dispatch-time code text: frozen job pin → workspace published → global
    factory seed → None (unrunnable; the caller fails the node).

    One DB read per dispatch, same cadence as the vault secret resolution it
    runs next to; the 30s route cache in ``routing.py`` only covers executor
    bindings and is deliberately not consulted here. The gate short-circuits
    to None instead of raising so a disabled feature never breaks dispatch.
    """
    if not custom_nodes_enabled:
        return None
    service = NodeCodeService(database_dsn)
    if frozen is not None:
        row = _get_version_scoped(
            service, workspace_id, workflow_key, node_key, int(frozen["version"])
        )
        if row is not None:
            # Fail closed on hash drift: the frozen pin and the stored code
            # must match exactly, otherwise the snapshot was tampered with.
            if row["code_hash"] != frozen.get("code_hash"):
                raise ValueError(
                    f"frozen node code hash mismatch for {workflow_key}/{node_key} "
                    f"v{frozen['version']}"
                )
            return str(row["code"])
        logger.warning(
            "frozen node code version missing, falling back to published: "
            "workspace=%s workflow=%s node=%s version=%s",
            workspace_id,
            workflow_key,
            node_key,
            frozen.get("version"),
        )
    row = service.get_effective_code(workspace_id, workflow_key, node_key)
    if row is None:
        row = service.get_global_published(workflow_key, node_key)
    return str(row["code"]) if row is not None else None


def require_runnable_capability(
    capabilities: dict[str, Any], capability: str, node_code: str | None
) -> None:
    """A capability with no published node code (either scope) has nothing to
    run: fail fast with a config error."""
    if capability in capabilities and node_code is None:
        raise ValueError(
            f"capability {capability!r} has no published node code "
            "(workspace version or global factory seed, EXEC-CODE-002)"
        )
