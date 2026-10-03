"""The runtime-adjustable ``timeout_seconds`` model (#691, CONFIG-RUNTIME-TIMEOUT-001).

Of the platform-reserved execution keys, ``sandbox_network`` stays
intake-frozen (network egress is a security boundary: loosening it ships with
a workflow revision), while ``timeout_seconds`` follows ONE model:

- Layers: L0 platform default by kind (agent 1800s / code 600s, the reserved
  schema default); L1 the job's pinned revision node ``config.timeout_seconds``
  (immutable per job); L2 the workspace override — the only mutable layer.
- ``base = timeout_base(L0, L1)`` is computed where the node definition is at
  hand (dispatch/enqueue on the Host). The queued manifest of a remote
  request carries it (``TIMEOUT_BASE_MANIFEST_KEY``) — an intermediate, not a
  decision: the claim never needs the revision document for the timeout.
- Exactly one decision point per execution — local code: dispatch; remote
  agent/code: the Worker claim (candidate-selection snapshot, reused by the
  write transaction). ``effective = resolve_timeout(base, L2)`` is the single
  pure function every path uses; after the decision the value is fixed and the
  audit records exactly the decided value + source.
- An invalid L2 (valid = integer, not bool, >= 1 — the reserved schema) never
  fails anything: every path falls back to the base, audits source
  ``workspace_override_invalid`` and logs one structured warning.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

TIMEOUT_KEY = "timeout_seconds"

SOURCE_PLATFORM_DEFAULT = "platform_default"
SOURCE_NODE_CONFIG = "node_config"
SOURCE_WORKSPACE_OVERRIDE = "workspace_override"
SOURCE_WORKSPACE_OVERRIDE_INVALID = "workspace_override_invalid"
# Requests queued by a pre-#691 Host carry no base: their enqueue-time value
# is the base (it may already contain the then-current override).
SOURCE_ENQUEUE_SNAPSHOT = "enqueue_snapshot"

# Queued manifest: the enqueue-time base (L0+L1) and the claim-time decision.
TIMEOUT_BASE_MANIFEST_KEY = "timeout_base"
CONFIG_RESOLUTION_MANIFEST_KEY = "config_resolution"
# Meta key inside node_runs.config_snapshot_json (the rest is the plain
# non-secret config map, unchanged).
CONFIG_RESOLUTION_AUDIT_KEY = "_config_resolution"


def valid_timeout(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def timeout_base(default: int, node_config: Mapping[str, Any]) -> dict[str, Any]:
    """``resolve(L0, L1)``; L1 is publish-validated, a malformed one reads as unset."""
    value = node_config.get(TIMEOUT_KEY)
    if valid_timeout(value):
        return {"value": value, "source": SOURCE_NODE_CONFIG}
    return {"value": default, "source": SOURCE_PLATFORM_DEFAULT}


@lru_cache(maxsize=256)
def _warn_invalid_override(workspace_id: str, node_key: str, raw: str) -> None:
    # Cached: claim admission re-evaluates queued candidates every poll, so
    # one warning per distinct (workspace, node, raw value) is enough.
    extra = {"workspace_id": workspace_id, "node_key": node_key, "raw_value": raw}
    logger.warning("invalid workspace timeout_seconds override ignored (base kept)", extra=extra)


def resolve_timeout(
    base: Mapping[str, Any], override: Any, *, workspace_id: str, node_key: str
) -> dict[str, Any]:
    """``effective = resolve(base, L2)`` — the single decision function.

    ``override`` is the raw L2 scalar; ``None`` (absent / JSON null) means unset.
    """
    if override is None:
        return {"value": base["value"], "source": base["source"]}
    if valid_timeout(override):
        return {"value": override, "source": SOURCE_WORKSPACE_OVERRIDE}
    _warn_invalid_override(workspace_id, node_key, json.dumps(override, default=str))
    return {"value": base["value"], "source": SOURCE_WORKSPACE_OVERRIDE_INVALID}


def dispatch_timeout(
    config_schema: Mapping[str, Any],
    node: Any,
    override: Mapping[str, Any],
    workspace: Mapping[str, Any] | None,
    *,
    decide: bool,
) -> dict[str, Any] | None:
    """Host-side entry: the base (remote enqueue, ``decide=False``) or the local
    code pool's decision (``decide=True``); None when the schema carries no
    reserved timeout."""
    # L0 as the effective schema carries it (the reserved merge seeds it).
    prop = (config_schema.get("properties") or {}).get(TIMEOUT_KEY)
    if not isinstance(prop, Mapping) or not valid_timeout(prop.get("default")):
        return None
    base = timeout_base(prop["default"], node.config)
    if not decide:
        return base
    workspace_id = str((workspace or {}).get("id") or "")
    return resolve_timeout(
        base, override.get(TIMEOUT_KEY), workspace_id=workspace_id, node_key=node.key
    )


def chain_override(override: Mapping[str, Any]) -> dict[str, Any]:
    """L2 as fed to the generic config chain: an invalid timeout is dropped so
    the generic validation (intake freeze, runtime_mutable re-resolution) can
    never fail on it — ``resolve_timeout`` owns that decision."""
    if TIMEOUT_KEY in override and not valid_timeout(override[TIMEOUT_KEY]):
        return {k: v for k, v in override.items() if k != TIMEOUT_KEY}
    return dict(override)


def run_audit_json(config: Mapping[str, Any], decided: Mapping[str, Any] | None) -> str:
    """The node_runs audit document: plain config plus the decided timeout."""
    snapshot = dict(config)
    if decided:
        snapshot[CONFIG_RESOLUTION_AUDIT_KEY] = {TIMEOUT_KEY: dict(decided)}
    return json.dumps(snapshot, sort_keys=True, default=str)


def manifest_run_audit_json(manifest: Mapping[str, Any]) -> str:
    """``run_audit_json`` for a claimed remote manifest (config + claim decision)."""
    decided = (manifest.get(CONFIG_RESOLUTION_MANIFEST_KEY) or {}).get(TIMEOUT_KEY)
    return run_audit_json(manifest.get("config") or {}, decided)
