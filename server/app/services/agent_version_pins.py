"""Per-run Agent version pins (schema v29, quality replay).

A quality replay freezes ``agent_versions[node_key]`` with ``{agent_id,
version, definition_hash}`` into the copy batch's intake payload so the copy
job dispatches one explicit immutable Agent version — draft, published, or
archived (comparing old or candidate versions is the point of the quality
loop) — instead of whatever is published when the node is claimed. Pin
mismatches fail closed, mirroring the custom-node-code pin contract
(EXEC-CODE-002/003).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.db.connection import DatabaseDsn
from server.app.services.agent_service import published_agent_definitions
from server.app.services.versioned_entities import VersionedEntityStore


def agent_version_pin(
    batch_payload: Mapping[str, Any] | None,
    node_key: str,
) -> dict[str, Any] | None:
    """Read one node's frozen Agent-version pin from an intake batch payload."""
    if not isinstance(batch_payload, Mapping):
        return None
    pins = batch_payload.get("agent_versions")
    if not isinstance(pins, Mapping):
        return None
    pin = pins.get(node_key)
    return dict(pin) if isinstance(pin, Mapping) else None


def resolve_dispatch_agent_definition(
    database_dsn: DatabaseDsn,
    agent_id: str,
    pin: Mapping[str, Any] | None,
) -> AgentDefinition | None:
    """Resolve the definition for dispatch; a frozen per-run version pin wins.

    Returns None when the unpinned published definition is gone (the caller
    reports the invalid route); a pin whose agent, version, or definition
    hash no longer matches raises ValueError so the node fails closed.
    """
    if pin is None:
        return published_agent_definitions(database_dsn).get(agent_id)
    pinned_agent = str(pin.get("agent_id") or "")
    if pinned_agent != agent_id:
        raise ValueError(
            f"Agent version pin targets {pinned_agent!r} but the node routes to {agent_id!r}"
        )
    version = int(pin.get("version") or 0)
    store = VersionedEntityStore(database_dsn, "agent")
    entity = store.get_version(agent_id, version, None)
    if entity is None:
        raise ValueError(f"pinned Agent version {agent_id!r} v{version} does not exist")
    expected_hash = str(pin.get("definition_hash") or "")
    if expected_hash and entity.definition_hash != expected_hash:
        raise ValueError(f"pinned Agent version {agent_id!r} v{version} definition hash mismatch")
    return AgentDefinition.model_validate(entity.definition)
