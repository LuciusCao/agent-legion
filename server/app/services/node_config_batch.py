"""Intake batch payload readers for frozen node configs (spec D8).

Split from ``node_config`` for the file-size budget: these helpers decode
the per-node config snapshot frozen into the intake batch source payload.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def frozen_node_config(
    batch_payload: Mapping[str, Any] | None,
    node_key: str,
) -> dict[str, Any] | None:
    """Read one node's frozen config from an intake batch payload, if present."""
    if not isinstance(batch_payload, Mapping):
        return None
    node_config = batch_payload.get("node_config")
    if not isinstance(node_config, Mapping):
        return None
    values = node_config.get(node_key)
    return dict(values) if isinstance(values, Mapping) else None


def batch_source_payload(job_db: Any, job: Mapping[str, Any]) -> dict[str, Any] | None:
    """Decode the source payload of the job's intake batch, if available."""
    batch_id = job.get("batch_id")
    if not batch_id:
        return None
    batch = job_db.get_batch(str(batch_id))
    if not batch:
        return None
    try:
        payload = json.loads(str(batch.get("source_payload_json") or ""))
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
