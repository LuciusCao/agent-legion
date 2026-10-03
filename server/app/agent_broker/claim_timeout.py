"""Claim-time ``timeout_seconds`` refresh for queued Worker requests (#691).

``timeout_seconds`` is the runtime-adjustable reserved execution key
(``services.runtime_reserved_config``): the Host resolves it at dispatch,
but an agent/code request can then sit in the Agent request queue until a
Worker claims it. The claim admission re-resolves the same chain once more —
platform default → the job revision's node ``config`` → the live workspace
override — so a request still queued when an operator raises the timeout
picks the new value up. Once claimed, the value is fixed for that execution
(running executions are never changed).

The scan row carries the inputs (``revision_definition_json`` and
``workspace_node_config_json``, ``claim_scan.fetch_candidates``). Rows from
other queries (the unclaimable sweeper) lack the workspace column and keep
the enqueue-time value, as do legacy jobs without a pinned revision (no
node layer to re-read). Pure with respect to the database.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from server.app.services.node_execution_config import (
    AGENT_DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
)
from server.app.services.runtime_reserved_config import (
    CONFIG_RESOLUTION_MANIFEST_KEY,
    claim_time_timeout,
)

WORKSPACE_NODE_CONFIG_COLUMN = "workspace_node_config_json"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@lru_cache(maxsize=32)
def _revision_node_config(revision_json: str, node_key: str) -> tuple[bool, Any]:
    """(node found, its ``config.timeout_seconds``) — scalar result, safe to cache."""
    try:
        definition = json.loads(revision_json)
    except ValueError:
        return False, None
    node = _mapping(_mapping(_mapping(definition).get("nodes")).get(node_key))
    return bool(node), _mapping(node.get("config")).get("timeout_seconds")


@lru_cache(maxsize=32)
def _workspace_override(node_config_json: str, workflow_key: str, node_key: str) -> Any:
    try:
        node_config = json.loads(node_config_json or "{}")
    except ValueError:
        return None
    return _mapping(_mapping(_mapping(node_config).get(workflow_key)).get(node_key)).get(
        "timeout_seconds"
    )


def refresh_claim_timeout(manifest: dict[str, Any], row: Mapping[str, Any], kind: str) -> None:
    """Re-resolve ``timeout_seconds`` into *manifest* in place (see module doc).

    Agent manifests carry it in ``execution`` (the caller re-renders the
    command spec afterwards); code manifests at the top level (plus the
    plain ``config`` copy the node code sees).
    """
    revision_json = row.get("revision_definition_json")
    if WORKSPACE_NODE_CONFIG_COLUMN not in row or not revision_json:
        return
    node_key = str(row["node_key"])
    found, node_value = _revision_node_config(str(revision_json), node_key)
    if not found:
        return
    workflow_key = str(manifest.get("workflow_key") or row.get("workspace_id") or "")
    override_value = _workspace_override(
        str(row.get(WORKSPACE_NODE_CONFIG_COLUMN) or ""), workflow_key, node_key
    )
    default = DEFAULT_TIMEOUT_SECONDS if kind == "code" else AGENT_DEFAULT_TIMEOUT_SECONDS
    entry = claim_time_timeout(
        default,
        {} if node_value is None else {"timeout_seconds": node_value},
        {} if override_value is None else {"timeout_seconds": override_value},
    )
    if kind == "code":
        manifest["timeout_seconds"] = entry["value"]
        config = manifest.get("config")
        if isinstance(config, dict) and "timeout_seconds" in config:
            config["timeout_seconds"] = entry["value"]
    else:
        manifest["execution"] = {
            **_mapping(manifest.get("execution")),
            "timeout_seconds": entry["value"],
        }
    resolution = dict(_mapping(manifest.get(CONFIG_RESOLUTION_MANIFEST_KEY)))
    resolution["timeout_seconds"] = entry
    manifest[CONFIG_RESOLUTION_MANIFEST_KEY] = resolution
