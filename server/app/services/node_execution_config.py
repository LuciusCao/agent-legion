"""Reserved execution keys and the code-node schema merge (P-0.5).

The platform auto-merges ``timeout_seconds`` (integer, default 600, >= 1)
and ``sandbox_network`` (boolean, default false) into every code-routed
node's effective config schema, so the values travel the regular node
config chain (defaults → node config → workspace override → intake freeze).
The executor-capability fallback retired with the executor concept (schema
v47): the v47 harvest moved executor declarations onto the revision nodes,
so the node layer is the only declaration source left.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.executors.contracts import CodeCapabilityConfig
from server.app.workflows.node_config_schema import RESERVED_EXECUTION_KEYS

DEFAULT_TIMEOUT_SECONDS = 600


def reserved_execution_defaults(seed: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Reserved-key defaults; missing/invalid seeds get the platform defaults."""
    seed = seed or {}
    timeout = seed.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        timeout = DEFAULT_TIMEOUT_SECONDS
    sandbox = seed.get("sandbox_network")
    return {
        "timeout_seconds": timeout,
        "sandbox_network": sandbox if isinstance(sandbox, bool) else False,
    }


def node_config_reserved_defaults(node_config: Mapping[str, Any]) -> dict[str, Any]:
    """Reserved-key defaults from the reserved values a node declares in ``config``.

    Dispatch seed: the v47 harvest moved executor-level timeout/network
    values onto the revision nodes' ``config``, so frozen batches predating
    the reserved keys are padded from the node's own declared values.
    """
    return reserved_execution_defaults(
        {key: node_config[key] for key in RESERVED_EXECUTION_KEYS if key in node_config}
    )


def merge_reserved_execution_schema(
    schema: Mapping[str, Any] | None,
    seed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge the platform-reserved execution keys into a node's effective schema.

    Reserved properties sit *under* declared ones (a node config_schema may
    not redeclare them — the workflow loader rejects that; the tolerance
    only matters for legacy payloads).
    """
    defaults = reserved_execution_defaults(seed)
    reserved = {
        "timeout_seconds": {
            "type": "integer",
            "default": defaults["timeout_seconds"],
            "minimum": 1,
        },
        "sandbox_network": {"type": "boolean", "default": defaults["sandbox_network"]},
    }
    merged = dict(schema) if isinstance(schema, Mapping) else {}
    existing = merged.get("properties")
    merged["type"] = "object"
    merged["properties"] = {
        **reserved,
        **(dict(existing) if isinstance(existing, Mapping) else {}),
    }
    return merged


def resolved_code_capability(
    schema: dict[str, Any],
    resolved: Mapping[str, Any],
    seed: Mapping[str, Any] | None = None,
) -> CodeCapabilityConfig:
    """Fresh capability config carrying the dispatch-resolved schema/timeout/network.

    The code manifest fills ``config_schema``/``timeout_seconds``/
    ``sandbox_network`` from this object (manifest keys unchanged, worker
    protocol untouched): the Worker never consults an executor definition.
    """
    values = {**reserved_execution_defaults(seed), **resolved}
    return CodeCapabilityConfig(
        config_schema=dict(schema),
        timeout_seconds=values["timeout_seconds"],
        sandbox_network=values["sandbox_network"],
    )
