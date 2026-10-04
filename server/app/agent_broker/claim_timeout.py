"""The claim-time ``timeout_seconds`` decision for remote requests (#691).

CONFIG-RUNTIME-TIMEOUT-001 (model: ``services.runtime_reserved_config``):
for a remote agent/code request the Worker claim is the single decision
point. ``effective = resolve_timeout(base, L2)`` where the base (L0 platform
default + L1 revision node config) was frozen into the queued manifest at
enqueue, and L2 is the workspace override as a SCALAR projected by the claim
scan (``claim_scan.WORKSPACE_TIMEOUT_COLUMN``) — no revision or workspace
document is parsed here. The batch claim's write transaction re-runs
admission on the same selection row, so it reuses the selection-time
snapshot (no re-read, no lock). Rows without the projected column (the
unclaimable sweeper's query) make no decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.services.node_execution_config import (
    AGENT_DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
)
from server.app.services.runtime_reserved_config import (
    CONFIG_RESOLUTION_MANIFEST_KEY,
    SOURCE_ENQUEUE_SNAPSHOT,
    SOURCE_PLATFORM_DEFAULT,
    TIMEOUT_BASE_MANIFEST_KEY,
    TIMEOUT_KEY,
    resolve_timeout,
    valid_timeout,
)

WORKSPACE_TIMEOUT_COLUMN = "workspace_timeout_override"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _queued_base(manifest: Mapping[str, Any], kind: str) -> dict[str, Any]:
    base = _mapping(manifest.get(TIMEOUT_BASE_MANIFEST_KEY))
    if valid_timeout(base.get("value")) and base.get("source"):
        return {"value": base["value"], "source": base["source"]}
    # Queued by a pre-#691 Host: the enqueue-time value is the base.
    enqueued = (
        manifest.get(TIMEOUT_KEY)
        if kind == "code"
        else _mapping(manifest.get("execution")).get(TIMEOUT_KEY)
    )
    if valid_timeout(enqueued):
        return {"value": enqueued, "source": SOURCE_ENQUEUE_SNAPSHOT}
    default = DEFAULT_TIMEOUT_SECONDS if kind == "code" else AGENT_DEFAULT_TIMEOUT_SECONDS
    return {"value": default, "source": SOURCE_PLATFORM_DEFAULT}


def decide_claim_timeout(manifest: dict[str, Any], row: Mapping[str, Any], kind: str) -> None:
    """Decide and write the timeout into *manifest* in place (see module doc).

    Agent manifests carry it in ``execution`` (the caller re-renders the
    command spec afterwards); code manifests at the top level plus the plain
    ``config`` copy the node code sees.
    """
    if WORKSPACE_TIMEOUT_COLUMN not in row:
        return
    decided = resolve_timeout(
        _queued_base(manifest, kind),
        row[WORKSPACE_TIMEOUT_COLUMN],
        workspace_id=str(row.get("workspace_id") or ""),
        node_key=str(row.get("node_key") or ""),
    )
    if kind == "code":
        manifest[TIMEOUT_KEY] = decided["value"]
        config = manifest.get("config")
        if isinstance(config, dict) and TIMEOUT_KEY in config:
            config[TIMEOUT_KEY] = decided["value"]
    else:
        manifest["execution"] = {
            **_mapping(manifest.get("execution")),
            TIMEOUT_KEY: decided["value"],
        }
    manifest[CONFIG_RESOLUTION_MANIFEST_KEY] = {TIMEOUT_KEY: decided}
