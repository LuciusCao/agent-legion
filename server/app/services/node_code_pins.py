"""Job-frozen node code pins (#109, split for the size budget): extraction
from the job's workflow snapshot and dispatch-time pin selection. Since
#115 the freeze is replay-only — ordinary jobs dispatch the latest
published code; pins survive as audit records and the quality-replay pin
source (fail-closed on drift, EXEC-CODE-003).
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
    ``definition_from_job_snapshot``); non-object snapshot tops degrade the
    same way (codex on #264). Callers fall back to the batch pins.
    """
    raw = job.get("workflow_definition_snapshot_json") or ""
    if not raw:
        return {}
    try:
        snapshot = json.loads(str(raw))
        pins = snapshot.get("node_code_pins") if isinstance(snapshot, dict) else None
    except (TypeError, ValueError):
        # #204: the only declared failure is a corrupt snapshot payload —
        # json.JSONDecodeError is a ValueError and a non-str column renders
        # as TypeError. Both are the documented "corrupt case" the docstring
        # already routes to {} (definition_from_job_snapshot logs it);
        # anything else is a programming error worth a traceback.
        return {}
    return dict(pins) if isinstance(pins, dict) else {}


def frozen_dispatch_pin(
    snapshot_pins: dict[str, Any] | None,
    run_payload: dict[str, Any] | None,
    node_key: str,
) -> dict[str, Any] | None:
    """The frozen pin dispatch resolves — quality-replay runs only (#115).

    Ordinary jobs return None here so dispatch resolves the latest published
    code; only a run carrying the ``quality_replay`` marker pins, taking
    the job snapshot pins first and the run's frozen ``node_code_versions``
    as the fallback for jobs whose snapshot carries no pins (legacy rows).
    """
    if not isinstance(run_payload, dict) or not run_payload.get("quality_replay"):
        return None
    pin = (snapshot_pins or {}).get(node_key)
    if pin is not None:
        return cast("dict[str, Any]", pin)
    run_pins: dict[str, Any] = run_payload.get("node_code_versions") or {}
    return cast("dict[str, Any] | None", run_pins.get(node_key))
