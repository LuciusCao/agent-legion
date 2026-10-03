"""Runtime-adjustable reserved execution keys (#691, CONFIG-RUNTIME-MUTABLE-001).

The platform-reserved execution keys split into two mutability classes:

- **runtime-adjustable** (``RUNTIME_MUTABLE_RESERVED_KEYS`` =
  ``timeout_seconds``): a pure resource/elasticity knob that does not change
  what a node produces. It is NOT taken from the intake freeze; every
  dispatch re-resolves it along the usual chain — schema default (platform
  default: agent 1800s / code 600s) → node ``config`` (versioned with the
  job's revision) → workspace override (the live knob) — and a remote Worker
  claim re-resolves it once more for requests still queued in the Agent
  request queue. Jobs created/queued before an override change therefore run
  with the new value; an execution that already started keeps its value.
- **versioned-with-workflow** (``sandbox_network``): network egress is a
  security boundary, so it stays intake-frozen — loosening it must go through
  a workflow revision release (or a new job), never a live toggle that
  silently opens the network for in-flight jobs.

Every resolution reports its source so the per-run audit
(``node_runs.config_snapshot_json`` under ``CONFIG_RESOLUTION_AUDIT_KEY``;
the queued manifest under ``CONFIG_RESOLUTION_MANIFEST_KEY``) can reconstruct
which timeout a job actually ran with and why.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.config_schema import validate_config_values

RUNTIME_MUTABLE_RESERVED_KEYS = frozenset({"timeout_seconds"})

SOURCE_PLATFORM_DEFAULT = "platform_default"
SOURCE_NODE_CONFIG = "node_config"
SOURCE_WORKSPACE_OVERRIDE = "workspace_override"

# Top-level manifest key (agent + code requests) carrying the dispatch-time
# resolution; promote_claim copies it into the node_runs audit snapshot.
CONFIG_RESOLUTION_MANIFEST_KEY = "config_resolution"
# Meta key inside node_runs.config_snapshot_json (the rest of the snapshot
# is the plain non-secret config map, unchanged).
CONFIG_RESOLUTION_AUDIT_KEY = "_config_resolution"


def _properties(config_schema: Mapping[str, Any]) -> Mapping[str, Any]:
    properties = config_schema.get("properties") if isinstance(config_schema, Mapping) else None
    return properties if isinstance(properties, Mapping) else {}


def resolve_runtime_reserved(
    config_schema: Mapping[str, Any],
    node_config: Mapping[str, Any],
    workspace_override: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Dispatch-time resolution of the runtime-adjustable reserved keys.

    Returns ``{key: {"value": ..., "source": ...}}`` for each such key the
    effective schema carries (the reserved merge always adds it). Both layers
    are validated against the key's schema property like the full chain
    does, so an invalid live override fails the node with the usual message
    (``ConfigSchemaError``) instead of shipping a bogus timeout.
    """
    resolved: dict[str, dict[str, Any]] = {}
    properties = _properties(config_schema)
    for key in sorted(RUNTIME_MUTABLE_RESERVED_KEYS):
        prop = properties.get(key)
        if not isinstance(prop, Mapping):
            continue
        sub_schema = {"type": "object", "properties": {key: dict(prop)}}
        layers = (
            (workspace_override, SOURCE_WORKSPACE_OVERRIDE, "workspace node config"),
            (node_config, SOURCE_NODE_CONFIG, "node config"),
        )
        for layer, _source, path in layers:
            if key in layer:
                validate_config_values(sub_schema, {key: layer[key]}, partial=True, path=path)
        picked = next(((layer[key], source) for layer, source, _ in layers if key in layer), None)
        if picked is None and "default" in prop:
            picked = (prop["default"], SOURCE_PLATFORM_DEFAULT)
        if picked is not None:
            resolved[key] = {"value": picked[0], "source": picked[1]}
    return resolved


def _valid_timeout(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def claim_time_timeout(
    default: int,
    node_config: Mapping[str, Any],
    workspace_override: Mapping[str, Any],
) -> dict[str, Any]:
    """Remote-claim re-resolution of ``timeout_seconds`` (same chain, lenient).

    Runs inside the lock-free claim admission, which must never raise for a
    single row: a malformed layer is skipped (the dispatch-time strict check
    already validated what was enqueued; workspace override writes are
    schema-validated too), falling through to the next layer.
    """
    for layer, source in (
        (workspace_override, SOURCE_WORKSPACE_OVERRIDE),
        (node_config, SOURCE_NODE_CONFIG),
    ):
        value = layer.get("timeout_seconds") if isinstance(layer, Mapping) else None
        if _valid_timeout(value):
            return {"value": value, "source": source}
    return {"value": default, "source": SOURCE_PLATFORM_DEFAULT}


def audit_snapshot(
    config: Mapping[str, Any], resolution: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The node_runs audit document: plain config plus the resolution meta key."""
    snapshot = dict(config)
    if resolution:
        snapshot[CONFIG_RESOLUTION_AUDIT_KEY] = dict(resolution)
    return snapshot
