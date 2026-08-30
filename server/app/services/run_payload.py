"""Run/job freeze helpers (schema v53, RUN-FREEZE-001).

The retired intake batch payload split into authoritative columns: frozen
pins (``node_code_versions`` / ``agent_versions`` / ``quality_replay``) live
on the run row, the frozen node config and the job's input live on the job
row. This module rebuilds the legacy payload-equivalent dict from those
columns so the dispatch chain (``dispatch_effective_config``,
``frozen_dispatch_pin``, ``agent_version_pin``) and the node SDK
(``ctx.batch_payload``) keep their contract without ever reading the old
storage shape again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

# candidate_input moved to the jobs domain (issue #195); re-exported for the
# historical import path.
from server.app.db.rowmap import parse_object
from server.app.jobs.run_freeze import candidate_input as candidate_input

_PIN_KEYS = ("node_code_versions", "agent_versions", "quality_replay")


def reconstruct_batch_payload(
    run: Mapping[str, Any] | None, job: Mapping[str, Any]
) -> dict[str, Any]:
    """Rebuild the legacy batch-payload-equivalent dict from run + job columns.

    Keys: the run's frozen pins, ``node_config`` (the job's whole frozen
    config), and ``task_candidates`` (the job's single input — one item one
    job). A missing run degrades to the job columns alone, mirroring the old
    unreadable-batch degradation.
    """
    payload: dict[str, Any] = {}
    if run is not None:
        pins = parse_object(run.get("frozen_pins_json"))
        for key in _PIN_KEYS:
            if key in pins:
                payload[key] = pins[key]
    payload["node_config"] = parse_object(job.get("frozen_config_json"))
    input_doc = parse_object(job.get("input_json"))
    payload["task_candidates"] = [input_doc] if input_doc else []
    return payload


def sdk_batch_row(run: Mapping[str, Any] | None, job: Mapping[str, Any]) -> dict[str, Any] | None:
    """The node-SDK-facing batch row: run columns plus a synthesized payload.

    The prefetched ``runtime["job_batch"]`` keeps its legacy shape (including
    ``source_payload_json``) so ``ctx.batch`` / ``ctx.batch_payload`` and the
    Worker code path stay wire-compatible; the payload content is rebuilt
    from the authoritative run/job columns, never from stored payload JSON.
    """
    if run is None:
        return None
    row = dict(run)
    row["source_payload_json"] = json.dumps(
        reconstruct_batch_payload(run, job), ensure_ascii=False, sort_keys=True
    )
    return row
