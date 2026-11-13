"""Frozen per-run execution pins for current and historical quality replays.

Current replays freeze ``node_profiles[node_key]`` as
``{revision_id, node_key, profile_hash}`` (#1079, #440 D6). The pure profile
hash and validation primitives live in ``agent_node_profile`` and are
re-exported here for the established import path. The workflow worker
re-verifies the hash before enqueue and copies the pin into the queued
manifest (:data:`MANIFEST_KEY`), which exempts the request from the broker's
live revision re-read of execution / prompt (EXEC-QUALITY-REPLAY-001).

Historical ``agent_versions[node_key]`` pins remain read-only compatibility
(#936): existing runs still resolve their exact immutable Agent version and
definition hash, failing closed on drift. New replays never write these pins.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.db.dialect import ConnectSource
from server.app.services.agent_node_profile import PROFILE_FIELDS as PROFILE_FIELDS
from server.app.services.agent_node_profile import node_profile_fields as node_profile_fields
from server.app.services.agent_node_profile import node_profile_hash as node_profile_hash
from server.app.services.agent_node_profile import node_profile_pin_error as node_profile_pin_error
from server.app.services.versioned_entities import VersionedEntityStore

PIN_KEY = "node_profiles"

#: Manifest key carrying the pin into the queued request (audit + claim
#: discriminator): a pinned replay's execution is frozen at enqueue and the
#: claim must not re-read it from the job's revision (the copy job keeps the
#: original revision link while its snapshot carries the chosen profile).
MANIFEST_KEY = "node_profile_pin"


def node_profile_pin(run_payload: Mapping[str, Any] | None, node_key: str) -> dict[str, Any] | None:
    """Read one node's frozen profile pin from a run's frozen pins."""
    if not isinstance(run_payload, Mapping):
        return None
    pins = run_payload.get(PIN_KEY)
    if not isinstance(pins, Mapping):
        return None
    pin = pins.get(node_key)
    return dict(pin) if isinstance(pin, Mapping) else None


def agent_version_pin(
    run_payload: Mapping[str, Any] | None, node_key: str
) -> dict[str, Any] | None:
    """Read one node's historical Agent-version pin from a run's frozen pins."""
    if not isinstance(run_payload, Mapping):
        return None
    pins = run_payload.get("agent_versions")
    if not isinstance(pins, Mapping):
        return None
    pin = pins.get(node_key)
    return dict(pin) if isinstance(pin, Mapping) else None


def resolve_pinned_agent_definition(
    database_dsn: ConnectSource, workspace_id: str, agent_id: str, pin: Mapping[str, Any]
) -> AgentDefinition:
    """Resolve the exact Agent version a historical per-run pin names.

    ``database_dsn`` accepts the JobQueries facade or a bare DSN string
    (BOUNDARY-DATA-001, #187). Strictly workspace-scoped (schema v46) with
    no global fallback. A pin whose agent, version, or definition hash no
    longer matches raises ValueError so the node fails closed. The unpinned
    (published) path lives in the agent node profile facade
    (``agent_node_profile_catalog.resolve_dispatch_agent_profile``, #932).
    """
    pinned_agent = str(pin.get("agent_id") or "")
    if pinned_agent != agent_id:
        raise ValueError(
            f"Agent version pin targets {pinned_agent!r} but the node routes to {agent_id!r}"
        )
    version = int(pin.get("version") or 0)
    store = VersionedEntityStore(database_dsn, "agent")
    entity = store.get_version(agent_id, version, workspace_id)
    if entity is None:
        raise ValueError(
            f"pinned Agent version {agent_id!r} v{version} does not exist in workspace"
            f" {workspace_id!r}"
        )
    expected_hash = str(pin.get("definition_hash") or "")
    if expected_hash and entity.definition_hash != expected_hash:
        raise ValueError(f"pinned Agent version {agent_id!r} v{version} definition hash mismatch")
    return AgentDefinition.model_validate(entity.definition)
