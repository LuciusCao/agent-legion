"""Dispatch-time node code resolution (EXEC-CODE-002, split from
``node_codes`` for the size budget): intake freeze pins and the dispatch
chain. Since #115 ordinary jobs resolve the currently published workspace
code; the frozen pins are honored only for
quality-replay batches (``frozen_dispatch_pin`` gates on the marker).
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.services.node_codes import (
    _ENTITY_TYPE,
    NodeCodeService,
    _entity_key,
    _split_entity_key,
)
from server.app.services.versioned_entities import VersionedEntityStore


def freeze_node_code_versions(
    database_dsn: DatabaseDsn,
    custom_nodes_enabled: bool,
    workspace_id: str,
    workflow_key: str,
    node_keys: list[str],
) -> dict[str, dict[str, Any]]:
    """Intake freeze: published ``{node_key: {version, code_hash}}`` pins.

    Covers workspace-scoped published versions, so jobs pin the exact code
    they start with. Nodes with no published code simply do not appear; the gate
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
    return pins


def _get_pinned_rows(
    service: NodeCodeService,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    version: int,
) -> list[dict[str, Any]]:
    """Rows matching a frozen pin; legacy global rows remain replay-readable."""
    rows = []
    row = service.get_code_by_version(workspace_id, workflow_key, node_key, version)
    if row is not None:
        rows.append(row)
    row = service.get_global_code_by_version(workflow_key, node_key, version)
    if row is not None:
        rows.append(row)
    return rows


def resolve_dispatch_node_code(
    database_dsn: DatabaseDsn,
    custom_nodes_enabled: bool,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    frozen: dict[str, Any] | None,
) -> str | None:
    """Dispatch-time code text: latest workspace published → None
    (unrunnable; the caller fails the node). A *frozen* pin is
    only ever passed for quality-replay batches (#115) and fails closed: a
    hash mismatch raises, and a pinned version missing at BOTH scopes is data
    corruption and raises too — never silently substituted.

    One DB read per dispatch, same cadence as the vault secret resolution it
    runs next to; the 30s route cache in ``routing.py`` only covers executor
    bindings and is deliberately not consulted here. The gate short-circuits
    to None instead of raising so a disabled feature never breaks dispatch.
    """
    if not custom_nodes_enabled:
        return None
    service = NodeCodeService(database_dsn)
    if frozen is not None:
        rows = _get_pinned_rows(
            service, workspace_id, workflow_key, node_key, int(frozen["version"])
        )
        for row in rows:
            if row["code_hash"] == frozen.get("code_hash"):
                return str(row["code"])
        if rows:
            # Fail closed on hash drift: the pin matches neither scope's row
            # at that version. A workspace publish with a colliding version
            # number is fine as long as the other scope matches the pin.
            raise ValueError(
                f"frozen node code hash mismatch for {workflow_key}/{node_key} v{frozen['version']}"
            )
        # The frozen version vanished at both current and legacy scopes: versioned entities are
        # immutable, so this means data corruption. Fail closed — silently
        # substituting the current published code would run code the job
        # never froze.
        raise ValueError(
            f"frozen node code version missing for {workflow_key}/{node_key} "
            f"v{frozen.get('version')} (data corruption)"
        )
    published = service.get_effective_code(workspace_id, workflow_key, node_key)
    return str(published["code"]) if published is not None else None
