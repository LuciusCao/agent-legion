"""Per-run node execution-profile pins (#1079, #440 D6, quality replay).

Since #440 P3 an agent node carries its own execution profile and that
profile versions with the workflow revision. A quality replay therefore
compares *revisions* (or the current Studio draft) instead of Agent
versions: the copy run freezes ``node_profiles[node_key]`` as::

    {revision_id, node_key, profile_hash}

``revision_id`` names where the profile came from (``None`` = the Studio
draft at replay creation); ``profile_hash`` is :func:`node_profile_hash` of
the node the copy job's snapshot carries. The replay setup transplants the
chosen profile into the copy job's own snapshot, so dispatch needs no extra
resolution — the claim only re-verifies the hash and fails the node closed
on any mismatch (mirroring the Agent-version pin, EXEC-QUALITY-REPLAY-001).

The legacy ``agent_versions`` pin (``agent_version_pins``) stays honored
for runs that already carry one; new replays never write it for
self-contained nodes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from server.app.workflows.schema import WorkflowNode

PIN_KEY = "node_profiles"

#: The node fields a profile transplant replaces and the hash covers: the
#: whole ``execution`` block (runtime + provider/model/thinking/prompt) and
#: the four self-contained profile fields (#933).
PROFILE_FIELDS = ("execution", "requires_labels", "tools", "config_schema", "skill")


def node_profile_fields(node: WorkflowNode) -> dict[str, Any]:
    """The JSON-shaped profile fields of *node* (snapshot ``asdict`` form)."""
    raw = asdict(node)
    return {field: raw[field] for field in PROFILE_FIELDS}


def node_profile_hash(node: WorkflowNode) -> str:
    """Stable hash of the node's execution profile (:data:`PROFILE_FIELDS`)."""
    text = json.dumps(
        node_profile_fields(node), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def node_profile_pin(run_payload: Mapping[str, Any] | None, node_key: str) -> dict[str, Any] | None:
    """Read one node's frozen profile pin from a run's frozen pins."""
    if not isinstance(run_payload, Mapping):
        return None
    pins = run_payload.get(PIN_KEY)
    if not isinstance(pins, Mapping):
        return None
    pin = pins.get(node_key)
    return dict(pin) if isinstance(pin, Mapping) else None


def node_profile_pin_error(node: WorkflowNode, pin: Mapping[str, Any]) -> str | None:
    """Why *node* does not satisfy its frozen profile pin; None when it does."""
    if str(pin.get("node_key") or "") != node.key:
        return f"node profile pin targets {pin.get('node_key')!r} but the node is {node.key!r}"
    expected = str(pin.get("profile_hash") or "")
    if not expected or node_profile_hash(node) != expected:
        return f"node {node.key} execution profile does not match its replay pin (profile_hash)"
    return None
