"""Job-frozen node code pins (#109, split for the size budget): extraction
from the job's workflow snapshot and dispatch-time selection between the
snapshot pins and the intake batch pins.
"""

from __future__ import annotations

import json
from typing import Any, cast


def node_code_pins_from_job_snapshot(job: dict) -> dict[str, Any]:
    """The publish-time ``node_code_pins`` riding the job's workflow snapshot.

    Upgrade-aware counterpart of the intake batch's ``node_code_versions``:
    ``upgrade_workflow`` rewrites the snapshot on revision upgrades but never
    the batch payload. ``{}`` = no snapshot (legacy), no pins, or a corrupt
    payload (the corrupt case is already logged by
    ``definition_from_job_snapshot``); callers fall back to the batch pins.
    """
    raw = job.get("workflow_definition_snapshot_json") or ""
    if not raw:
        return {}
    try:
        pins = json.loads(str(raw)).get("node_code_pins")
    except Exception:
        return {}
    return dict(pins) if isinstance(pins, dict) else {}


def frozen_dispatch_pin(
    snapshot_pins: dict[str, Any] | None,
    batch_payload: dict[str, Any] | None,
    node_key: str,
) -> dict[str, Any] | None:
    """The frozen pin dispatch resolves: the job snapshot pins win; the
    intake batch payload's ``node_code_versions`` are the fallback for jobs
    whose snapshot carries no pins (legacy rows)."""
    pin = (snapshot_pins or {}).get(node_key)
    if pin is not None:
        return cast("dict[str, Any]", pin)
    batch_pins: dict[str, Any] = (batch_payload or {}).get("node_code_versions") or {}
    return cast("dict[str, Any] | None", batch_pins.get(node_key))
